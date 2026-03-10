"""
Weekly Digest Service: collects personalized data for the weekly digest email.

Follows the same static/classmethod pattern as MonthlyRecapService.
No persistent model: uses EmailLog for deduplication.
"""
import re
import logging
import pytz
from datetime import datetime, timedelta

from django.conf import settings
from django.db.models import Count, Q
from django.db.models.functions import TruncDate
from django.utils import timezone

from trophies.services.monthly_recap_service import MonthlyRecapService

logger = logging.getLogger(__name__)


class WeeklyDigestService:
    """
    Collects all data needed for a user's weekly digest email.

    Data sources: EarnedTrophy, ProfileGame, Challenge (+ slot tables),
    UserBadge, UserBadgeProgress, Review. Community spotlight is fetched
    once per batch and shared across all recipients.
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
    def get_platinums_earned(cls, profile, week_start, week_end, user_tz=None):
        """
        Detailed data for platinums earned this week.

        Returns: list of {game_name, game_image, earned_date} (max 10).
        """
        from trophies.models import EarnedTrophy

        tz = user_tz or pytz.UTC

        platinums = EarnedTrophy.objects.filter(
            profile=profile,
            earned=True,
            trophy__trophy_type='platinum',
            earned_date_time__gte=week_start,
            earned_date_time__lt=week_end,
        ).select_related('trophy', 'trophy__game').order_by('earned_date_time')[:10]

        result = []
        for earned in platinums:
            game = earned.trophy.game
            local_dt = earned.earned_date_time.astimezone(tz) if earned.earned_date_time else None
            result.append({
                'game_name': game.title_name,
                'game_image': game.title_image or game.title_icon_url or '',
                'earned_date': local_dt.strftime('%b %d') if local_dt else '',
            })
        return result

    @classmethod
    def get_games_started(cls, profile, week_start, week_end):
        """Count of games first played this week."""
        from trophies.models import ProfileGame

        return ProfileGame.objects.filter(
            profile=profile,
            first_played_date_time__gte=week_start,
            first_played_date_time__lt=week_end,
        ).count()

    @classmethod
    def get_active_days(cls, profile, week_start, week_end):
        """Count of distinct days with at least one trophy earned."""
        from trophies.models import EarnedTrophy

        return EarnedTrophy.objects.filter(
            profile=profile,
            earned=True,
            earned_date_time__gte=week_start,
            earned_date_time__lt=week_end,
        ).annotate(
            day=TruncDate('earned_date_time')
        ).values('day').distinct().count()

    @classmethod
    def get_challenge_progress(cls, profile, week_start, week_end):
        """
        Active challenge data with weekly deltas.

        Returns list of dicts: {challenge_type, name, completed_count,
        total_items, progress_percentage, weekly_delta}
        """
        from trophies.models import (
            Challenge, AZChallengeSlot, CalendarChallengeDay, GenreChallengeSlot,
        )

        challenges = Challenge.objects.filter(
            profile=profile,
            is_deleted=False,
            is_complete=False,
        ).order_by('-updated_at')

        result = []
        for challenge in challenges:
            # Weekly delta varies by type
            weekly_delta = 0
            if challenge.challenge_type == 'az':
                weekly_delta = AZChallengeSlot.objects.filter(
                    challenge=challenge,
                    is_completed=True,
                    completed_at__gte=week_start,
                    completed_at__lt=week_end,
                ).count()
            elif challenge.challenge_type == 'calendar':
                weekly_delta = CalendarChallengeDay.objects.filter(
                    challenge=challenge,
                    is_filled=True,
                    filled_at__gte=week_start,
                    filled_at__lt=week_end,
                ).count()
            elif challenge.challenge_type == 'genre':
                weekly_delta = GenreChallengeSlot.objects.filter(
                    challenge=challenge,
                    is_completed=True,
                    completed_at__gte=week_start,
                    completed_at__lt=week_end,
                ).count()

            result.append({
                'challenge_type': challenge.get_challenge_type_display(),
                'name': challenge.name,
                'completed_count': challenge.completed_count,
                'total_items': challenge.total_items,
                'progress_percentage': challenge.progress_percentage,
                'weekly_delta': weekly_delta,
            })

        return result

    @classmethod
    def get_badge_updates(cls, profile, week_start, week_end):
        """
        Badges earned this week and the closest badge to earning next.

        Returns: {badges_earned: [...], closest_badge: {...} or None}
        """
        from trophies.models import Badge, UserBadge, UserBadgeProgress

        # Badges earned this week
        earned_this_week = UserBadge.objects.filter(
            profile=profile,
            earned_at__gte=week_start,
            earned_at__lt=week_end,
        ).select_related('badge')

        badges_earned = []
        for ub in earned_this_week:
            badge = ub.badge
            try:
                image_url = badge.get_badge_layers().get('main', '')
            except Exception:
                image_url = ''
            tier_map = {1: 'Bronze', 2: 'Silver', 3: 'Gold', 4: 'Platinum'}
            badges_earned.append({
                'name': badge.effective_display_series or badge.name,
                'tier_name': tier_map.get(badge.tier, 'Bronze'),
                'image_url': image_url,
            })

        # Closest badge to earning (tier 1, not yet earned, has progress)
        all_earned_ids = UserBadge.objects.filter(
            profile=profile,
        ).values_list('badge_id', flat=True)

        progress_records = UserBadgeProgress.objects.filter(
            profile=profile,
            badge__tier=1,
            badge__is_live=True,
            completed_concepts__gt=0,
        ).exclude(
            badge_id__in=all_earned_ids,
        ).select_related('badge').order_by('-completed_concepts')

        closest_badge = None
        for prog in progress_records:
            badge = prog.badge
            required = badge.required_stages if badge.required_stages > 0 else 1
            progress_pct = min(100, int((prog.completed_concepts / required) * 100))
            if progress_pct >= 1:
                try:
                    image_url = badge.get_badge_layers().get('main', '')
                except Exception:
                    image_url = ''
                closest_badge = {
                    'name': badge.effective_display_series or badge.name,
                    'progress_pct': progress_pct,
                    'completed': prog.completed_concepts,
                    'required': required,
                    'image_url': image_url,
                }
                break  # Only need the closest one

        return {
            'badges_earned': badges_earned,
            'closest_badge': closest_badge,
        }

    @classmethod
    def get_community_spotlight(cls, week_start, week_end):
        """
        Community data for the digest. Called ONCE per batch, not per user.

        Returns: {top_review: {...} or None, site_stats: {...}}
        """
        from trophies.models import EarnedTrophy, Review

        # Top review of the week by helpful count
        top_review_obj = Review.objects.filter(
            is_deleted=False,
            created_at__gte=week_start,
            created_at__lt=week_end,
        ).order_by('-helpful_count', '-created_at').select_related(
            'profile', 'concept',
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
                'game_image': top_review_obj.concept.concept_icon_url or '',
                'game_slug': top_review_obj.concept.slug,
                'body_snippet': snippet,
                'helpful_count': top_review_obj.helpful_count,
                'recommended': top_review_obj.recommended,
            }

        # Site-wide stats for the week
        trophy_stats = EarnedTrophy.objects.filter(
            earned=True,
            earned_date_time__gte=week_start,
            earned_date_time__lt=week_end,
        ).aggregate(
            total_trophies=Count('id'),
            total_platinums=Count('id', filter=Q(trophy__trophy_type='platinum')),
        )

        total_reviews = Review.objects.filter(
            is_deleted=False,
            created_at__gte=week_start,
            created_at__lt=week_end,
        ).count()

        site_stats = {
            'total_trophies': trophy_stats['total_trophies'] or 0,
            'total_platinums': trophy_stats['total_platinums'] or 0,
            'total_reviews': total_reviews,
        }

        return {
            'top_review': top_review,
            'site_stats': site_stats,
        }

    @classmethod
    def build_digest_data(cls, profile, week_start, week_end):
        """
        Collect all digest data for a single profile.

        Returns a dict with all personal stats for the week.
        """
        user_tz = MonthlyRecapService._resolve_user_tz(profile)

        trophy_stats = cls.get_trophy_stats(profile, week_start, week_end)
        platinums = cls.get_platinums_earned(profile, week_start, week_end, user_tz)
        games_started = cls.get_games_started(profile, week_start, week_end)
        active_days = cls.get_active_days(profile, week_start, week_end)
        challenges = cls.get_challenge_progress(profile, week_start, week_end)
        badge_updates = cls.get_badge_updates(profile, week_start, week_end)

        return {
            'trophy_stats': trophy_stats,
            'platinums': platinums,
            'games_started': games_started,
            'active_days': active_days,
            'challenges': challenges,
            'badge_updates': badge_updates,
        }

    @staticmethod
    def should_suppress(digest_data):
        """
        Determine if the digest should be suppressed (nothing to show).

        Suppresses when ALL are true:
        - Zero trophies earned
        - No active challenges
        - No badges earned this week
        - No closest badge to show
        """
        trophy_stats = digest_data['trophy_stats']
        badge_updates = digest_data['badge_updates']

        return (
            trophy_stats['total'] == 0
            and len(digest_data['challenges']) == 0
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
            community_data: Output of get_community_spotlight() (shared)

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
        top_review = community_data.get('top_review')

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

        return {
            'username': profile.display_psn_username or profile.psn_username,
            'week_start_display': week_start_display,
            'week_end_display': week_end_display,
            # Trophy stats
            'total_trophies': trophy_stats['total'],
            'bronze_count': trophy_stats['bronze'],
            'silver_count': trophy_stats['silver'],
            'gold_count': trophy_stats['gold'],
            'platinum_count': trophy_stats['platinum'],
            'active_days': digest_data['active_days'],
            'games_started': digest_data['games_started'],
            # Platinums
            'platinums_earned': digest_data['platinums'],
            'has_platinums': len(digest_data['platinums']) > 0,
            # Challenges
            'challenges': digest_data['challenges'],
            'has_challenges': len(digest_data['challenges']) > 0,
            # Badges
            'badges_earned': badge_updates['badges_earned'],
            'has_badges_earned': len(badge_updates['badges_earned']) > 0,
            'closest_badge': badge_updates['closest_badge'],
            'has_closest_badge': badge_updates['closest_badge'] is not None,
            # Community
            'top_review': top_review,
            'has_top_review': top_review is not None,
            'review_url': review_url,
            'site_stats': community_data.get('site_stats', {}),
            # Links
            'profile_url': f"{settings.SITE_URL}/profiles/{profile.psn_username}/",
            'site_url': settings.SITE_URL,
            'preference_url': preference_url,
        }
