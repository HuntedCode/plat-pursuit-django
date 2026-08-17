"""
Weekly Digest Service: collects data for the "This Week in PlatPursuit" newsletter.

Community-focused email with site-wide stats, top platted games, review of the
week, and condensed personal stats. Follows the same static/classmethod pattern
as MonthlyRecapService. No persistent model: uses EmailLog for deduplication.
"""
import re
import logging
import pytz
from datetime import datetime, timedelta

from django.urls import reverse
from django.conf import settings
from django.db.models import Count, Q
from django.utils import timezone

from trophies.services.monthly_recap_service import MonthlyRecapService

logger = logging.getLogger(__name__)


class WeeklyDigestService:
    """
    Collects all data needed for the "This Week in PlatPursuit" newsletter.

    Community data (site-wide stats, top platted games, review of the week)
    is fetched once per batch and shared across all recipients. Personal data
    (trophy count, badges) is fetched per user.
    """

    @staticmethod
    def get_week_date_range(user_tz=None):
        """
        Get start/end datetimes for the previous ISO week in the user's timezone.

        Returns (week_start, week_end) as UTC-aware datetimes where:
        - week_start = previous Monday 00:00:00 local
        - week_end   = this Monday 00:00:00 local (exclusive upper bound)
        """
        tz = user_tz or pytz.UTC
        now_local = timezone.now().astimezone(tz)
        today = now_local.date()

        # This Monday (or today if Monday)
        this_monday = today - timedelta(days=today.weekday())
        prev_monday = this_monday - timedelta(days=7)

        start_naive = datetime(prev_monday.year, prev_monday.month, prev_monday.day)
        end_naive = datetime(this_monday.year, this_monday.month, this_monday.day)

        start_utc = tz.localize(start_naive).astimezone(pytz.UTC)
        end_utc = tz.localize(end_naive).astimezone(pytz.UTC)
        return start_utc, end_utc

    @classmethod
    def get_trophy_stats(cls, profile, week_start, week_end):
        """
        Aggregate trophy counts by type for the week.

        Returns: {total, bronze, silver, gold, platinum}
        """
        from trophies.models import EarnedTrophy

        counts = EarnedTrophy.objects.filter(
            profile=profile,
            earned=True,
            earned_date_time__gte=week_start,
            earned_date_time__lt=week_end,
        ).exclude(
            trophy__game__shovelware_status__in=['auto_flagged', 'manually_flagged'],
        ).aggregate(
            total=Count('id'),
            bronze=Count('id', filter=Q(trophy__trophy_type='bronze')),
            silver=Count('id', filter=Q(trophy__trophy_type='silver')),
            gold=Count('id', filter=Q(trophy__trophy_type='gold')),
            platinum=Count('id', filter=Q(trophy__trophy_type='platinum')),
        )

        return {
            'total': counts['total'] or 0,
            'bronze': counts['bronze'] or 0,
            'silver': counts['silver'] or 0,
            'gold': counts['gold'] or 0,
            'platinum': counts['platinum'] or 0,
        }


    @classmethod
    def get_badge_updates(cls, profile, week_start, week_end):
        """Group badges earned this week, plus the series they are closest to finishing.

        Repointed off the legacy tier engine in the 2026-08 cutover. Two shape changes reach the email:
        a badge's secondary label is now its EDITION ("Ultra HD"), because tiers no longer exist; and the
        closest-badge bar counts STAGES, not games, because that is the unit a group badge is earned in.

        Returns: {badges_earned: [...], closest_badge: {...} or None}
        """
        from trophies.models import UserGroupBadge
        from trophies.services.collection_service import closest_badge

        earned_this_week = (
            UserGroupBadge.objects.filter(
                profile=profile,
                group_badge__is_live=True,
                earned_at__gte=week_start,
                earned_at__lt=week_end,
            )
            .select_related('group_badge__series', 'group_badge__platform_group')
            .order_by('earned_at')
        )
        badges_earned = [
            {
                'name': ugb.group_badge.series.name,
                'edition': ugb.group_badge.platform_group.name,
            }
            for ugb in earned_this_week
        ]

        # `closest_badge` reads the materialized SeriesBadgeStanding the Collection CTA already uses, so the
        # email and the site name the same series rather than each running their own "nearest" heuristic.
        nearest = closest_badge(profile)
        closest = {
            'name': nearest['series_name'],
            'progress_pct': nearest['pct'],
            'completed': nearest['cleared'],
            'required': nearest['total'],
        } if nearest else None

        return {
            'badges_earned': badges_earned,
            'closest_badge': closest,
        }


    @classmethod
    def get_community_data(cls, week_start, week_end):
        """
        Community data for the newsletter. Called ONCE per batch, not per user.

        Returns: {
            top_review: {...} or None,
            site_stats: {total_trophies, total_platinums, total_reviews,
                         active_hunters, total_badges, new_signups},
            top_platted_games: [{game_name, game_image, game_slug, plat_count}, ...],
        }
        """
        from trophies.models import Concept, EarnedTrophy, Review, Profile

        # ── Site-wide trophy stats ──
        trophy_stats = EarnedTrophy.objects.filter(
            earned=True,
            earned_date_time__gte=week_start,
            earned_date_time__lt=week_end,
        ).exclude(
            trophy__game__shovelware_status__in=['auto_flagged', 'manually_flagged'],
        ).aggregate(
            total_trophies=Count('id'),
            total_platinums=Count('id', filter=Q(trophy__trophy_type='platinum')),
        )

        # ── Active hunters (distinct profiles with trophy activity) ──
        active_hunters = EarnedTrophy.objects.filter(
            earned=True,
            earned_date_time__gte=week_start,
            earned_date_time__lt=week_end,
        ).exclude(
            trophy__game__shovelware_status__in=['auto_flagged', 'manually_flagged'],
        ).values('profile_id').distinct().count()

        # ── Total reviews this week ──
        total_reviews = Review.objects.filter(
            is_deleted=False,
            created_at__gte=week_start,
            created_at__lt=week_end,
        ).count()

        # ── New signups (linked profiles) ──
        new_signups = Profile.objects.filter(
            is_linked=True,
            created_at__gte=week_start,
            created_at__lt=week_end,
        ).count()

        site_stats = {
            'total_trophies': trophy_stats['total_trophies'] or 0,
            'total_platinums': trophy_stats['total_platinums'] or 0,
            'total_reviews': total_reviews,
            'active_hunters': active_hunters,
            'new_signups': new_signups,
        }

        # ── Top 5 most-platted games this week ──
        top_platted_rows = list(
            EarnedTrophy.objects.filter(
                earned=True,
                trophy__trophy_type='platinum',
                earned_date_time__gte=week_start,
                earned_date_time__lt=week_end,
                trophy__game__concept__isnull=False,
            ).exclude(
                trophy__game__shovelware_status__in=['auto_flagged', 'manually_flagged'],
            ).values(
                'trophy__game__concept_id',
            ).annotate(
                plat_count=Count('id'),
            ).order_by('-plat_count')[:5]
        )

        # Look up the Concept instances separately so cover_url's IGDB-first
        # chain works for anchored concepts (empty concept_icon_url).
        concept_ids = [r['trophy__game__concept_id'] for r in top_platted_rows]
        concept_lookup = {
            c.id: c
            for c in Concept.objects
            .filter(id__in=concept_ids)
            .select_related('igdb_match')
            .defer('igdb_match__raw_response')
        }
        top_platted_games = []
        for row in top_platted_rows:
            c = concept_lookup.get(row['trophy__game__concept_id'])
            if not c:
                continue
            top_platted_games.append({
                'game_name': c.unified_title or 'Unknown Game',
                'game_image': c.cover_url or '',
                'game_slug': c.slug or '',
                'plat_count': row['plat_count'],
            })

        # ── Top review of the week by helpful count ──
        top_review_obj = Review.objects.filter(
            is_deleted=False,
            created_at__gte=week_start,
            created_at__lt=week_end,
        ).order_by('-helpful_count', '-created_at').select_related(
            'profile', 'concept', 'concept__igdb_match',
        ).first()

        top_review = None
        if top_review_obj:
            # Strip markdown formatting for clean snippet
            clean_body = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', top_review_obj.body)
            clean_body = re.sub(r'[*_`\[\]()#>~\-]', '', clean_body)
            snippet = clean_body[:150]
            # Break at last word boundary
            if len(clean_body) > 150:
                last_space = snippet.rfind(' ')
                if last_space > 100:
                    snippet = snippet[:last_space]

            top_review = {
                'author_username': (
                    top_review_obj.profile.display_psn_username
                    or top_review_obj.profile.psn_username
                ),
                'game_name': top_review_obj.concept.unified_title,
                'game_image': top_review_obj.concept.cover_url or '',
                'game_slug': top_review_obj.concept.slug,
                'body_snippet': snippet,
                'helpful_count': top_review_obj.helpful_count,
                'recommended': top_review_obj.recommended,
            }

        return {
            'top_review': top_review,
            'site_stats': site_stats,
            'top_platted_games': top_platted_games,
        }

    @classmethod
    def build_digest_data(cls, profile, week_start, week_end):
        """
        Collect personal digest data for a single profile.

        Returns a dict with trophy stats and badge updates.
        Intentionally lightweight: deep personal stats live in the monthly recap.
        """
        trophy_stats = cls.get_trophy_stats(profile, week_start, week_end)
        badge_updates = cls.get_badge_updates(profile, week_start, week_end)

        return {
            'trophy_stats': trophy_stats,
            'badge_updates': badge_updates,
        }

    @staticmethod
    def should_suppress(digest_data, community_data):
        """
        Determine if the digest should be suppressed.

        The newsletter is community-focused, so it has value even when the user
        had a quiet week. Only suppress if the community itself had essentially
        zero activity (e.g., site downtime).
        """
        site_stats = community_data.get('site_stats', {})
        community_has_content = (
            site_stats.get('total_trophies', 0) > 0
            or site_stats.get('total_reviews', 0) > 0
            or len(community_data.get('top_platted_games', [])) > 0
        )

        if community_has_content:
            return False  # Community content exists, always send

        # Community had nothing; fall back to personal check
        trophy_stats = digest_data['trophy_stats']
        badge_updates = digest_data['badge_updates']
        return (
            trophy_stats['total'] == 0
            and len(badge_updates['badges_earned']) == 0
            and badge_updates['closest_badge'] is None
        )

    @classmethod
    def build_email_context(cls, profile, digest_data, community_data):
        """
        Assemble the full template context dict for weekly_digest.html.

        Args:
            profile: Profile instance (with user)
            digest_data: Output of build_digest_data()
            community_data: Output of get_community_data() (shared)

        Returns:
            dict with all template context variables.
        """
        from users.services.email_preference_service import EmailPreferenceService

        user = profile.user
        user_tz = MonthlyRecapService._resolve_user_tz(profile)
        week_start, week_end = cls.get_week_date_range(user_tz)

        # Format week display dates in user's local timezone
        start_local = week_start.astimezone(user_tz)
        # End is exclusive Monday, so display Sunday = end - 1 day
        end_local = (week_end - timedelta(seconds=1)).astimezone(user_tz)
        week_start_display = f"{start_local.strftime('%b')} {start_local.day}"
        week_end_display = f"{end_local.strftime('%b')} {end_local.day}"

        trophy_stats = digest_data['trophy_stats']
        badge_updates = digest_data['badge_updates']
        site_stats = community_data.get('site_stats', {})
        top_review = community_data.get('top_review')

        # Personal contribution percentage
        community_total = site_stats.get('total_trophies', 0)
        user_total = trophy_stats['total']

        contribution_pct = None
        if community_total > 0 and user_total > 0:
            pct = round((user_total / community_total) * 100, 1)
            if pct < 0.1:
                contribution_pct = '<0.1'
            else:
                contribution_pct = str(pct)

        # Build preference URL
        try:
            token = EmailPreferenceService.generate_preference_token(user.id)
            preference_url = f"{settings.SITE_URL}/users/email-preferences/?token={token}"
        except Exception:
            preference_url = f"{settings.SITE_URL}/users/email-preferences/"

        # Build review URL if top review exists
        review_url = ''
        if top_review and top_review.get('game_slug'):
            review_url = f"{settings.SITE_URL}/reviews/{top_review['game_slug']}/"

        # Condensed personal section
        your_week = {
            'total_trophies': user_total,
            'has_activity': user_total > 0,
            'contribution_pct': contribution_pct,
            'platinums_count': trophy_stats['platinum'],
            'badges_earned': badge_updates['badges_earned'],
            'has_badges_earned': len(badge_updates['badges_earned']) > 0,
            'closest_badge': badge_updates['closest_badge'],
            'has_closest_badge': badge_updates['closest_badge'] is not None,
        }

        return {
            # Identity
            'username': profile.display_psn_username or profile.psn_username,
            'week_start_display': week_start_display,
            'week_end_display': week_end_display,
            # Community
            'site_stats': site_stats,
            'top_platted_games': community_data.get('top_platted_games', []),
            'has_top_platted_games': len(community_data.get('top_platted_games', [])) > 0,
            'top_review': top_review,
            'has_top_review': top_review is not None,
            'review_url': review_url,
            # Personal
            'your_week': your_week,
            # Links
            # reverse(), not a hardcoded path: this link goes out in EMAIL and outlives any redirect we
            # keep. The section moved twice in 2026-08 and a literal here followed neither.
            'profile_url': f"{settings.SITE_URL}{reverse('profile_detail', args=[profile.psn_username])}",
            'reviews_url': f"{settings.SITE_URL}/reviews/",
            'site_url': settings.SITE_URL,
            'preference_url': preference_url,
        }
