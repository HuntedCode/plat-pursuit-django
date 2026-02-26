"""
Challenge Service — Core service for challenge creation, progress checking, and management.
Handles A-Z Platinum Challenges, Platinum Calendar Challenges, and Genre Challenges.
"""
import calendar as cal_module
from collections import Counter, defaultdict
from itertools import chain
import random
import logging

import pytz
from django.db.models import Count, Q
from django.utils import timezone

from trophies.models import (
    Challenge, AZChallengeSlot, CalendarChallengeDay, GenreChallengeSlot,
    GenreBonusSlot, CALENDAR_DAYS_PER_MONTH, ProfileGame, Game, EarnedTrophy,
    Concept,
)
from trophies.util_modules.constants import (
    GENRE_CHALLENGE_GENRES, GENRE_DISPLAY_NAMES, GENRE_MERGE_MAP,
    GENRE_CHALLENGE_SUBGENRES, SUBGENRE_MERGE_MAP, _GENRE_CHALLENGE_SUBGENRES_SET,
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

        now = timezone.now()
        slots_to_update = []
        for slot in pending_slots:
            if slot.game_id in platted_game_ids:
                slot.is_completed = True
                slot.completed_at = now
                slots_to_update.append(slot)

        if slots_to_update:
            AZChallengeSlot.objects.bulk_update(
                slots_to_update, ['is_completed', 'completed_at']
            )
        newly_completed = len(slots_to_update)

        if newly_completed > 0:
            # Compute counts from DB (prefetch cache may be stale after bulk_update)
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

            # Check A-Z milestone progress
            from trophies.services.milestone_service import check_all_milestones_for_user
            check_all_milestones_for_user(profile, criteria_type='az_progress')


def recalculate_challenge_counts(challenge):
    """Refresh filled_count and completed_count from actual slot data (type-aware)."""
    if challenge.challenge_type == 'az':
        challenge.filled_count = challenge.az_slots.filter(game__isnull=False).count()
        challenge.completed_count = challenge.az_slots.filter(is_completed=True).count()
    elif challenge.challenge_type == 'calendar':
        filled = challenge.calendar_days.filter(is_filled=True).count()
        challenge.filled_count = filled
        challenge.completed_count = filled
    elif challenge.challenge_type == 'genre':
        challenge.filled_count = challenge.genre_slots.filter(concept__isnull=False).count()
        challenge.completed_count = challenge.genre_slots.filter(is_completed=True).count()
        challenge.subgenre_count = len(get_collected_subgenres(challenge))
        challenge.platted_subgenre_count = len(get_platted_subgenres(challenge))
        challenge.bonus_count = challenge.bonus_slots.filter(concept__isnull=False).count()


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
    - Games where user has earned >=50% of base-game (default group) trophies.
      This implicitly covers platinumed games (platinum = 100% base-game).
    - All related versions via Concept siblings and GameFamily siblings.
    """
    played_game_ids = set(
        ProfileGame.objects.filter(profile=profile)
        .values_list('game_id', flat=True)
    )

    if not played_game_ids:
        return set()

    # Count earned base-game trophies per game (single aggregated query)
    earned_counts = dict(
        EarnedTrophy.objects.filter(
            profile=profile, earned=True,
            trophy__trophy_group_id='default',
            trophy__game_id__in=played_game_ids,
        ).values('trophy__game_id')
        .annotate(count=Count('id'))
        .values_list('trophy__game_id', 'count')
    )

    # Get total base-game trophies per game (single query)
    game_totals = dict(
        Game.objects.filter(id__in=played_game_ids)
        .values_list('id', 'defined_trophies')
    )

    excluded = set()
    for game_id, earned_count in earned_counts.items():
        defined = game_totals.get(game_id)
        if not defined:
            continue
        total_base = sum(
            defined.get(t, 0)
            for t in ('bronze', 'silver', 'gold', 'platinum')
        )
        if total_base > 0 and (earned_count / total_base) >= 0.50:
            excluded.add(game_id)

    if not excluded:
        return set()

    # Expand via Concept: all games sharing a Concept with any excluded game
    concept_ids = set(
        Game.objects.filter(
            id__in=excluded, concept__isnull=False,
        ).values_list('concept_id', flat=True)
    )
    concept_siblings = set(
        Game.objects.filter(concept_id__in=concept_ids)
        .values_list('id', flat=True)
    ) if concept_ids else set()

    # Expand via GameFamily: all games in families containing any excluded game
    family_ids = set(
        Game.objects.filter(
            id__in=excluded, concept__family__isnull=False,
        ).values_list('concept__family_id', flat=True)
    )
    family_siblings = set(
        Game.objects.filter(concept__family_id__in=family_ids)
        .values_list('id', flat=True)
    ) if family_ids else set()

    return excluded | concept_siblings | family_siblings


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
        elif challenge.challenge_type == 'genre':
            title = 'Genre Challenge Complete!'
            message = (
                f'You conquered every genre in "{challenge.name}"! '
                f'{challenge.completed_count}/{challenge.total_items} genres mastered. '
                f'Welcome to the Hall of Fame!'
            )
            action_url = f'/challenges/genre/{challenge.id}/'
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
        user_hidden=False,
    ).exclude(
        trophy__game__shovelware_status__in=['auto_flagged', 'manually_flagged'],
    ).select_related('trophy__game').order_by('earned_date_time')

    # Group by (month, day) in user's timezone: take the first per day,
    # and count total platinums per day for plat_count
    day_map = {}  # (month, day) -> EarnedTrophy (first/earliest)
    plat_counter = Counter()  # (month, day) -> total count
    for et in platinums:
        local_dt = et.earned_date_time.astimezone(user_tz)
        key = (local_dt.month, local_dt.day)
        # Skip leap day
        if key == (2, 29):
            continue
        plat_counter[key] += 1
        if key not in day_map:
            day_map[key] = et

    if not day_map:
        return

    # Fetch all days (not just unfilled) to set plat_count on all of them
    all_days = {
        (d.month, d.day): d
        for d in challenge.calendar_days.all()
    }

    to_update = []
    for key in set(day_map.keys()) | set(plat_counter.keys()):
        day_obj = all_days.get(key)
        if not day_obj:
            continue
        changed = False

        # Fill unfilled days
        et = day_map.get(key)
        if et and not day_obj.is_filled:
            day_obj.is_filled = True
            day_obj.filled_at = now
            day_obj.platinum_earned_at = et.earned_date_time
            day_obj.game_id = et.trophy.game_id
            changed = True

        # Set plat_count
        count = plat_counter.get(key, 0)
        if day_obj.plat_count != count:
            day_obj.plat_count = count
            changed = True

        if changed:
            to_update.append(day_obj)

    if to_update:
        CalendarChallengeDay.objects.bulk_update(
            to_update,
            ['is_filled', 'filled_at', 'platinum_earned_at', 'game_id', 'plat_count'],
        )

    recalculate_challenge_counts(challenge)
    challenge.save(update_fields=['filled_count', 'completed_count', 'updated_at'])

    # Check for immediate completion
    if challenge.completed_count >= challenge.total_items:
        challenge.is_complete = True
        challenge.completed_at = now
        challenge.save(update_fields=['is_complete', 'completed_at', 'updated_at'])
        _create_completion_notification(challenge)

    # Check calendar milestone progress (backfill path)
    _check_calendar_milestones(challenge.profile)


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
        user_tz = _get_user_tz(profile)
        now = timezone.now()

        # Early-exit: skip expensive full platinum scan if no new platinums since last check
        has_new_plats = EarnedTrophy.objects.filter(
            profile=profile,
            trophy__trophy_type='platinum',
            earned=True,
            earned_date_time__isnull=False,
            earned_date_time__gt=challenge.updated_at,
            user_hidden=False,
        ).exclude(
            trophy__game__shovelware_status__in=['auto_flagged', 'manually_flagged'],
        ).exists()

        if not has_new_plats:
            continue

        # Fetch all platinum earned dates for this user (no shovelware, not hidden)
        platinums = EarnedTrophy.objects.filter(
            profile=profile,
            trophy__trophy_type='platinum',
            earned=True,
            earned_date_time__isnull=False,
            user_hidden=False,
        ).exclude(
            trophy__game__shovelware_status__in=['auto_flagged', 'manually_flagged'],
        ).select_related('trophy__game').order_by('earned_date_time')

        # Build map for unfilled days + count ALL platinums per day for plat_count
        all_days = {
            (d.month, d.day): d
            for d in challenge.calendar_days.all()
        }
        unfilled_keys = {k for k, d in all_days.items() if not d.is_filled}

        day_map = {}  # (month, day) -> first EarnedTrophy (for unfilled days only)
        plat_counter = Counter()  # (month, day) -> total count
        for et in platinums:
            local_dt = et.earned_date_time.astimezone(user_tz)
            key = (local_dt.month, local_dt.day)
            if key == (2, 29):
                continue
            plat_counter[key] += 1
            if key in unfilled_keys and key not in day_map:
                day_map[key] = et

        # Update plat_count on all days + fill newly matched days
        to_update = []
        newly_filled = False
        for key, day_obj in all_days.items():
            changed = False

            # Fill unfilled days
            et = day_map.get(key)
            if et and not day_obj.is_filled:
                day_obj.is_filled = True
                day_obj.filled_at = now
                day_obj.platinum_earned_at = et.earned_date_time
                day_obj.game_id = et.trophy.game_id
                changed = True
                newly_filled = True

            # Update plat_count if changed
            count = plat_counter.get(key, 0)
            if day_obj.plat_count != count:
                day_obj.plat_count = count
                changed = True

            if changed:
                to_update.append(day_obj)

        if to_update:
            CalendarChallengeDay.objects.bulk_update(
                to_update,
                ['is_filled', 'filled_at', 'platinum_earned_at', 'game_id', 'plat_count'],
            )

        if newly_filled:
            # Compute counts in-memory from all_days dict (avoids 2 COUNT queries)
            filled = sum(1 for d in all_days.values() if d.is_filled)
            challenge.filled_count = filled
            challenge.completed_count = filled

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

            # Check calendar milestone progress (sync path)
            _check_calendar_milestones(profile)
        elif to_update:
            # plat_counts changed but no new days filled; advance the watermark
            # so the early-exit check doesn't re-scan the same platinums next sync
            challenge.save(update_fields=['updated_at'])


def _check_calendar_milestones(profile):
    """Check all calendar-related milestones for a profile in a single batch."""
    # Skip entirely if user has no calendar challenge
    if not Challenge.objects.filter(
        profile=profile, challenge_type='calendar', is_deleted=False
    ).exists():
        return

    from trophies.milestone_constants import ALL_CALENDAR_TYPES
    from trophies.services.milestone_service import check_all_milestones_for_user
    check_all_milestones_for_user(profile, criteria_types=ALL_CALENDAR_TYPES)


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
    # Prefetch all days for this challenge (plat_count is precomputed on the model)
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
                'plat_count': day_obj.plat_count if day_obj else 0,
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
    # Most platinums earned on a single calendar day
    max_plat_count = 0
    max_plat_day_label = 'N/A'
    for m in month_data:
        for d in m['days']:
            if d['is_filled']:
                current_streak += 1
                longest_streak = max(longest_streak, current_streak)
            else:
                current_streak = 0
            if d.get('plat_count', 0) > max_plat_count:
                max_plat_count = d['plat_count']
                max_plat_day_label = f"{m['month_name']} {d['day']}"

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
        'max_plat_day_count': max_plat_count,
        'max_plat_day_label': max_plat_day_label,
    }


# ─── Genre Challenge ─────────────────────────────────────────────────────────


def create_genre_challenge(profile, name='My Genre Challenge'):
    """
    Create a new Genre Challenge with one slot per curated genre.

    Returns:
        Challenge instance

    Raises:
        ValueError if user already has an active Genre challenge
    """
    if Challenge.objects.filter(
        profile=profile, challenge_type='genre', is_deleted=False, is_complete=False
    ).exists():
        raise ValueError("You already have an active Genre Challenge.")

    challenge = Challenge.objects.create(
        profile=profile,
        challenge_type='genre',
        name=name,
        total_items=len(GENRE_CHALLENGE_GENRES),
    )
    slots = [
        GenreChallengeSlot(
            challenge=challenge,
            genre=genre,
            genre_display=GENRE_DISPLAY_NAMES.get(genre, genre),
        )
        for genre in GENRE_CHALLENGE_GENRES
    ]
    GenreChallengeSlot.objects.bulk_create(slots)
    return challenge


def check_genre_challenge_progress(profile):
    """
    Check all active Genre challenges for newly completed slots.
    Called during sync in _job_sync_complete().

    A slot is completed when ANY game under the assigned concept has been platted.
    """
    challenges = Challenge.objects.filter(
        profile=profile, challenge_type='genre',
        is_deleted=False, is_complete=False,
    ).prefetch_related('genre_slots__concept', 'bonus_slots__concept')

    for challenge in challenges:
        any_updated = False

        # --- Check genre slots ---
        pending_slots = [
            s for s in challenge.genre_slots.all()
            if s.concept_id and not s.is_completed
        ]
        # --- Check bonus slots ---
        bonus_pending = [
            s for s in challenge.bonus_slots.all()
            if s.concept_id and not s.is_completed
        ]

        all_pending_concept_ids = (
            [s.concept_id for s in pending_slots]
            + [s.concept_id for s in bonus_pending]
        )
        if not all_pending_concept_ids:
            continue

        # Build concept -> game IDs map
        concept_game_map = defaultdict(set)
        for game_id, concept_id in Game.objects.filter(
            concept_id__in=all_pending_concept_ids
        ).values_list('id', 'concept_id'):
            concept_game_map[concept_id].add(game_id)

        # Flatten all game IDs and batch-check which are platted
        all_game_ids = set()
        for gids in concept_game_map.values():
            all_game_ids.update(gids)

        if not all_game_ids:
            continue

        platted_game_ids = set(
            ProfileGame.objects.filter(
                profile=profile, game_id__in=all_game_ids, has_plat=True
            ).values_list('game_id', flat=True)
        )

        # A concept is platted if any of its games are platted
        platted_concept_ids = {
            c_id for c_id, g_ids in concept_game_map.items()
            if g_ids & platted_game_ids
        }

        now = timezone.now()

        # Update genre slots
        genre_to_update = []
        for slot in pending_slots:
            if slot.concept_id in platted_concept_ids:
                slot.is_completed = True
                slot.completed_at = now
                genre_to_update.append(slot)
        if genre_to_update:
            GenreChallengeSlot.objects.bulk_update(
                genre_to_update, ['is_completed', 'completed_at']
            )
            any_updated = True

        # Update bonus slots
        bonus_to_update = []
        for slot in bonus_pending:
            if slot.concept_id in platted_concept_ids:
                slot.is_completed = True
                slot.completed_at = now
                bonus_to_update.append(slot)
        if bonus_to_update:
            GenreBonusSlot.objects.bulk_update(
                bonus_to_update, ['is_completed', 'completed_at']
            )
            any_updated = True

        if any_updated:
            recalculate_challenge_counts(challenge)
            total_genres = len(GENRE_CHALLENGE_GENRES)
            save_fields = [
                'completed_count', 'filled_count', 'subgenre_count',
                'platted_subgenre_count', 'bonus_count', 'updated_at',
            ]
            if challenge.completed_count == total_genres:
                challenge.is_complete = True
                challenge.completed_at = timezone.now()
                save_fields.extend(['is_complete', 'completed_at'])
                challenge.save(update_fields=save_fields)
                _create_completion_notification(challenge)
            else:
                challenge.save(update_fields=save_fields)

    # Check genre milestone progress after processing all challenges
    from trophies.services.milestone_service import check_all_milestones_for_user
    check_all_milestones_for_user(profile, criteria_types=['genre_progress', 'subgenre_progress'])


def auto_set_cover_genre(challenge):
    """
    Pick a random assigned slot's genre as the cover.
    Returns the chosen genre key or '' if no concepts are assigned.
    """
    assigned_genres = list(
        challenge.genre_slots.filter(concept__isnull=False)
        .values_list('genre', flat=True)
    )
    if not assigned_genres:
        challenge.cover_genre = ''
    else:
        challenge.cover_genre = random.choice(assigned_genres)
    challenge.save(update_fields=['cover_genre'])
    return challenge.cover_genre


def get_genre_excluded_concept_ids(profile):
    """
    Return set of concept IDs to exclude from Genre challenge search:
    - Concepts where user has platted any game under the concept
    - Concepts where user has >50% progress on any game under the concept
    - GameFamily siblings of platted concepts (prevents cross-gen duplicates)
    """
    platted_concept_ids = set(
        ProfileGame.objects.filter(
            profile=profile, has_plat=True,
        ).exclude(
            game__concept__isnull=True
        ).values_list('game__concept_id', flat=True)
    )

    progress_concept_ids = set(
        ProfileGame.objects.filter(
            profile=profile, progress__gt=50,
        ).exclude(
            game__concept__isnull=True
        ).values_list('game__concept_id', flat=True)
    )

    # GameFamily sibling expansion for platted concepts
    family_sibling_concept_ids = set()
    if platted_concept_ids:
        family_ids = set(
            Concept.objects.filter(
                id__in=platted_concept_ids,
                family__isnull=False,
            ).values_list('family_id', flat=True)
        )
        if family_ids:
            family_sibling_concept_ids = set(
                Concept.objects.filter(
                    family_id__in=family_ids,
                ).values_list('id', flat=True)
            )

    return platted_concept_ids | progress_concept_ids | family_sibling_concept_ids


def resolve_subgenres(raw_subgenres):
    """
    Map raw PSN subgenre strings to curated subgenre keys.
    Filters out N/A, MMORPG, and any other uncurated values.
    Returns a set of curated subgenre keys.
    """
    result = set()
    for sg in raw_subgenres:
        if not sg:
            continue
        # Check merge map first, then direct membership
        mapped = SUBGENRE_MERGE_MAP.get(sg, sg)
        if mapped in _GENRE_CHALLENGE_SUBGENRES_SET:
            result.add(mapped)
    return result


def get_subgenre_status(challenge):
    """
    Return dict mapping curated subgenre key -> 'platted' | 'assigned'.
    A subgenre is 'platted' if ANY slot contributing it has is_completed=True.
    A subgenre is 'assigned' if contributed by an assigned-but-not-completed slot.
    Subgenres not in the dict are uncollected.
    """
    status = {}
    for slot in chain(
        challenge.genre_slots.filter(concept__isnull=False).select_related('concept'),
        challenge.bonus_slots.filter(concept__isnull=False).select_related('concept'),
    ):
        resolved = resolve_subgenres(slot.concept.subgenres or [])
        slot_status = 'platted' if slot.is_completed else 'assigned'
        for sg in resolved:
            if status.get(sg) != 'platted':
                status[sg] = slot_status
    return status


def get_collected_subgenres(challenge):
    """
    Return the set of curated subgenre keys collected from all assigned concepts.
    Thin wrapper around get_subgenre_status() for backward compatibility.
    """
    return set(get_subgenre_status(challenge).keys())


def get_platted_subgenres(challenge):
    """Return the set of curated subgenre keys that are platted (not just assigned)."""
    return {k for k, v in get_subgenre_status(challenge).items() if v == 'platted'}


def recalculate_subgenre_count(challenge):
    """Recalculate and save both subgenre counts for a genre challenge."""
    challenge.subgenre_count = len(get_collected_subgenres(challenge))
    challenge.platted_subgenre_count = len(get_platted_subgenres(challenge))
    challenge.save(update_fields=['subgenre_count', 'platted_subgenre_count'])


def get_genre_swap_targets(concept, challenge, current_source):
    """
    Return list of valid move targets for a concept within a genre challenge.

    Args:
        concept: The Concept being moved
        challenge: The Challenge instance
        current_source: The genre key or 'BONUS' where the concept currently is

    Returns:
        List of dicts: [{'genre': str, 'genre_display': str, 'has_game': bool}]
        Always includes 'BONUS' as a target (unless source is already bonus).
    """
    _GENRE_SET = set(GENRE_CHALLENGE_GENRES)

    # Map concept genres through GENRE_MERGE_MAP to find qualifying genre keys
    concept_genres = set(concept.genres or [])
    mapped_genres = set()
    for g in concept_genres:
        mapped = GENRE_MERGE_MAP.get(g, g)
        if mapped in _GENRE_SET:
            mapped_genres.add(mapped)

    targets = []
    for slot in challenge.genre_slots.select_related('concept').all():
        if slot.genre == current_source:
            continue
        if slot.is_completed:
            continue
        if slot.genre in mapped_genres and not slot.concept_id:
            targets.append({
                'genre': slot.genre,
                'genre_display': slot.genre_display,
                'has_game': False,
            })

    # Add bonus as a target (unless already in bonus)
    if current_source != 'BONUS':
        targets.append({
            'genre': 'BONUS',
            'genre_display': 'Bonus Slot',
            'has_game': False,
        })

    return targets
