"""
Monthly Recap Service - Handles generation and caching of monthly recap data.

This service manages the creation of "Spotify Wrapped" style monthly recaps:
- Calculates trophy/game statistics for a given month
- Caches results in MonthlyRecap model for performance
- Handles finalization of past month recaps
- Provides comparison data (vs previous month, personal bests)
"""
import calendar
import logging
import pytz
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Optional
from django.db import transaction
from django.db.models import Count, Min, Q, F
from django.db.models.functions import TruncDate, TruncMonth
from django.templatetags.static import static
from django.utils import timezone

logger = logging.getLogger(__name__)


class MonthlyRecapService:
    """
    Service for generating and managing monthly recap data.

    Follows existing service patterns (ShareableDataService, profile_stats_service).
    All methods are static/classmethod for consistency with other services.
    """

    # Cache staleness threshold (1 hour) for current month recaps
    CACHE_STALENESS_SECONDS = 3600

    @staticmethod
    def _resolve_user_tz(profile):
        """
        Resolve a pytz timezone object from a profile's linked user.

        Falls back to UTC if:
        - Profile has no linked user
        - User has no timezone set
        - Timezone string is invalid

        Returns:
            pytz timezone object
        """
        try:
            tz_name = profile.user.user_timezone if profile.user else 'UTC'
            return pytz.timezone(tz_name or 'UTC')
        except (pytz.exceptions.UnknownTimeZoneError, AttributeError):
            return pytz.UTC

    @staticmethod
    def get_month_date_range(year, month, user_tz=None):
        """
        Get the start and end datetime for a given month in the user's timezone.

        Args:
            year: Year (e.g., 2026)
            month: Month (1-12)
            user_tz: pytz timezone object (defaults to UTC)

        Returns:
            tuple: (start_datetime, end_datetime) where end is first of next month,
                   both aware datetimes in UTC (converted from user's local midnight)
        """
        if user_tz is None:
            user_tz = pytz.UTC

        # Create midnight boundaries in the user's local timezone
        start_naive = datetime(year, month, 1)
        if month == 12:
            end_naive = datetime(year + 1, 1, 1)
        else:
            end_naive = datetime(year, month + 1, 1)

        # Localize to user's timezone, then convert to UTC for DB queries
        start_date = user_tz.localize(start_naive).astimezone(pytz.UTC)
        end_date = user_tz.localize(end_naive).astimezone(pytz.UTC)

        return start_date, end_date

    @classmethod
    def get_or_generate_recap(cls, profile, year, month, force_regenerate=False):
        """
        Get existing recap or generate a new one.

        For past months: Returns cached data (immutable once finalized).
        For current month: Regenerates if stale (>1 hour) or if force_regenerate=True.

        Args:
            profile: Profile instance
            year: Year
            month: Month (1-12)
            force_regenerate: Force recalculation even if cached

        Returns:
            MonthlyRecap instance or None if no activity
        """
        from trophies.models import MonthlyRecap

        user_tz = cls._resolve_user_tz(profile)

        # Check "current month" in the USER'S local time
        now_utc = timezone.now()
        now_local = now_utc.astimezone(user_tz)
        is_current_month = (year == now_local.year and month == now_local.month)

        try:
            recap = MonthlyRecap.objects.get(profile=profile, year=year, month=month)

            # If finalized, always return cached data
            if recap.is_finalized:
                return recap

            # For current month, check staleness
            if is_current_month:
                time_since_update = (now_utc - recap.updated_at).total_seconds()
                is_stale = time_since_update > cls.CACHE_STALENESS_SECONDS

                if not is_stale and not force_regenerate:
                    return recap
            else:
                # Past month but not finalized - regenerate once then finalize
                pass

        except MonthlyRecap.DoesNotExist:
            recap = None

        # Check if there's any activity for this month
        trophy_count = cls.get_trophy_count_for_month(profile, year, month, user_tz=user_tz)
        if trophy_count == 0:
            # No activity - don't create a recap
            if recap:
                recap.delete()
            return None

        # Generate fresh data
        data = cls.generate_recap_data(profile, year, month, user_tz=user_tz)

        recap, _ = MonthlyRecap.objects.update_or_create(
            profile=profile,
            year=year,
            month=month,
            defaults=data
        )

        # If this is a past month, finalize it
        if not is_current_month:
            recap.is_finalized = True
            recap.save(update_fields=['is_finalized'])

        return recap

    @classmethod
    def generate_recap_data(cls, profile, year, month, user_tz=None):
        """
        Calculate all monthly stats from EarnedTrophy/ProfileGame tables.

        Args:
            profile: Profile instance
            year: Year
            month: Month (1-12)
            user_tz: pytz timezone object (defaults to UTC)

        Returns:
            dict: Data suitable for MonthlyRecap model fields
        """
        # Get trophy breakdown
        trophy_counts = cls.get_trophy_counts_for_month(profile, year, month, user_tz=user_tz)

        # Get game stats
        games_started = cls.get_games_started_in_month(profile, year, month, user_tz=user_tz)
        games_completed = cls.get_games_completed_in_month(profile, year, month, user_tz=user_tz)

        # Get highlight data
        platinums_data = cls.get_platinums_data_for_month(profile, year, month, user_tz=user_tz)
        rarest_trophy = cls.get_rarest_trophy_in_month(profile, year, month, user_tz=user_tz)
        most_active = cls.get_most_active_day(profile, year, month, user_tz=user_tz)
        activity_calendar = cls.get_daily_activity_calendar(profile, year, month, user_tz=user_tz)

        # Get badge stats
        badge_stats = cls.get_badge_stats_for_month(profile, year, month, user_tz=user_tz)

        # Get badge progress quiz data (snapshot at time of generation)
        badge_progress_quiz = cls.get_badge_progress_quiz_snapshot(profile, year, month, user_tz=user_tz)

        # Get streak and time analysis data
        streak_data = cls.get_streak_data(profile, year, month, user_tz=user_tz)
        time_analysis = cls.get_time_of_day_analysis(profile, year, month, user_tz=user_tz)

        # Get quiz data (snapshots for historical accuracy)
        quiz_total_trophies = cls.get_quiz_total_trophies_options(profile, year, month, user_tz=user_tz)
        quiz_rarest_trophy = cls.get_quiz_rarest_trophy_options(profile, year, month, user_tz=user_tz)
        quiz_active_day = cls.get_quiz_active_day_options(profile, year, month, user_tz=user_tz)

        # Get comparison data
        comparison = cls.get_comparison_data(profile, year, month, user_tz=user_tz)

        # Context beats: what they played, how they stack up, and this month across their other years.
        taste = cls.get_taste_for_month(profile, year, month, user_tz=user_tz)
        community = cls.get_community_comparison_for_month(profile, year, month, user_tz=user_tz)
        history = cls.get_month_in_history(profile, year, month, user_tz=user_tz)

        return {
            'total_trophies_earned': trophy_counts['total'],
            'bronzes_earned': trophy_counts['bronze'],
            'silvers_earned': trophy_counts['silver'],
            'golds_earned': trophy_counts['gold'],
            'platinums_earned': trophy_counts['platinum'],
            'games_started': games_started,
            'games_completed': games_completed,
            'platinums_data': platinums_data,
            'rarest_trophy_data': rarest_trophy or {},
            'most_active_day': most_active or {},
            'activity_calendar': activity_calendar or {},
            'streak_data': streak_data or {},
            'time_analysis_data': time_analysis or {},
            'quiz_total_trophies_data': quiz_total_trophies or {},
            'quiz_rarest_trophy_data': quiz_rarest_trophy or {},
            'quiz_active_day_data': quiz_active_day or {},
            'badge_xp_earned': badge_stats['xp_earned'],
            'badges_earned_count': badge_stats['badges_count'],
            'taste_data': taste or {},
            'community_comparison_data': community or {},
            'month_in_history_data': history or {},
            'badges_data': badge_stats['badges_data'],
            'badge_progress_quiz_data': badge_progress_quiz or {},
            'comparison_data': comparison,
        }

    @classmethod
    def get_trophy_count_for_month(cls, profile, year, month, user_tz=None):
        """Get total trophy count for a month (used for activity check)."""
        from trophies.models import EarnedTrophy

        start_date, end_date = cls.get_month_date_range(year, month, user_tz)

        return EarnedTrophy.objects.filter(
            profile=profile,
            earned=True,
            earned_date_time__gte=start_date,
            earned_date_time__lt=end_date
        ).count()

    @classmethod
    def get_trophy_counts_for_month(cls, profile, year, month, user_tz=None):
        """
        Get trophy counts by type for a month.

        Returns:
            dict: {total, bronze, silver, gold, platinum}
        """
        from trophies.models import EarnedTrophy

        start_date, end_date = cls.get_month_date_range(year, month, user_tz)

        counts = EarnedTrophy.objects.filter(
            profile=profile,
            earned=True,
            earned_date_time__gte=start_date,
            earned_date_time__lt=end_date
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
    def get_games_started_in_month(cls, profile, year, month, user_tz=None):
        """Get count of games first played in this month."""
        from trophies.models import ProfileGame

        start_date, end_date = cls.get_month_date_range(year, month, user_tz)

        return ProfileGame.objects.filter(
            profile=profile,
            first_played_date_time__gte=start_date,
            first_played_date_time__lt=end_date
        ).count()

    @classmethod
    def get_games_completed_in_month(cls, profile, year, month, user_tz=None):
        """
        Get count of games completed (100%) in this month.

        A game is considered completed in a month if a platinum trophy
        was earned for it in that month.
        """
        from trophies.models import EarnedTrophy

        start_date, end_date = cls.get_month_date_range(year, month, user_tz)

        # Count distinct games where platinum was earned this month
        return EarnedTrophy.objects.filter(
            profile=profile,
            earned=True,
            trophy__trophy_type='platinum',
            earned_date_time__gte=start_date,
            earned_date_time__lt=end_date
        ).values('trophy__game').distinct().count()

    @classmethod
    def get_platinums_data_for_month(cls, profile, year, month, user_tz=None):
        """
        Get detailed data for all platinums earned in the month.

        Returns:
            list: [{game_name, game_image, earned_date, earn_rate}, ...]
        """
        from trophies.models import EarnedTrophy

        tz = user_tz or pytz.UTC
        start_date, end_date = cls.get_month_date_range(year, month, user_tz)

        platinums = EarnedTrophy.objects.filter(
            profile=profile,
            earned=True,
            trophy__trophy_type='platinum',
            earned_date_time__gte=start_date,
            earned_date_time__lt=end_date
        ).select_related(
            'trophy',
            'trophy__game',
            'trophy__game__concept',
            'trophy__game__concept__igdb_match',
        ).order_by('earned_date_time')

        result = []
        for earned in platinums:
            game = earned.trophy.game
            # Convert to user's local timezone before formatting
            local_dt = earned.earned_date_time.astimezone(tz) if earned.earned_date_time else None
            result.append({
                'game_name': game.title_name,
                'game_image': game.display_image_url,
                'earned_date': local_dt.strftime('%b %d') if local_dt else '',
                'earn_rate': earned.trophy.trophy_earn_rate or 0,
            })

        return result

    @classmethod
    def get_rarest_trophy_in_month(cls, profile, year, month, user_tz=None):
        """
        Find the rarest trophy (lowest earn_rate) earned in the month.

        Returns:
            dict or None: {name, game, earn_rate, icon_url, trophy_type, rarity_label}
        """
        from trophies.models import EarnedTrophy

        start_date, end_date = cls.get_month_date_range(year, month, user_tz)

        # Get trophy with lowest earn_rate (most rare)
        # Filter out trophies with 0 earn_rate as those may be invalid
        rarest = EarnedTrophy.objects.filter(
            profile=profile,
            earned=True,
            earned_date_time__gte=start_date,
            earned_date_time__lt=end_date,
            trophy__trophy_earn_rate__gt=0
        ).select_related('trophy', 'trophy__game').order_by(
            'trophy__trophy_earn_rate'
        ).first()

        if not rarest:
            return None

        trophy = rarest.trophy
        # PSN's OWN rarity band, not the site's rarity scale. `earn_rate` here is
        # Trophy.trophy_earn_rate -- the share of players who own the game and earned this -- whereas
        # `--pp-rarity-*` / data-rarity grades against the whole PlatPursuit community. Different
        # populations, so grading a PSN earn rate on the site's thresholds would be a category error.
        # The plat card labels its platinum from this same map; reused so the two agree.
        from core.services.completion_card_service import RARITY_LABELS
        return {
            'name': trophy.trophy_name,
            'game': trophy.game.title_name,
            'earn_rate': trophy.trophy_earn_rate,
            'icon_url': trophy.trophy_icon_url or '',
            'trophy_type': trophy.trophy_type,
            'rarity_label': RARITY_LABELS.get(trophy.trophy_rarity, ''),
        }

    @classmethod
    def _get_daily_trophy_counts(cls, profile, year, month, user_tz=None):
        """
        Shared helper to get trophy counts by day for a month.

        Used by get_most_active_day() and get_daily_activity_calendar().

        Returns:
            QuerySet: [{day: date, count: int}, ...]
        """
        from trophies.models import EarnedTrophy

        tz = user_tz or pytz.UTC
        start_date, end_date = cls.get_month_date_range(year, month, user_tz)

        return EarnedTrophy.objects.filter(
            profile=profile,
            earned=True,
            earned_date_time__gte=start_date,
            earned_date_time__lt=end_date
        ).annotate(
            day=TruncDate('earned_date_time', tzinfo=tz)
        ).values('day').annotate(
            count=Count('id')
        )

    @classmethod
    def get_most_active_day(cls, profile, year, month, user_tz=None):
        """
        Find the day with most trophies earned in the month.

        Returns:
            dict or None: {date, day_name, trophy_count}
        """
        daily_counts = cls._get_daily_trophy_counts(profile, year, month, user_tz=user_tz).order_by('-count').first()

        if not daily_counts or not daily_counts['day']:
            return None

        day = daily_counts['day']
        return {
            'date': day.strftime('%B %d'),  # e.g., "January 15"
            'day_name': day.strftime('%A'),  # e.g., "Wednesday"
            'trophy_count': daily_counts['count'],
        }

    @classmethod
    def get_daily_activity_calendar(cls, profile, year, month, user_tz=None):
        """
        Get daily trophy counts for the entire month in calendar format.

        Returns:
            dict: {
                'days': [{
                    day: 1,
                    count: 5,
                    platinum_count: 1,
                    platinums: [{game_name, trophy_name, icon_url}, ...],
                    level: 3
                }, ...],
                'max_count': int,
                'total_active_days': int,
                'first_day_weekday': int (0=Sunday, 6=Saturday),
                'days_in_month': int
            }
        """
        from trophies.models import EarnedTrophy

        tz = user_tz or pytz.UTC
        daily_counts = cls._get_daily_trophy_counts(profile, year, month, user_tz=user_tz)

        # Convert to dict for easy lookup
        counts_by_day = {item['day'].day: item['count'] for item in daily_counts}

        # Get platinum trophy details by day
        start_date, end_date = cls.get_month_date_range(year, month, user_tz)
        platinum_trophies = EarnedTrophy.objects.filter(
            profile=profile,
            earned=True,
            earned_date_time__gte=start_date,
            earned_date_time__lt=end_date,
            trophy__trophy_type='platinum'
        ).select_related(
            'trophy',
            'trophy__game',
            'trophy__game__concept',
            'trophy__game__concept__igdb_match',
        ).order_by('earned_date_time')

        # Group platinums by day with details
        platinums_by_day = {}
        for et in platinum_trophies:
            # Convert to user's local timezone to get the correct day
            local_dt = et.earned_date_time.astimezone(tz)
            day = local_dt.day
            if day not in platinums_by_day:
                platinums_by_day[day] = []

            platinums_by_day[day].append({
                'game_name': et.trophy.game.title_name,
                'trophy_name': et.trophy.trophy_name,
                'icon_url': et.trophy.trophy_icon_url or '',
            })

        # Calculate calendar metadata
        days_in_month = calendar.monthrange(year, month)[1]
        # monthrange returns weekday of first day (0=Monday, 6=Sunday)
        # Convert to Sunday=0 format for display
        first_day_weekday_monday = calendar.monthrange(year, month)[0]
        first_day_weekday = (first_day_weekday_monday + 1) % 7

        max_count = max(counts_by_day.values()) if counts_by_day else 0

        # Build day data with activity levels
        days = []
        for day in range(1, days_in_month + 1):
            count = counts_by_day.get(day, 0)

            # Calculate activity level (0-4)
            if count == 0:
                level = 0
            elif max_count > 0:
                percentage = count / max_count
                if percentage <= 0.25:
                    level = 1
                elif percentage <= 0.50:
                    level = 2
                elif percentage <= 0.75:
                    level = 3
                else:
                    level = 4
            else:
                level = 0

            day_platinums = platinums_by_day.get(day, [])
            days.append({
                'day': day,
                'count': count,
                'platinum_count': len(day_platinums),
                'platinums': day_platinums,  # List of platinum details
                'level': level,
            })

        return {
            'days': days,
            'max_count': max_count,
            'total_active_days': len(counts_by_day),
            'first_day_weekday': first_day_weekday,
            'days_in_month': days_in_month,
        }

    @classmethod
    def get_badge_stats_for_month(cls, profile, year, month, user_tz=None):
        """Badges earned in the month, as objects ready for the Medallion.

        Reads UserGroupBadge -- the badge system. The version this replaced read the legacy `UserBadge`
        table, which nothing writes any more: the only writer is `badge_service`, which no live path
        calls (evaluation runs through `badge_apply` from the `evaluate_badges` command). So the slide
        was showing an empty or frozen set for everybody.

        XP comes from the engine's dates, not from a ledger -- there is no badge-XP ledger and none is
        needed. Every cleared gating stage carries the date it fell and every earned badge carries its
        earn date, so `badge_xp.monthly_xp` buckets the same two components the standings SUM. This runs
        one evaluation: ~6 catalog queries (profile-independent) plus the two bounded, whale-safe
        completion reads. It happens once per (profile, month) at GENERATION time, not per page view --
        `get_or_generate_recap` persists the result -- so the deck's 8-16 concurrent slide requests read
        the stored number.

        `art_layers` are absolute static URLs and are SNAPSHOTTED onto the recap. Safe because the project
        serves static through whitenoise's CompressedStaticFilesStorage, which does not hash filenames.

        Returns:
            dict: {xp_earned, badges_count, badges_data}
        """
        from trophies.models import UserGroupBadge
        from trophies.services.badge_detail_service import group_medallion_layers
        from trophies.services.badge_orchestrator import evaluate_profile
        from trophies.services.badge_xp import monthly_xp

        user_tz = user_tz or cls._resolve_user_tz(profile)
        start_date, end_date = cls.get_month_date_range(year, month, user_tz)

        earned = (
            UserGroupBadge.objects
            .filter(profile=profile, earned_at__gte=start_date, earned_at__lt=end_date)
            .select_related('group_badge__series', 'group_badge__platform_group')
            .order_by('earned_at')
        )

        badges_data = []
        for ugb in earned:
            gb = ugb.group_badge
            try:
                tier, layers, is_avatar = group_medallion_layers(gb)
            except Exception:
                logger.exception("Could not resolve medallion art for group badge %s", gb.id)
                continue

            # The frame dict components/badge_medallion.html reads. An earned badge needs no progress
            # meter, so the stage counts are deliberately absent rather than zeroed.
            badges_data.append({
                'tier': tier,
                'state': 'earned',
                'art_layers': layers,
                'is_avatar': is_avatar,
                'is_holographic': ugb.is_holo,
                'series_name': gb.series.name,
                'badge_name': gb.platform_group.name,      # the edition: "Legacy HD" / "Ultra HD"
                'series_slug': gb.series.series_slug,
                'set_number': gb.set_number,
            })

        # One evaluation yields EVERY month's buckets; we keep the one being generated.
        try:
            xp_by_month = monthly_xp(evaluate_profile(profile).values(), user_tz)
        except Exception:
            logger.exception("Badge XP evaluation failed for profile %s; recording 0 for %s/%s",
                             profile.id, year, month)
            xp_by_month = {}

        return {
            'xp_earned': xp_by_month.get((year, month), 0),
            'badges_count': len(badges_data),
            'badges_data': badges_data,
        }

    @classmethod
    def get_badge_progress_quiz_snapshot(cls, profile, year, month, user_tz=None):
        """"Which badge are you closest to earning?" -- read from SeriesBadgeStanding.

        Reads the badge subsystem's materialized per-series standing. The version this replaced read
        `UserBadgeProgress` filtered to `badge__tier=1`, both of which belong to the legacy tier-based
        system that nothing writes any more.

        MOST RECENT COMPLETED MONTH ONLY. Returns None for anything older, which drops the slide (the
        frontend already skips quiz slides with no data). SeriesBadgeStanding is LIVE state, recomputed
        from scratch on every evaluation -- there is no history in it and no way to reconstruct "where you
        stood in March 2019". Now that every month is openable, generating an old recap would freeze
        TODAY'S progress into it and label it with that month, permanently, because the snapshot is
        persisted. A quiz that lies about the past is worse than one slide fewer.

        Progress is in basis points (0-10000): 10000 means the best edition is fully cleared, i.e. earned,
        so those are excluded rather than filtered against a separate earned-badge list.

        Returns:
            dict or None: {correct_badge_id, correct_badge_name, correct_progress_pct,
                           correct_completed, correct_required, options: [...]}
        """
        import random
        from trophies.models import GroupBadge, SeriesBadgeStanding
        from trophies.services.badge_detail_service import group_medallion_layers

        user_tz = user_tz or cls._resolve_user_tz(profile)
        now_local = timezone.now().astimezone(user_tz)
        prev_year, prev_month = (now_local.year, now_local.month - 1) if now_local.month > 1             else (now_local.year - 1, 12)
        if (year, month) != (prev_year, prev_month):
            return None

        standings = list(
            SeriesBadgeStanding.objects
            .filter(profile=profile, progress_bp__gt=0, progress_bp__lt=10000)
            .order_by('-progress_bp')[:8]
        )
        if len(standings) < 2:
            return None

        # One representative edition per series: the one the standing's progress describes, which is the
        # furthest-along entry in the per-edition read-model. Its art is what the option shows.
        best_group_key = {}
        for st in standings:
            groups = st.group_progress or {}
            ranked = [
                (cleared / gating, key)
                for key, (cleared, gating) in (
                    (k, v) for k, v in groups.items()
                    if isinstance(v, (list, tuple)) and len(v) == 2 and v[1]
                )
            ]
            if ranked:
                best_group_key[st.series_slug] = max(ranked)[1]

        group_badges = {
            (gb.series.series_slug, gb.platform_group.key): gb
            for gb in GroupBadge.objects
            .filter(series__series_slug__in=[st.series_slug for st in standings])
            .select_related('series', 'platform_group')
        }

        candidates = []
        for st in standings:
            gb = group_badges.get((st.series_slug, best_group_key.get(st.series_slug)))
            if gb is None:
                continue
            try:
                tier, layers, is_avatar = group_medallion_layers(gb)
            except Exception:
                logger.exception("Could not resolve medallion art for group badge %s", gb.id)
                continue
            candidates.append({
                'id': str(gb.id),
                'name': gb.series.name,
                'series': gb.platform_group.name,       # the edition, as the option's subtitle
                'tier': tier,
                'state': 'unearned',
                'art_layers': layers,
                'is_avatar': is_avatar,
                'progress_pct': round(st.progress_bp / 100),
                'completed': st.stages_cleared,
                'required': st.stages_total,
            })

        if len(candidates) < 2:
            return None

        # Already ordered by progress_bp desc, so the first is the answer.
        closest, others = candidates[0], candidates[1:]
        options = [closest] + (random.sample(others, 3) if len(others) >= 3 else others)
        random.shuffle(options)

        return {
            'correct_badge_id': closest['id'],
            'correct_badge_name': closest['name'],
            'correct_progress_pct': closest['progress_pct'],
            'correct_completed': closest['completed'],
            'correct_required': closest['required'],
            # Progress is stripped from the options: it IS the answer.
            'options': [
                {k: opt[k] for k in ('id', 'name', 'series', 'tier', 'state', 'art_layers', 'is_avatar')}
                for opt in options
            ],
        }

    @classmethod
    def get_taste_for_month(cls, profile, year, month, user_tz=None):
        """What the hunter actually played this month: top genre, and top franchise if there is one.

        Both are DB-aggregated group-bys over the month's earned trophies -- a whale can earn thousands in
        a month and iterating them in Python to build a Counter is the exact pattern that OOMs the worker.

        Genres come from the IGDB enrichment (ConceptGenre), so an unmatched or PSN-only concept simply
        contributes nothing rather than skewing toward a placeholder. Excluded franchise links are
        filtered: `is_excluded` is the admin override that hides a bad IGDB link everywhere else, and this
        slide must not be the one place it leaks back in.

        Returns:
            dict or None: {genre, genre_count, runners_up: [(name, count)], franchise, franchise_count}
        """
        from trophies.models import EarnedTrophy

        start_date, end_date = cls.get_month_date_range(year, month, user_tz)
        earned = EarnedTrophy.objects.filter(
            profile=profile, earned=True,
            earned_date_time__gte=start_date, earned_date_time__lt=end_date,
        )

        genre_field = 'trophy__game__concept__concept_genres__genre__name'
        genres = list(
            earned.filter(**{f'{genre_field}__isnull': False})
            .values(genre_field).annotate(n=Count('id')).order_by('-n', genre_field)[:3]
        )
        if not genres:
            return None

        fr_field = 'trophy__game__concept__concept_franchises__franchise__name'
        franchise = (
            earned.filter(**{f'{fr_field}__isnull': False,
                             'trophy__game__concept__concept_franchises__is_excluded': False})
            .values(fr_field).annotate(n=Count('id')).order_by('-n', fr_field).first()
        )

        return {
            'genre': genres[0][genre_field],
            'genre_count': genres[0]['n'],
            'runners_up': [(row[genre_field], row['n']) for row in genres[1:]],
            'franchise': franchise[fr_field] if franchise else '',
            'franchise_count': franchise['n'] if franchise else 0,
        }

    @classmethod
    def get_community_comparison_for_month(cls, profile, year, month, user_tz=None):
        """The hunter's completion on this month's headline game, against everyone else's.

        The deck's only outward-looking beat: every other slide is the hunter alone. Uses the community
        stats already denormalized onto Game (avg_completion, played_count, plats_earned_count) by the
        nightly recalc, so this costs one indexed read rather than an aggregate over every owner.

        The "headline game" is the one they earned the most trophies in this month -- the game the month
        was actually about, not their highest completion.

        Returns:
            dict or None: {game_name, game_image, your_completion, avg_completion, played_count,
                           plats_earned_count, beats_average}
        """
        from trophies.models import EarnedTrophy, ProfileGame

        start_date, end_date = cls.get_month_date_range(year, month, user_tz)
        top = (
            EarnedTrophy.objects
            .filter(profile=profile, earned=True,
                    earned_date_time__gte=start_date, earned_date_time__lt=end_date)
            .values('trophy__game_id').annotate(n=Count('id')).order_by('-n', 'trophy__game_id').first()
        )
        if not top:
            return None

        pg = (
            ProfileGame.objects
            .filter(profile=profile, game_id=top['trophy__game_id'])
            .select_related('game', 'game__concept', 'game__concept__igdb_match')
            .defer('game__concept__igdb_match__raw_response')
            .first()
        )
        # A game with no community sample says nothing worth a slide.
        if pg is None or not pg.game.played_count:
            return None

        game = pg.game
        return {
            'game_name': game.title_name,
            'game_image': game.display_image_url or '',
            'your_completion': round(pg.progress or 0),
            'avg_completion': round(game.avg_completion or 0),
            'played_count': game.played_count,
            'plats_earned_count': game.plats_earned_count,
            'beats_average': (pg.progress or 0) > (game.avg_completion or 0),
        }

    @classmethod
    def get_month_in_history(cls, profile, year, month, user_tz=None):
        """Every OTHER year's version of this same month, plus the notable thing that happened in one.

        Year-over-year on the same month is a fairer comparison than vs-last-month, which is really a
        seasonality measurement -- December beats February for almost everyone. And it is the one beat
        that gets better the longer someone uses the site: a fifth March means a fifth bar.

        DB-aggregated into one row per active month (a couple of hundred rows for a decade-old account),
        then filtered to this month number in Python. The filter cannot move into SQL without giving up
        `TruncMonth`'s timezone handling, and the row count it runs over is summary-sized, not trophy-sized.

        Returns:
            dict or None: {years: [{year, trophies, platinums, is_current}], best_year,
                           anniversary: {years_ago, game_name, trophy_name} | None}
        """
        from trophies.models import EarnedTrophy

        user_tz = user_tz or cls._resolve_user_tz(profile)
        rows = (
            EarnedTrophy.objects
            .filter(profile=profile, earned=True, earned_date_time__isnull=False)
            .annotate(bucket=TruncMonth('earned_date_time', tzinfo=user_tz))
            .values('bucket')
            .annotate(trophies=Count('id'),
                      platinums=Count('id', filter=Q(trophy__trophy_type='platinum')))
            .order_by('bucket')
        )

        years = [
            {'year': r['bucket'].year, 'trophies': r['trophies'], 'platinums': r['platinums'],
             'is_current': r['bucket'].year == year}
            for r in rows if r['bucket'] and r['bucket'].month == month
        ]
        # One year is not a history.
        if len(years) < 2:
            return None

        # The anniversary: their FIRST platinum ever, if it landed in this month of some earlier year.
        anniversary = None
        first_plat = (
            EarnedTrophy.objects
            .filter(profile=profile, earned=True, trophy__trophy_type='platinum',
                    earned_date_time__isnull=False)
            .select_related('trophy', 'trophy__game')
            .order_by('earned_date_time')
            .first()
        )
        if first_plat:
            local = first_plat.earned_date_time.astimezone(user_tz)
            if local.month == month and local.year < year:
                anniversary = {
                    'years_ago': year - local.year,
                    'game_name': first_plat.trophy.game.title_name,
                    'trophy_name': first_plat.trophy.trophy_name,
                }

        best = max(years, key=lambda y: y['trophies'])
        return {
            'years': years,
            'best_year': best['year'],
            # The bar scale. `years` is chronological, so the template cannot find the tallest itself and
            # scaling against the first year would let later bars exceed 100%.
            'best_trophies': best['trophies'] or 1,
            'anniversary': anniversary,
        }

    @classmethod
    def get_comparison_data(cls, profile, year, month, user_tz=None):
        """
        Get comparison stats vs previous month and personal bests.

        Returns:
            dict: {vs_prev_month_pct, personal_bests: []}
        """
        from trophies.models import MonthlyRecap

        # Get current month stats
        current_total = cls.get_trophy_count_for_month(profile, year, month, user_tz=user_tz)
        current_plats = cls.get_trophy_counts_for_month(profile, year, month, user_tz=user_tz)['platinum']

        # Calculate previous month
        if month == 1:
            prev_year, prev_month = year - 1, 12
        else:
            prev_year, prev_month = year, month - 1

        prev_total = cls.get_trophy_count_for_month(profile, prev_year, prev_month, user_tz=user_tz)

        # Calculate percentage change
        if prev_total > 0:
            change_pct = round(((current_total - prev_total) / prev_total) * 100)
            vs_prev = f"+{change_pct}%" if change_pct >= 0 else f"{change_pct}%"
        elif current_total > 0:
            vs_prev = "+100%"  # Any activity vs no activity
        else:
            vs_prev = "0%"

        # Check for personal bests by looking at all previous recaps
        personal_bests = []

        # Get all finalized recaps for comparison
        past_recaps = MonthlyRecap.objects.filter(
            profile=profile,
            is_finalized=True
        ).exclude(year=year, month=month)

        if past_recaps.exists():
            # Check if most trophies in a month
            max_trophies = max((r.total_trophies_earned for r in past_recaps), default=0)
            if current_total > max_trophies:
                personal_bests.append("Most trophies in a month!")

            # Check if most platinums in a month
            max_plats = max((r.platinums_earned for r in past_recaps), default=0)
            if current_plats > max_plats and current_plats > 0:
                personal_bests.append("Most platinums in a month!")
        else:
            # First recap - everything is a personal best!
            if current_total > 0:
                personal_bests.append("Your first monthly recap!")

        # Same month, previous year. A fairer read than vs-last-month, which mostly measures seasonality:
        # almost everyone's December beats their February. Empty when there is no such month to compare to,
        # so a first-year hunter sees nothing rather than a meaningless "+100%".
        last_year_total = cls.get_trophy_count_for_month(profile, year - 1, month, user_tz=user_tz)
        if last_year_total > 0:
            yoy_pct = round(((current_total - last_year_total) / last_year_total) * 100)
            vs_last_year = f"+{yoy_pct}%" if yoy_pct >= 0 else f"{yoy_pct}%"
        else:
            vs_last_year = ''

        return {
            'vs_prev_month_pct': vs_prev,
            'vs_last_year_pct': vs_last_year,
            'last_year_total': last_year_total,
            'personal_bests': personal_bests,
        }

    @classmethod
    def months_with_activity(cls, profile, user_tz=None):
        """Every (year, month) this hunter earned a trophy in, in THEIR local time.

        This is what "months you can open" means, and it is deliberately NOT "months we have already
        stored a MonthlyRecap row for". Rows are created BY opening a month (`get_or_generate_recap`),
        so sourcing the picker from stored rows made history unreachable: a month with no row was never
        offered, so it was never opened, so it never got a row. That chicken-and-egg was the actual thing
        blocking full history -- not the premium checks.

        DB-aggregated: `TruncMonth` in the hunter's own timezone, GROUPed in Postgres, so a trophy earned
        at 23:00 on the 31st belongs to the month they experienced it in rather than the UTC one. Served
        by the partial (profile, earned, earned_date_time) index, and it returns at most one row per
        month -- never the trophies themselves.

        Scan cost is still O(that profile's trophies), so call it ONCE per render and pass `user_tz` in;
        a whale's 250k rows is a cheap index scan but not a free one.

        Returns: set of (year, month).
        """
        from trophies.models import EarnedTrophy

        user_tz = user_tz or cls._resolve_user_tz(profile)
        rows = (
            EarnedTrophy.objects
            .filter(profile=profile, earned=True, earned_date_time__isnull=False)
            .annotate(bucket=TruncMonth('earned_date_time', tzinfo=user_tz))
            .values('bucket')
            .annotate(n=Count('id'))       # forces the GROUP BY; the count itself is not selected
            .values_list('bucket', flat=True)
        )
        return {(dt.year, dt.month) for dt in rows if dt}

    @classmethod
    def get_available_months(cls, profile):
        """
        Every month this hunter can open a recap for, newest first.

        Sourced from `months_with_activity`, NOT from stored MonthlyRecap rows -- see that method for
        why. The current (in-progress) month is excluded: a recap is a retrospective, and the page 404s
        it anyway, so listing it only ever offered a door that does not open.

        Returns:
            list: [{year, month, month_name, short_month_name}, ...]
        """
        user_tz = cls._resolve_user_tz(profile)
        now_local = timezone.now().astimezone(user_tz)
        current = (now_local.year, now_local.month)

        months = sorted(
            (ym for ym in cls.months_with_activity(profile, user_tz=user_tz) if ym != current),
            reverse=True,
        )
        result = [
            {
                'year': year,
                'month': month,
                'month_name': calendar.month_name[month],
                'short_month_name': calendar.month_abbr[month],
            }
            for year, month in months
        ]

        return result

    @classmethod
    def get_available_months_by_year(cls, profile):
        """
        Get available months grouped by year for calendar display.

        Returns year-grouped structure with month metadata for rendering
        a calendar-style month selector.

        Args:
            profile: Profile instance
            (no gating argument: every month with activity is openable by any linked hunter)

        Returns:
            {
                'years': [
                    {
                        'year': 2026,
                        'months': [
                            {
                                'month': 1,
                                'month_name': 'January',
                                'short_month_name': 'Jan',
                                'has_data': True,
                                'is_current': False,
                                'is_recent': True,  # Most recent completed month
                                'is_future': False
                            },
                            # ... 11 more months
                        ]
                    },
                    # ... more years back to earliest_year
                ],
                'earliest_year': 2024,
                'current_year': 2026,
                'current_month': 2,
                'recent_year': 2026,
                'recent_month': 1
            }
        """
        # Get user timezone and current datetime
        user_tz = cls._resolve_user_tz(profile)
        now_local = timezone.now().astimezone(user_tz)
        current_year = now_local.year
        current_month = now_local.month

        # Calculate most recent completed month (previous calendar month)
        if current_month == 1:
            recent_year, recent_month = current_year - 1, 12
        else:
            recent_year, recent_month = current_year, current_month - 1

        # Which months this hunter can actually open. Trophy activity, NOT stored MonthlyRecap rows:
        # a row is created BY opening a month, so sourcing the picker from rows meant a month with no
        # row was never offered, never opened, and so never got a row. See `months_with_activity`.
        active_months = cls.months_with_activity(profile, user_tz=user_tz)

        # Earliest month, derived from `active_months` -- the SAME local-time source as has_data below.
        #
        # This used to read `first_trophy.earned_date_time.year/.month` straight off the UTC value while
        # active_months was bucketed in local time, and the two disagreed for any hunter west of UTC
        # whose first trophy landed in the opening hours of a UTC month. A Los Angeles hunter whose first
        # trophy was 2024-03-01 03:00Z earned it on 2024-02-29 locally: the flat list offered February
        # while the calendar marked it `is_before_first_trophy` and disabled it. Worse, if that first
        # trophy fell on 1 January UTC the whole preceding local year was never even emitted by the year
        # range below, so December vanished from the calendar entirely while the flat list listed it.
        if active_months:
            earliest_year, earliest_month = min(active_months)
        elif profile.created_at:
            created_local = profile.created_at.astimezone(user_tz)
            earliest_year, earliest_month = created_local.year, created_local.month
        else:
            earliest_year, earliest_month = current_year, 1

        # Build year-by-year structure
        years_data = []
        month_names = [calendar.month_name[i] for i in range(1, 13)]
        short_month_names = [calendar.month_abbr[i] for i in range(1, 13)]

        for year in range(current_year, earliest_year - 1, -1):
            months = []
            for month in range(1, 13):
                has_data = (year, month) in active_months
                is_current = (year == current_year and month == current_month)
                is_recent = (year == recent_year and month == recent_month)
                is_future = (year > current_year or
                            (year == current_year and month > current_month))

                # Check if month is before first earned trophy
                is_before_first_trophy = (year == earliest_year and month < earliest_month)


                months.append({
                    'month': month,
                    'month_name': month_names[month - 1],
                    'short_month_name': short_month_names[month - 1],
                    'has_data': has_data,
                    'is_current': is_current,
                    'is_recent': is_recent,
                    'is_future': is_future,
                    'is_before_first_trophy': is_before_first_trophy,
                })

            years_data.append({
                'year': year,
                'months': months,
            })

        return {
            'years': years_data,
            'earliest_year': earliest_year,
            'earliest_month': earliest_month,
            'current_year': current_year,
            'current_month': current_month,
            'recent_year': recent_year,
            'recent_month': recent_month,
        }

    @classmethod
    @transaction.atomic
    def finalize_month_recaps(cls, year, month):
        """
        Mark all recaps for a given month as finalized.

        Called by cron job at the start of a new month.

        Args:
            year: Year to finalize
            month: Month to finalize

        Returns:
            int: Number of recaps finalized
        """
        from trophies.models import MonthlyRecap

        count = MonthlyRecap.objects.filter(
            year=year,
            month=month,
            is_finalized=False
        ).update(is_finalized=True)

        logger.info(f"Finalized {count} monthly recaps for {year}/{month:02d}")
        return count

    @classmethod
    def generate_recaps_for_active_profiles(cls, year, month, dry_run=False):
        """
        Generate recaps for all profiles with activity in the given month.

        Args:
            year: Year
            month: Month
            dry_run: If True, only return count without generating

        Returns:
            int: Number of recaps generated
        """
        from trophies.models import Profile, EarnedTrophy

        # Use a wider window to catch all possible timezones (UTC-12 to UTC+14)
        utc_start, utc_end = cls.get_month_date_range(year, month, pytz.UTC)
        # Expand by max timezone offset to catch edge cases
        search_start = utc_start - timedelta(hours=14)
        search_end = utc_end + timedelta(hours=14)

        # Find profiles with trophy activity this month
        active_profile_ids = EarnedTrophy.objects.filter(
            earned=True,
            earned_date_time__gte=search_start,
            earned_date_time__lt=search_end
        ).values_list('profile_id', flat=True).distinct()

        # Filter to linked profiles only
        # Add select_related for user_timezone access
        profiles = Profile.objects.filter(
            id__in=active_profile_ids,
            is_linked=True,
            user__isnull=False
        ).select_related('user')

        if dry_run:
            return profiles.count()

        count = 0
        for profile in profiles:
            try:
                recap = cls.get_or_generate_recap(profile, year, month)
                if recap:
                    count += 1
            except Exception as e:
                logger.exception(f"Error generating recap for {profile.psn_username}: {e}")

        logger.info(f"Generated {count} monthly recaps for {year}/{month:02d}")
        return count

    # =========================================================================
    # QUIZ DATA METHODS
    # =========================================================================

    @classmethod
    def get_quiz_total_trophies_options(cls, profile, year, month, user_tz=None):
        """
        Generate quiz options for "guess your total trophies" quiz.

        Returns actual value + 3 decoy values that are plausible.

        Returns:
            dict: {correct_value, options: [shuffled list of 4 values]}
        """
        import random

        trophy_counts = cls.get_trophy_counts_for_month(profile, year, month, user_tz=user_tz)
        actual = trophy_counts['total']

        if actual == 0:
            return None

        # Generate plausible decoys based on the actual value
        # For small numbers, use additive offsets; for large, use percentages
        if actual <= 20:
            offsets = [-5, -3, 3, 7]
        elif actual <= 50:
            offsets = [-15, -8, 10, 20]
        else:
            # Use percentage-based offsets for larger numbers
            offsets = [
                -int(actual * 0.35),
                -int(actual * 0.15),
                int(actual * 0.25),
                int(actual * 0.45),
            ]

        # Create decoy values and ensure they're positive and unique
        decoys = set()
        for offset in offsets:
            decoy = max(1, actual + offset)
            if decoy != actual:
                decoys.add(decoy)

        # If we don't have enough decoys, add some random ones
        while len(decoys) < 3:
            if actual <= 20:
                decoy = random.randint(max(1, actual - 10), actual + 15)
            else:
                decoy = random.randint(max(1, int(actual * 0.5)), int(actual * 1.5))
            if decoy != actual:
                decoys.add(decoy)

        # Pick 3 decoys and combine with actual
        decoy_list = list(decoys)[:3]
        options = decoy_list + [actual]
        random.shuffle(options)

        return {
            'correct_value': actual,
            'options': options,
        }

    @classmethod
    def get_quiz_rarest_trophy_options(cls, profile, year, month, user_tz=None):
        """
        Generate quiz options for "which was your rarest trophy" quiz.

        Returns the rarest trophy + 3 other trophies from the month.

        Returns:
            dict: {correct_trophy_id, options: [{id, name, icon_url, game}, ...]}
        """
        import random
        from trophies.models import EarnedTrophy

        start_date, end_date = cls.get_month_date_range(year, month, user_tz)

        # Get all trophies earned this month with earn rates
        earned_trophies = list(EarnedTrophy.objects.filter(
            profile=profile,
            earned=True,
            earned_date_time__gte=start_date,
            earned_date_time__lt=end_date,
            trophy__trophy_earn_rate__gt=0
        ).select_related('trophy', 'trophy__game').order_by('trophy__trophy_earn_rate'))

        if len(earned_trophies) < 4:
            # Not enough trophies for a quiz
            return None

        # Rarest trophy (lowest earn rate) is first in ordered list
        rarest = earned_trophies[0]
        correct_id = str(rarest.trophy.id)

        # Select 3 random other trophies (not the rarest)
        other_trophies = earned_trophies[1:]
        decoys = random.sample(other_trophies, min(3, len(other_trophies)))

        # Build options
        all_trophies = [rarest] + decoys
        random.shuffle(all_trophies)

        options = []
        for et in all_trophies:
            options.append({
                'id': str(et.trophy.id),
                'name': et.trophy.trophy_name,
                'icon_url': et.trophy.trophy_icon_url or '',
                'game': et.trophy.game.title_name,
                'trophy_type': et.trophy.trophy_type,
            })

        return {
            'correct_trophy_id': correct_id,
            'options': options,
        }

    @classmethod
    def get_quiz_active_day_options(cls, profile, year, month, user_tz=None):
        """
        Generate quiz data for "guess your most active day of week" quiz.

        Returns trophy counts by day of week and the winning day.

        Returns:
            dict: {correct_day, correct_day_name, day_counts: {0: count, ...}}
        """
        from trophies.models import EarnedTrophy
        from django.db.models.functions import ExtractWeekDay

        tz = user_tz or pytz.UTC
        start_date, end_date = cls.get_month_date_range(year, month, user_tz)

        # Get trophy counts by day of week
        # ExtractWeekDay returns 1=Sunday, 2=Monday, ..., 7=Saturday
        day_counts = EarnedTrophy.objects.filter(
            profile=profile,
            earned=True,
            earned_date_time__gte=start_date,
            earned_date_time__lt=end_date
        ).annotate(
            weekday=ExtractWeekDay('earned_date_time', tzinfo=tz)
        ).values('weekday').annotate(
            count=Count('id')
        )

        if not day_counts:
            return None

        # Build counts dict and find max
        day_names = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
        counts_by_day = {i: 0 for i in range(7)}  # 0=Sunday, 6=Saturday

        for item in day_counts:
            # Convert ExtractWeekDay (1=Sunday) to our format (0=Sunday)
            day_index = item['weekday'] - 1
            counts_by_day[day_index] = item['count']

        # Find the day with most trophies
        max_day = max(counts_by_day, key=counts_by_day.get)
        max_count = counts_by_day[max_day]

        if max_count == 0:
            return None

        return {
            'correct_day': max_day,
            'correct_day_name': day_names[max_day],
            'correct_count': max_count,
            'day_counts': counts_by_day,
            'day_names': day_names,
        }

    # =========================================================================
    # NEW STAT METHODS (Streak, Time-of-Day)
    # =========================================================================

    @classmethod
    def get_streak_data(cls, profile, year, month, user_tz=None):
        """
        Calculate longest streak of consecutive active days in the month.

        Returns:
            dict or None: {longest_streak, streak_start, streak_end, total_active_days}
        """
        from trophies.models import EarnedTrophy

        tz = user_tz or pytz.UTC
        start_date, end_date = cls.get_month_date_range(year, month, user_tz)

        # Get unique earning dates in the month
        earning_dates = EarnedTrophy.objects.filter(
            profile=profile,
            earned=True,
            earned_date_time__gte=start_date,
            earned_date_time__lt=end_date
        ).annotate(
            day=TruncDate('earned_date_time', tzinfo=tz)
        ).values_list('day', flat=True).distinct().order_by('day')

        dates = list(earning_dates)

        if not dates:
            return None

        # Calculate longest streak
        longest_streak = 1
        current_streak = 1
        streak_start = dates[0]
        longest_start = dates[0]
        longest_end = dates[0]

        for i in range(1, len(dates)):
            if (dates[i] - dates[i-1]).days == 1:
                current_streak += 1
                if current_streak > longest_streak:
                    longest_streak = current_streak
                    longest_start = streak_start
                    longest_end = dates[i]
            else:
                streak_start = dates[i]
                current_streak = 1

        return {
            'longest_streak': longest_streak,
            'streak_start': longest_start.strftime('%b %d'),
            'streak_end': longest_end.strftime('%b %d'),
            'total_active_days': len(dates),
        }

    @classmethod
    def get_time_of_day_analysis(cls, profile, year, month, user_tz=None):
        """
        Analyze what time of day the user earns most trophies.

        Returns:
            dict or None: {peak_hour, peak_hour_12, peak_period, persona, periods}
        """
        from trophies.models import EarnedTrophy
        from django.db.models.functions import ExtractHour

        tz = user_tz or pytz.UTC
        start_date, end_date = cls.get_month_date_range(year, month, user_tz)

        # Group trophies by hour
        hourly_counts = EarnedTrophy.objects.filter(
            profile=profile,
            earned=True,
            earned_date_time__gte=start_date,
            earned_date_time__lt=end_date
        ).annotate(
            hour=ExtractHour('earned_date_time', tzinfo=tz)
        ).values('hour').annotate(
            count=Count('id')
        ).order_by('-count')

        if not hourly_counts:
            return None

        # Find peak hour
        peak_hour = hourly_counts[0]['hour']

        # Define periods and categorize
        def get_period(hour):
            if 5 <= hour < 12:
                return 'Morning'
            elif 12 <= hour < 17:
                return 'Afternoon'
            elif 17 <= hour < 22:
                return 'Evening'
            else:
                return 'Late Night'

        def get_persona(period):
            if period == 'Morning':
                return 'early_bird'
            elif period in ('Evening', 'Late Night'):
                return 'night_owl'
            else:
                return 'day_hunter'

        # Aggregate by period
        periods = {'Morning': 0, 'Afternoon': 0, 'Evening': 0, 'Late Night': 0}
        for item in hourly_counts:
            period = get_period(item['hour'])
            periods[period] += item['count']

        top_period = max(periods, key=periods.get)
        persona = get_persona(top_period)

        # Format peak hour in 12-hour format
        if peak_hour == 0:
            peak_hour_12 = '12AM'
        elif peak_hour < 12:
            peak_hour_12 = f'{peak_hour}AM'
        elif peak_hour == 12:
            peak_hour_12 = '12PM'
        else:
            peak_hour_12 = f'{peak_hour - 12}PM'

        return {
            'peak_hour': peak_hour,
            'peak_hour_12': peak_hour_12,
            'peak_period': top_period,
            'persona': persona,
            'periods': periods,
        }

    @classmethod
    def build_slides_response(cls, recap, include_quizzes=True):
        """Build the ordered slide array for the deck.

        Order is DATA (`DECK`, below), not an append sequence. It used to be ~110 lines of
        `if ...: slides.append(...)`, which meant the arc could only be understood by reading control flow
        and could only be changed by editing it. See the module-level `DECK` for the arc itself.

        Args:
            recap: MonthlyRecap instance
            include_quizzes: Whether to include the interactive quiz beats

        Returns:
            list: Slides array suitable for frontend rendering
        """
        ctx = {'month_name': calendar.month_name[recap.month], 'year': recap.year}
        slides = []
        for beat in DECK:
            if beat.is_quiz and not include_quizzes:
                continue
            if beat.when is not None and not beat.when(recap):
                continue
            slides.append({'type': beat.type, **beat.payload(recap, ctx)})

        # The score beat only makes sense if there is something to score, so the check runs on the
        # assembled deck rather than being guessed at up front. It must exclude ITSELF from the count:
        # `quiz_score` starts with `quiz_` too, so a naive prefix test always found a quiz and the slide
        # survived into months with nothing to grade, reading "0 / 0 guessed right".
        scorable = any(s['type'].startswith('quiz_') and s['type'] != 'quiz_score' for s in slides)
        if not scorable:
            slides = [s for s in slides if s['type'] != 'quiz_score']

        return slides


@dataclass(frozen=True)
class RecapBeat:
    """One slide in the deck: what it is, whether this month earns it, and what it carries.

    `payload` receives (recap, ctx) and returns the slide's data. `when` receives the recap; None means
    the beat always appears.
    """
    type: str
    payload: Callable
    when: Optional[Callable] = None
    is_quiz: bool = False


def _has_streak(recap):
    # A "streak" of one day is just a day. Two is the smallest number that means anything.
    return bool(recap.streak_data) and recap.streak_data.get('longest_streak', 0) >= 2


def _summary_highlights(recap):
    """The closing chips. Deliberately not every stat -- three things someone would actually say out loud."""
    highlights = []
    if recap.platinums_earned:
        highlights.append(f"{recap.platinums_earned} platinum{'' if recap.platinums_earned == 1 else 's'}")
    highlights.append(f"{recap.total_trophies_earned} trophies")
    if recap.games_started:
        highlights.append(f"{recap.games_started} new game{'' if recap.games_started == 1 else 's'}")
    if recap.badges_earned_count:
        highlights.append(f"{recap.badges_earned_count} badge{'' if recap.badges_earned_count == 1 else 's'}")
    return highlights


# The arc, in order: OPEN -> BUILD -> PEAK -> PAYOFF -> CLOSE.
#
# Each quiz sits IMMEDIATELY BEFORE the thing it asks about, so every guess is followed by its answer --
# guess, then find out. That pairing is the reason the order is data: it is an editorial decision, and it
# should be legible as a list rather than inferred from a hundred lines of appends.
#
# The peak moved. Platinums used to be the FOURTH slide, spending the deck's biggest moment before it had
# built anything; they now land after rarity, so the sequence climbs volume -> habit -> rarity -> platinums
# rather than opening on its loudest note and coasting.
#
# The payoff is the quiz score, which the deck has always computed (`RecapQuizManager.getScore`) and never
# shown. It is filled in client-side from the answers actually given, so it has no server payload.
DECK = [
    # -- OPEN ------------------------------------------------------------------------------------------
    RecapBeat('intro', lambda r, c: {
        'month_name': c['month_name'], 'year': r.year,
    }),

    # -- BUILD: how much ------------------------------------------------------------------------------
    RecapBeat('quiz_total_trophies', lambda r, c: dict(r.quiz_total_trophies_data),
              when=lambda r: bool(r.quiz_total_trophies_data), is_quiz=True),
    RecapBeat('total_trophies', lambda r, c: {
        'value': r.total_trophies_earned,
        'breakdown': {
            'bronze': r.bronzes_earned, 'silver': r.silvers_earned,
            'gold': r.golds_earned, 'platinum': r.platinums_earned,
        },
    }, when=lambda r: r.total_trophies_earned > 0),
    RecapBeat('games', lambda r, c: {
        'started': r.games_started, 'completed': r.games_completed,
    }, when=lambda r: r.games_started > 0 or r.games_completed > 0),
    RecapBeat('taste', lambda r, c: {**r.taste_data, 'month_name': c['month_name']},
              when=lambda r: bool(r.taste_data)),

    # -- BUILD: when, and how consistently ------------------------------------------------------------
    RecapBeat('quiz_active_day', lambda r, c: dict(r.quiz_active_day_data),
              when=lambda r: bool(r.quiz_active_day_data), is_quiz=True),
    RecapBeat('most_active_day', lambda r, c: dict(r.most_active_day),
              when=lambda r: bool(r.most_active_day)),
    RecapBeat('activity_calendar', lambda r, c: {
        **r.activity_calendar, 'month_name': c['month_name'], 'year': r.year,
    }, when=lambda r: bool(r.activity_calendar) and bool(r.activity_calendar.get('days'))),
    RecapBeat('streak', lambda r, c: dict(r.streak_data), when=_has_streak),
    RecapBeat('time_analysis', lambda r, c: {
        **r.time_analysis_data,
        # Bar heights are a percentage of the busiest period; never 0, which would divide by zero.
        'max_period_count': max((r.time_analysis_data.get('periods') or {}).values(), default=1) or 1,
    }, when=lambda r: bool(r.time_analysis_data)),

    # -- PEAK: what it was worth ----------------------------------------------------------------------
    RecapBeat('quiz_rarest_trophy', lambda r, c: dict(r.quiz_rarest_trophy_data),
              when=lambda r: bool(r.quiz_rarest_trophy_data), is_quiz=True),
    RecapBeat('rarest_trophy', lambda r, c: dict(r.rarest_trophy_data),
              when=lambda r: bool(r.rarest_trophy_data)),
    RecapBeat('platinums', lambda r, c: {
        'count': r.platinums_earned, 'games': r.platinums_data or [],
    }, when=lambda r: r.platinums_earned > 0),

    # -- PEAK: and what it moved ----------------------------------------------------------------------
    RecapBeat('community', lambda r, c: dict(r.community_comparison_data),
              when=lambda r: bool(r.community_comparison_data)),
    RecapBeat('quiz_closest_badge', lambda r, c: dict(r.badge_progress_quiz_data),
              when=lambda r: bool(r.badge_progress_quiz_data), is_quiz=True),
    RecapBeat('badges', lambda r, c: {
        'xp_earned': r.badge_xp_earned,
        'badges_count': r.badges_earned_count,
        'badges': r.badges_data or [],
    }, when=lambda r: r.badges_earned_count > 0),
    RecapBeat('comparison', lambda r, c: {
        'vs_prev_month': (r.comparison_data or {}).get('vs_prev_month_pct', '0%'),
        'vs_last_year': (r.comparison_data or {}).get('vs_last_year_pct', ''),
        'personal_bests': (r.comparison_data or {}).get('personal_bests', []),
    }),
    RecapBeat('month_in_history', lambda r, c: {
        **r.month_in_history_data, 'month_name': c['month_name'], 'year': r.year,
    }, when=lambda r: bool(r.month_in_history_data)),

    # -- PAYOFF + CLOSE -------------------------------------------------------------------------------
    # No payload: the score is whatever the hunter actually answered, so the controller fills it in.
    RecapBeat('quiz_score', lambda r, c: {}, is_quiz=True),
    RecapBeat('summary', lambda r, c: {'highlights': _summary_highlights(r)}),
]

# By type, for the slide-partial view -- so the API renders the same payload the deck was built from.
DECK_BY_TYPE = {beat.type: beat for beat in DECK}
