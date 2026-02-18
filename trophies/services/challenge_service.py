"""
Challenge Service — Core service for challenge creation, progress checking, and management.
Handles A-Z Platinum Challenges and Platinum Calendar Challenges.
"""
import calendar as cal_module
import random
import logging

import pytz
from django.db.models import Q
from django.utils import timezone

from trophies.models import (
    Challenge, AZChallengeSlot, CalendarChallengeDay,
    CALENDAR_DAYS_PER_MONTH, ProfileGame, Game, EarnedTrophy,
)

logger = logging.getLogger("psn_api")


def create_az_challenge(profile, name='My A-Z Challenge'):
    """
    Create a new A-Z Challenge with 26 empty slots (A-Z).
    Validates that no existing active AZ challenge exists for this user.

    Returns:
        Challenge instance

    Raises:
        ValueError if user already has an active AZ challenge
    """
    if Challenge.objects.filter(
        profile=profile, challenge_type='az', is_deleted=False, is_complete=False
    ).exists():
        raise ValueError("You already have an active A-Z Challenge.")

    challenge = Challenge.objects.create(
        profile=profile,
        challenge_type='az',
        name=name,
        total_items=26,
    )
    slots = [
        AZChallengeSlot(challenge=challenge, letter=chr(i))
        for i in range(65, 91)  # A-Z
    ]
    AZChallengeSlot.objects.bulk_create(slots)
    return challenge


def check_az_challenge_progress(profile):
    """
    Check all active A-Z challenges for newly completed slots.
    Called during sync in _job_sync_complete().

    Uses a batch query on ProfileGame.has_plat instead of N+1
    Concept.has_user_earned_platinum() calls.
    """
    challenges = Challenge.objects.filter(
        profile=profile, challenge_type='az', is_deleted=False, is_complete=False
    ).prefetch_related('az_slots__game')

    for challenge in challenges:
        pending_slots = list(
            challenge.az_slots.filter(game__isnull=False, is_completed=False)
        )
        if not pending_slots:
            continue

        game_ids = [s.game_id for s in pending_slots]

        # Batch: which of these games has the user platted?
        platted_game_ids = set(
            ProfileGame.objects.filter(
                profile=profile, game_id__in=game_ids, has_plat=True
            ).values_list('game_id', flat=True)
        )

        newly_completed = 0
        for slot in pending_slots:
            if slot.game_id in platted_game_ids:
                slot.is_completed = True
                slot.completed_at = timezone.now()
                slot.save(update_fields=['is_completed', 'completed_at'])
                newly_completed += 1

        if newly_completed > 0:
            recalculate_challenge_counts(challenge)
            if challenge.completed_count == 26:
                challenge.is_complete = True
                challenge.completed_at = timezone.now()
                challenge.save(update_fields=[
                    'completed_count', 'filled_count', 'is_complete',
                    'completed_at', 'updated_at',
                ])
                _create_completion_notification(challenge)
            else:
                challenge.save(update_fields=[
                    'completed_count', 'filled_count', 'updated_at',
                ])


def recalculate_challenge_counts(challenge):
    """Refresh filled_count and completed_count from actual slot data (type-aware)."""
    if challenge.challenge_type == 'az':
        challenge.filled_count = challenge.az_slots.filter(game__isnull=False).count()
        challenge.completed_count = challenge.az_slots.filter(is_completed=True).count()
    elif challenge.challenge_type == 'calendar':
        filled = challenge.calendar_days.filter(is_filled=True).count()
        challenge.filled_count = filled
        challenge.completed_count = filled


def auto_set_cover_letter(challenge):
    """
    Pick a random assigned slot's letter as the cover.
    Called when cover_letter is empty (first assignment) or when the
    current cover slot's game is cleared.
    Returns the chosen letter or '' if no games are assigned.
    """
    assigned_letters = list(
        challenge.az_slots.filter(game__isnull=False).values_list('letter', flat=True)
    )
    if not assigned_letters:
        challenge.cover_letter = ''
    else:
        challenge.cover_letter = random.choice(assigned_letters)
    challenge.save(update_fields=['cover_letter'])
    return challenge.cover_letter


def get_excluded_game_ids(profile):
    """
    Return set of game IDs to exclude from AZ search:
    - Games user has >50% progress on (that specific game only)
    - Games user already has platinum for, PLUS all related versions:
      - Concept siblings (same Concept, different platform/region)
      - GameFamily siblings (different Concept, same GameFamily)
    """
    # Tier 1: >50% progress — excludes only the specific game
    progress_excluded = set(
        ProfileGame.objects.filter(
            profile=profile, progress__gt=50,
        ).values_list('game_id', flat=True)
    )

    # Tier 2: Platinumed games + concept/family expansion
    plat_game_ids = set(
        ProfileGame.objects.filter(
            profile=profile, has_plat=True,
        ).values_list('game_id', flat=True)
    )

    if not plat_game_ids:
        return progress_excluded

    # Expand via Concept: all games sharing a Concept with any platted game
    concept_ids = set(
        Game.objects.filter(
            id__in=plat_game_ids, concept__isnull=False,
        ).values_list('concept_id', flat=True)
    )
    concept_siblings = set(
        Game.objects.filter(concept_id__in=concept_ids)
        .values_list('id', flat=True)
    ) if concept_ids else set()

    # Expand via GameFamily: all games in families that contain any platted game's concept
    family_ids = set(
        Game.objects.filter(
            id__in=plat_game_ids, concept__family__isnull=False,
        ).values_list('concept__family_id', flat=True)
    )
    family_siblings = set(
        Game.objects.filter(concept__family_id__in=family_ids)
        .values_list('id', flat=True)
    ) if family_ids else set()

    return progress_excluded | plat_game_ids | concept_siblings | family_siblings


def _create_completion_notification(challenge):
    """Create in-app notification for challenge completion (type-aware)."""
    try:
        from notifications.services.notification_service import NotificationService

        if challenge.challenge_type == 'calendar':
            title = 'Platinum Calendar Complete!'
            message = (
                f'You filled every day of the year in your Platinum Calendar '
                f'"{challenge.name}"! {challenge.total_items}/{challenge.total_items}: '
                f'welcome to the Hall of Fame!'
            )
            action_url = f'/challenges/calendar/{challenge.id}/'
        else:
            title = 'A-Z Challenge Complete!'
            message = (
                f'You completed your A-Z Challenge "{challenge.name}"! '
                f'Welcome to the Hall of Fame!'
            )
            action_url = f'/challenges/az/{challenge.id}/'

        NotificationService.create_notification(
            recipient=challenge.profile.user,
            notification_type='challenge_completed',
            title=title,
            message=message,
            icon='🏆',
            action_url=action_url,
            action_text='View Challenge',
            metadata={
                'challenge_id': challenge.id,
                'challenge_type': challenge.challenge_type,
                'challenge_name': challenge.name,
                'completed_count': challenge.completed_count,
                'total_items': challenge.total_items,
            },
        )
    except Exception:
        logger.exception(f"Failed to create completion notification for challenge {challenge.id}")


# ─── Platinum Calendar Challenge ──────────────────────────────────────────────


def _get_user_tz(profile):
    """Resolve the user's timezone, falling back to UTC."""
    try:
        tz_name = profile.user.user_timezone if profile.user else 'UTC'
        return pytz.timezone(tz_name or 'UTC')
    except (pytz.exceptions.UnknownTimeZoneError, AttributeError):
        return pytz.UTC


def create_calendar_challenge(profile, name='My Platinum Calendar'):
    """
    Create a Platinum Calendar Challenge with 365 empty day slots (no Feb 29).
    Auto-backfills from the user's existing platinum history.

    Returns:
        Challenge instance

    Raises:
        ValueError if user already has an active Calendar challenge
    """
    if Challenge.objects.filter(
        profile=profile, challenge_type='calendar', is_deleted=False, is_complete=False
    ).exists():
        raise ValueError("You already have an active Platinum Calendar.")

    challenge = Challenge.objects.create(
        profile=profile,
        challenge_type='calendar',
        name=name,
        total_items=365,
    )

    # Create 365 day slots (skip Feb 29)
    days = []
    for month, num_days in CALENDAR_DAYS_PER_MONTH.items():
        for day in range(1, num_days + 1):
            days.append(CalendarChallengeDay(
                challenge=challenge, month=month, day=day,
            ))
    CalendarChallengeDay.objects.bulk_create(days)

    # Backfill from existing platinum history
    backfill_calendar_from_history(challenge)

    return challenge


def backfill_calendar_from_history(challenge):
    """
    Scan all of the user's earned platinums and fill matching calendar days.
    Uses the EARLIEST platinum for each calendar day. Respects user timezone.
    Excludes shovelware games.
    """
    user_tz = _get_user_tz(challenge.profile)
    now = timezone.now()

    # Fetch all platinum earned trophies with dates, oldest first (no shovelware, not hidden)
    platinums = EarnedTrophy.objects.filter(
        profile=challenge.profile,
        trophy__trophy_type='platinum',
        earned=True,
        earned_date_time__isnull=False,
        trophy__game__is_shovelware=False,
        user_hidden=False,
    ).select_related('trophy__game').order_by('earned_date_time')

    # Group by (month, day) in user's timezone: take the first per day
    day_map = {}  # (month, day) -> EarnedTrophy
    for et in platinums:
        local_dt = et.earned_date_time.astimezone(user_tz)
        key = (local_dt.month, local_dt.day)
        # Skip leap day
        if key == (2, 29):
            continue
        if key not in day_map:
            day_map[key] = et

    if not day_map:
        return

    # Fetch all unfilled days and match
    unfilled_days = {
        (d.month, d.day): d
        for d in challenge.calendar_days.filter(is_filled=False)
    }

    to_update = []
    for key, et in day_map.items():
        day_obj = unfilled_days.get(key)
        if day_obj:
            day_obj.is_filled = True
            day_obj.filled_at = now
            day_obj.platinum_earned_at = et.earned_date_time
            day_obj.game_id = et.trophy.game_id
            to_update.append(day_obj)

    if to_update:
        CalendarChallengeDay.objects.bulk_update(
            to_update, ['is_filled', 'filled_at', 'platinum_earned_at', 'game_id']
        )

    recalculate_challenge_counts(challenge)
    challenge.save(update_fields=['filled_count', 'completed_count', 'updated_at'])

    # Check for immediate completion
    if challenge.completed_count >= challenge.total_items:
        challenge.is_complete = True
        challenge.completed_at = now
        challenge.save(update_fields=['is_complete', 'completed_at', 'updated_at'])
        _create_completion_notification(challenge)


def check_calendar_challenge_progress(profile):
    """
    Check active Calendar challenges for newly filled days.
    Called during sync in _job_sync_complete(). Excludes shovelware games.
    """
    challenges = Challenge.objects.filter(
        profile=profile, challenge_type='calendar',
        is_deleted=False, is_complete=False,
    )

    for challenge in challenges:
        unfilled_keys = set(
            challenge.calendar_days.filter(is_filled=False)
            .values_list('month', 'day')
        )
        if not unfilled_keys:
            continue

        user_tz = _get_user_tz(profile)
        now = timezone.now()

        # Fetch all platinum earned dates for this user (no shovelware, not hidden)
        platinums = EarnedTrophy.objects.filter(
            profile=profile,
            trophy__trophy_type='platinum',
            earned=True,
            earned_date_time__isnull=False,
            trophy__game__is_shovelware=False,
            user_hidden=False,
        ).select_related('trophy__game').order_by('earned_date_time')

        # Build map: (month, day) -> first EarnedTrophy for unfilled days only
        day_map = {}
        for et in platinums:
            local_dt = et.earned_date_time.astimezone(user_tz)
            key = (local_dt.month, local_dt.day)
            if key == (2, 29):
                continue
            if key in unfilled_keys and key not in day_map:
                day_map[key] = et

        if not day_map:
            continue

        # Fetch unfilled day objects and match
        unfilled_days = {
            (d.month, d.day): d
            for d in challenge.calendar_days.filter(is_filled=False)
        }

        to_update = []
        for key, et in day_map.items():
            day_obj = unfilled_days.get(key)
            if day_obj:
                day_obj.is_filled = True
                day_obj.filled_at = now
                day_obj.platinum_earned_at = et.earned_date_time
                day_obj.game_id = et.trophy.game_id
                to_update.append(day_obj)

        if to_update:
            CalendarChallengeDay.objects.bulk_update(
                to_update, ['is_filled', 'filled_at', 'platinum_earned_at', 'game_id']
            )

            recalculate_challenge_counts(challenge)

            if challenge.completed_count >= challenge.total_items:
                challenge.is_complete = True
                challenge.completed_at = now
                challenge.save(update_fields=[
                    'completed_count', 'filled_count', 'is_complete',
                    'completed_at', 'updated_at',
                ])
                _create_completion_notification(challenge)
            else:
                challenge.save(update_fields=[
                    'completed_count', 'filled_count', 'updated_at',
                ])


def _get_calendar_year():
    """
    The calendar grid uses the current year for weekday alignment.
    The calendar is a perpetual 365-slot challenge. Feb 29 is always excluded
    even in leap years. Only the weekday offsets and holiday positions change
    each year.
    """
    from django.utils import timezone
    return timezone.now().year


def get_calendar_month_data(challenge):
    """
    Build structured month data for template rendering.
    Returns a list of 12 month dicts, each with:
      - month_num, month_name, num_days, weekday_offset
      - days: list of day dicts with day_num, is_filled, game info
    """
    # Prefetch all days for this challenge
    all_days = {
        (d.month, d.day): d
        for d in challenge.calendar_days.select_related('game').all()
    }

    months = []
    for month_num in range(1, 13):
        num_days = CALENDAR_DAYS_PER_MONTH[month_num]

        # Weekday of the 1st: cal_module.weekday returns 0=Monday
        # Convert to Sunday-start: (weekday + 1) % 7, where 0=Sunday
        first_weekday = cal_module.weekday(_get_calendar_year(), month_num, 1)
        weekday_offset = (first_weekday + 1) % 7

        days_list = []
        for day_num in range(1, num_days + 1):
            day_obj = all_days.get((month_num, day_num))
            days_list.append({
                'day': day_num,
                'is_filled': day_obj.is_filled if day_obj else False,
                'game_name': day_obj.game.title_name if day_obj and day_obj.game else '',
                'game_icon': (
                    (day_obj.game.title_icon_url or day_obj.game.title_image or '')
                    if day_obj and day_obj.game else ''
                ),
            })

        months.append({
            'month_num': month_num,
            'month_name': cal_module.month_name[month_num],
            'month_abbr': cal_module.month_abbr[month_num],
            'num_days': num_days,
            'weekday_offset': weekday_offset,
            'offset_range': range(weekday_offset),  # For template iteration
            'days': days_list,
            'filled_count': sum(1 for d in days_list if d['is_filled']),
        })

    return months


def get_calendar_stats(challenge, month_data=None):
    """
    Compute display stats for a Calendar challenge.
    Optionally accepts pre-computed month_data to avoid redundant queries.
    """
    if month_data is None:
        month_data = get_calendar_month_data(challenge)

    total_filled = sum(m['filled_count'] for m in month_data)

    # Best and worst months (by fill count)
    best_month = max(month_data, key=lambda m: m['filled_count'])
    worst_month = min(month_data, key=lambda m: m['filled_count'])

    # Longest streak of consecutive filled days (across the whole year)
    longest_streak = 0
    current_streak = 0
    for m in month_data:
        for d in m['days']:
            if d['is_filled']:
                current_streak += 1
                longest_streak = max(longest_streak, current_streak)
            else:
                current_streak = 0

    return {
        'total_filled': total_filled,
        'total_days': sum(m['num_days'] for m in month_data),
        'best_month_name': best_month['month_name'],
        'best_month_filled': best_month['filled_count'],
        'best_month_total': best_month['num_days'],
        'worst_month_name': worst_month['month_name'],
        'worst_month_filled': worst_month['filled_count'],
        'worst_month_total': worst_month['num_days'],
        'longest_streak': longest_streak,
    }
