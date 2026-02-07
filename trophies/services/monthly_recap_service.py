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
from datetime import datetime, timedelta
from django.db import transaction
from django.db.models import Count, Min, Q, F
from django.db.models.functions import TruncDate
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
        ).select_related('trophy', 'trophy__game').order_by('earned_date_time')

        result = []
        for earned in platinums:
            game = earned.trophy.game
            # Convert to user's local timezone before formatting
            local_dt = earned.earned_date_time.astimezone(tz) if earned.earned_date_time else None
            result.append({
                'game_name': game.title_name,
                'game_image': game.title_image or game.title_icon_url or '',
                'earned_date': local_dt.strftime('%b %d') if local_dt else '',
                'earn_rate': earned.trophy.trophy_earn_rate or 0,
            })

        return result

    @classmethod
    def get_rarest_trophy_in_month(cls, profile, year, month, user_tz=None):
        """
        Find the rarest trophy (lowest earn_rate) earned in the month.

        Returns:
            dict or None: {name, game, earn_rate, icon_url, trophy_type}
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
        return {
            'name': trophy.trophy_name,
            'game': trophy.game.title_name,
            'earn_rate': trophy.trophy_earn_rate,
            'icon_url': trophy.trophy_icon_url or '',
            'trophy_type': trophy.trophy_type,
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
        ).select_related('trophy', 'trophy__game').order_by('earned_date_time')

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
        """
        Get badge XP and badges earned in the month.

        Includes both badge completion bonuses (3000 XP per badge) and
        stage progress XP (tier-specific XP per concept completed).

        When a badge is earned, we attribute all its accumulated stage
        progress XP to that month as a reasonable approximation.

        Returns:
            dict: {xp_earned, badges_count, badges_data}
        """
        from trophies.models import UserBadge
        from trophies.services.xp_service import calculate_progress_xp_for_badge
        from trophies.util_modules.constants import BADGE_TIER_XP

        start_date, end_date = cls.get_month_date_range(year, month, user_tz)

        # Get badges earned this month
        badges_earned = UserBadge.objects.filter(
            profile=profile,
            earned_at__gte=start_date,
            earned_at__lt=end_date
        ).select_related('badge')

        total_xp = 0
        badges_data = []

        for user_badge in badges_earned:
            badge = user_badge.badge

            # Badge completion bonus (3000 XP)
            completion_xp = BADGE_TIER_XP

            # Stage progress XP (tier-specific: Bronze/Gold=250, Silver/Plat=75)
            # Uses badge.required_stages as completed concepts count
            progress_xp = calculate_progress_xp_for_badge(
                badge,
                badge.required_stages if badge.required_stages > 0 else 0
            )

            total_xp += completion_xp + progress_xp

            # Build badge display data
            try:
                layers = badge.get_badge_layers()
                image_url = layers.get('main', '')
            except Exception:
                image_url = ''

            badges_data.append({
                'name': badge.effective_display_series or badge.name,
                'tier': badge.tier,
                'tier_name': cls._get_tier_name(badge.tier),
                'series_slug': badge.series_slug,
                'has_image': bool(badge.badge_image or (badge.base_badge and badge.base_badge.badge_image)),
                'image_url': image_url,
            })

        return {
            'xp_earned': total_xp,
            'badges_count': len(badges_earned),
            'badges_data': badges_data,
        }

    @classmethod
    def get_badge_progress_quiz_snapshot(cls, profile, year, month, user_tz=None):
        """
        Capture a snapshot of badge progress state at the end of a month for quiz.

        Finds Tier 1 badges the user had progress on but hadn't earned by month-end,
        and creates quiz options. Data is denormalized so it doesn't change over time.

        Returns:
            dict or None: {correct_badge_id, correct_badge_name, correct_progress_pct,
                          correct_completed, correct_required, options: [...]}
        """
        import random
        from trophies.models import Badge, UserBadge, UserBadgeProgress

        # Get date at end of month to capture state at that time
        _, end_date = cls.get_month_date_range(year, month, user_tz)

        # Get badges earned by end of month
        earned_badge_ids = UserBadge.objects.filter(
            profile=profile,
            earned_at__lt=end_date
        ).values_list('badge_id', flat=True)

        # Get progress records for tier 1 badges not yet earned by month end
        # Note: UserBadgeProgress tracks current state, but we filter by earned_at
        # to determine "earned by month end" status
        progress_records = UserBadgeProgress.objects.filter(
            profile=profile,
            badge__tier=1,
            completed_concepts__gt=0,
            last_checked__lte=end_date  # Progress as of month end
        ).exclude(
            badge_id__in=earned_badge_ids
        ).select_related('badge').order_by('-completed_concepts')

        if not progress_records.exists():
            return None

        # Calculate progress percentage for each
        badges_with_progress = []
        for prog in progress_records:
            badge = prog.badge
            required = badge.required_stages if badge.required_stages > 0 else 1
            progress_pct = min(100, int((prog.completed_concepts / required) * 100))

            # Only include if they have any meaningful progress (at least 1%)
            if progress_pct >= 1:
                badges_with_progress.append({
                    'id': str(badge.id),
                    'name': badge.effective_display_title or badge.name,
                    'series': badge.effective_display_series or '',
                    'has_image': bool(badge.badge_image or (badge.base_badge and badge.base_badge.badge_image)),
                    'icon_url': badge.get_badge_layers().get('main', ''),
                    'progress_pct': progress_pct,
                    'completed': prog.completed_concepts,
                    'required': required,
                })

        if len(badges_with_progress) < 2:
            # Need at least 2 badges with progress for a quiz
            return None

        # Sort by progress percentage descending
        badges_with_progress.sort(key=lambda x: x['progress_pct'], reverse=True)

        # The closest badge is the one with highest progress
        closest = badges_with_progress[0]
        correct_id = closest['id']

        # Select up to 3 decoys from the rest
        others = badges_with_progress[1:]
        if len(others) >= 3:
            decoys = random.sample(others, 3)
        elif len(others) > 0:
            decoys = others
        else:
            # Only 1 badge with progress - can't make a quiz
            return None

        # Build options and shuffle (will have 2-4 options)
        options = [closest] + decoys
        random.shuffle(options)

        # Remove progress info from options (don't give away the answer)
        clean_options = []
        for opt in options:
            clean_options.append({
                'id': opt['id'],
                'name': opt['name'],
                'series': opt['series'],
                'icon_url': opt['icon_url'],
                'has_image': opt['has_image'],
            })

        return {
            'correct_badge_id': correct_id,
            'correct_badge_name': closest['name'],
            'correct_progress_pct': closest['progress_pct'],
            'correct_completed': closest['completed'],
            'correct_required': closest['required'],
            'options': clean_options,
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

        return {
            'vs_prev_month_pct': vs_prev,
            'personal_bests': personal_bests,
        }

    @classmethod
    def get_available_months(cls, profile, include_premium_only=True):
        """
        Get list of months that have recap data available.

        Args:
            profile: Profile instance
            include_premium_only: If False, only returns current month

        Returns:
            list: [{year, month, month_name, is_current, is_premium_required}, ...]
        """
        from trophies.models import MonthlyRecap

        user_tz = cls._resolve_user_tz(profile)
        now_local = timezone.now().astimezone(user_tz)
        current_year, current_month = now_local.year, now_local.month

        # Get all months with recaps
        recaps = MonthlyRecap.objects.filter(
            profile=profile
        ).values('year', 'month').order_by('-year', '-month')

        result = []
        for recap in recaps:
            is_current = (recap['year'] == current_year and recap['month'] == current_month)

            # Skip past months if not including premium content
            if not include_premium_only and not is_current:
                continue

            result.append({
                'year': recap['year'],
                'month': recap['month'],
                'month_name': calendar.month_name[recap['month']],
                'short_month_name': calendar.month_abbr[recap['month']],
                'is_current': is_current,
                'is_premium_required': not is_current,
            })

        return result

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

    @staticmethod
    def _get_tier_name(tier):
        """Convert tier number to display name."""
        tier_map = {1: 'Bronze', 2: 'Silver', 3: 'Gold', 4: 'Platinum'}
        return tier_map.get(tier, 'Bronze')

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
    def get_quiz_platinum_options(cls, profile, year, month, user_tz=None):
        """
        Generate quiz options for "spot your platinums" quiz.

        Returns platinum games + decoy games from the month.

        Returns:
            dict: {correct_game_ids: [], options: [{id, name, image}, ...]}
        """
        import random
        from trophies.models import EarnedTrophy

        start_date, end_date = cls.get_month_date_range(year, month, user_tz)

        # Get games platinumed this month
        platinum_games = EarnedTrophy.objects.filter(
            profile=profile,
            earned=True,
            trophy__trophy_type='platinum',
            earned_date_time__gte=start_date,
            earned_date_time__lt=end_date
        ).select_related('trophy__game').values_list('trophy__game', flat=True).distinct()

        platinum_game_ids = list(platinum_games)

        if not platinum_game_ids:
            return None

        # Get games played this month but NOT platinumed (decoys)
        from trophies.models import Game
        games_with_activity = EarnedTrophy.objects.filter(
            profile=profile,
            earned=True,
            earned_date_time__gte=start_date,
            earned_date_time__lt=end_date
        ).exclude(
            trophy__game_id__in=platinum_game_ids
        ).values_list('trophy__game_id', flat=True).distinct()

        decoy_game_ids = list(games_with_activity)

        # We need at least some decoys to make it a challenge
        target_total = min(8, len(platinum_game_ids) + max(3, len(decoy_game_ids)))
        num_decoys = target_total - len(platinum_game_ids)

        if num_decoys > len(decoy_game_ids):
            num_decoys = len(decoy_game_ids)

        if num_decoys < 2 and len(platinum_game_ids) < 3:
            # Not enough variety for a good quiz
            return None

        selected_decoys = random.sample(decoy_game_ids, num_decoys) if decoy_game_ids else []

        # Fetch game data
        all_game_ids = platinum_game_ids + selected_decoys
        games = Game.objects.filter(id__in=all_game_ids)
        games_map = {g.id: g for g in games}

        # Build options
        options = []
        for game_id in all_game_ids:
            game = games_map.get(game_id)
            if game:
                options.append({
                    'id': str(game.id),
                    'name': game.title_name,
                    'image': game.title_image or game.title_icon_url or '',
                })

        random.shuffle(options)

        return {
            'correct_game_ids': [str(gid) for gid in platinum_game_ids],
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
        """
        Build the slides array for API response.

        Args:
            recap: MonthlyRecap instance
            include_quizzes: Whether to include interactive quiz slides

        Returns:
            list: Slides array suitable for frontend rendering
        """
        slides = []
        month_name = calendar.month_name[recap.month]

        # Slide 1: Intro
        slides.append({
            'type': 'intro',
            'title': f"Your {month_name} {recap.year}",
            'subtitle': "Let's see what you accomplished",
        })

        # Quiz: Guess total trophies (before reveal)
        if include_quizzes and recap.quiz_total_trophies_data:
            slides.append({
                'type': 'quiz_total_trophies',
                **recap.quiz_total_trophies_data,
            })

        # Total Trophies reveal
        if recap.total_trophies_earned > 0:
            slides.append({
                'type': 'total_trophies',
                'value': recap.total_trophies_earned,
                'breakdown': {
                    'bronze': recap.bronzes_earned,
                    'silver': recap.silvers_earned,
                    'gold': recap.golds_earned,
                    'platinum': recap.platinums_earned,
                },
            })

        # Platinums reveal
        if recap.platinums_earned > 0:
            slides.append({
                'type': 'platinums',
                'count': recap.platinums_earned,
                'games': recap.platinums_data or [],
            })

        # Quiz: Which was your rarest? (before reveal)
        if include_quizzes and recap.quiz_rarest_trophy_data:
            slides.append({
                'type': 'quiz_rarest_trophy',
                **recap.quiz_rarest_trophy_data,
            })

        # Rarest Trophy reveal
        if recap.rarest_trophy_data:
            slides.append({
                'type': 'rarest_trophy',
                **recap.rarest_trophy_data,
            })

        # Quiz: Guess most active day of week (before calendar)
        if include_quizzes and recap.quiz_active_day_data:
            slides.append({
                'type': 'quiz_active_day',
                **recap.quiz_active_day_data,
            })

        # Most Active Day reveal
        if recap.most_active_day:
            slides.append({
                'type': 'most_active_day',
                **recap.most_active_day,
            })

        # Activity Calendar
        if recap.activity_calendar and recap.activity_calendar.get('days'):
            slides.append({
                'type': 'activity_calendar',
                **recap.activity_calendar,
                'month_name': month_name,
                'year': recap.year,
            })

        # Streak slide (NEW)
        if recap.streak_data and recap.streak_data.get('longest_streak', 0) >= 2:
            slides.append({
                'type': 'streak',
                **recap.streak_data,
            })

        # Time-of-day analysis (NEW)
        if recap.time_analysis_data:
            slides.append({
                'type': 'time_analysis',
                **recap.time_analysis_data,
            })

        # Games Started/Completed
        if recap.games_started > 0 or recap.games_completed > 0:
            slides.append({
                'type': 'games',
                'started': recap.games_started,
                'completed': recap.games_completed,
            })

        # Quiz: Which badge are you closest to? (before badges slide)
        if include_quizzes and recap.badge_progress_quiz_data:
            slides.append({
                'type': 'quiz_closest_badge',
                **recap.badge_progress_quiz_data,
            })

        # Badge XP
        if recap.badge_xp_earned > 0 or recap.badges_earned_count > 0:
            slides.append({
                'type': 'badges',
                'xp_earned': recap.badge_xp_earned,
                'badges_count': recap.badges_earned_count,
                'badges': recap.badges_data or [],
            })

        # Comparison
        comparison = recap.comparison_data or {}
        slides.append({
            'type': 'comparison',
            'vs_prev_month': comparison.get('vs_prev_month_pct', '0%'),
            'personal_bests': comparison.get('personal_bests', []),
        })

        # Summary
        highlights = []
        if recap.platinums_earned > 0:
            highlights.append(f"{recap.platinums_earned} platinum{'s' if recap.platinums_earned != 1 else ''}")
        highlights.append(f"{recap.total_trophies_earned} trophies")
        if recap.games_started > 0:
            highlights.append(f"{recap.games_started} new game{'s' if recap.games_started != 1 else ''}")

        slides.append({
            'type': 'summary',
            'highlights': highlights,
        })

        return slides
