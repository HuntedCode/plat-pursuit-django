"""
Timeline service - Builds chronologically ordered notable events for profile headers.

Collects notable events from a user's trophy hunting journey (first trophy,
fastest platinum, rarest platinum, badges, etc.) and selects the most
interesting ones for display using a priority-based algorithm.
"""
import math
import logging

from django.core.cache import cache
from django.db.models import Min

logger = logging.getLogger("psn_api")


def _make_event(event_type, title, subtitle, date, color, priority):
    """Create a timeline event dict.

    Args:
        color: CSS variable name for theming (e.g. 'primary', 'accent').
               Used as var(--color-{color}) in inline styles.
    """
    return {
        'event_type': event_type,
        'title': title,
        'subtitle': subtitle,
        'date': date,
        'color': color,
        'priority': priority,
    }


def _get_joined_event(profile):
    """Profile creation date. No query needed."""
    if not profile.created_at:
        return []
    return [_make_event(
        'joined', 'Joined PlatPursuit', 'The journey begins...',
        profile.created_at, 'secondary', 2
    )]


def _get_first_trophy_event(profile):
    """Earliest earned trophy with game info. 1 query."""
    from trophies.models import EarnedTrophy

    et = (
        EarnedTrophy.objects
        .filter(profile=profile, earned=True)
        .select_related('trophy__game')
        .order_by('earned_date_time')
        .first()
    )
    if not et or not et.earned_date_time:
        return []
    return [_make_event(
        'first_trophy', 'First Trophy',
        et.trophy.game.title_name,
        et.earned_date_time, 'primary', 7,
    )]




def _get_fastest_plat_event(profile):
    """Fastest platinum by play_duration. 1 query. Only if data exists."""
    from trophies.models import ProfileGame

    pg = (
        ProfileGame.objects
        .filter(profile=profile, has_plat=True, play_duration__isnull=False)
        .select_related('game')
        .order_by('play_duration')
        .first()
    )
    if not pg or not pg.play_duration:
        return []

    # Format duration for display
    total_seconds = int(pg.play_duration.total_seconds())
    if total_seconds < 3600:
        duration_str = f"{total_seconds // 60}m"
    elif total_seconds < 86400:
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        duration_str = f"{hours}h {minutes}m" if minutes else f"{hours}h"
    else:
        days = total_seconds // 86400
        hours = (total_seconds % 86400) // 3600
        duration_str = f"{days}d {hours}h" if hours else f"{days}d"

    return [_make_event(
        'fastest_plat', 'Fastest Platinum',
        f'{pg.game.title_name} ({duration_str})',
        pg.most_recent_trophy_date, 'success', 6,
    )]


def _get_rarest_plat_event(profile):
    """Rarest platinum. No extra query — uses denormalized FK."""
    if not profile.rarest_plat or not profile.rarest_plat.earned_date_time:
        return []

    # Skip if same as recent_plat (already shown in header card)
    if (
        profile.recent_plat
        and profile.rarest_plat_id == profile.recent_plat_id
    ):
        return []

    trophy = profile.rarest_plat.trophy
    return [_make_event(
        'rarest_plat', 'Rarest Platinum',
        f'{trophy.game.title_name} ({trophy.trophy_earn_rate}%)',
        profile.rarest_plat.earned_date_time, 'accent', 5,
    )]


def _get_badge_events(profile):
    """Top badges by tier. 1 query."""
    from trophies.models import UserBadge

    badges = list(
        UserBadge.objects
        .filter(profile=profile)
        .select_related('badge', 'badge__base_badge')
        .order_by('-badge__tier', '-earned_at')[:5]
    )
    if not badges:
        return []

    events = []
    for ub in badges:
        badge = ub.badge
        display_name = badge.effective_display_series or badge.name
        tier_names = {1: 'Bronze', 2: 'Silver', 3: 'Gold', 4: 'Platinum'}
        tier_label = tier_names.get(badge.tier, f'Tier {badge.tier}')
        # Scale priority by tier: tier 1=3, tier 2=4, tier 3=5, tier 4=6
        priority = 2 + badge.tier
        events.append(_make_event(
            'badge', f'{tier_label} Badge',
            display_name,
            ub.earned_at, 'warning', priority,
        ))
    return events


def _select_events(candidates, max_events):
    """
    Select the most interesting events from candidates using priority-based algorithm.

    Rules:
    - Always include 'joined' and 'first_trophy' if present
    - Cap badge events at 2
    - Sort by priority descending, take top max_events
    - Re-sort final selection by date descending
    """
    # Separate by type
    guaranteed = []
    badges = []
    other = []

    for event in candidates:
        if event['event_type'] in ('joined', 'first_trophy'):
            guaranteed.append(event)
        elif event['event_type'] == 'badge':
            badges.append(event)
        else:
            other.append(event)

    # Pick badges: up to 2 (highest tier first)
    picked_badges = badges[:2]

    # Guaranteed events are always included, fill remaining slots by priority
    remaining_slots = max_events - len(guaranteed)
    extras = picked_badges + other
    extras.sort(key=lambda e: e['priority'], reverse=True)
    selected = guaranteed + extras[:remaining_slots]

    # Sort by date ascending (oldest first) for left-to-right chronological display
    selected.sort(key=lambda e: e['date'])

    return selected


def build_timeline_events(profile, max_events=8):
    """
    Build a chronologically ordered list of notable timeline events for a profile.

    Uses a priority-based selection algorithm to pick the most interesting
    events, then sorts them chronologically (newest first) for display.

    Args:
        profile: Profile instance (with recent_plat/rarest_plat already loaded)
        max_events: Maximum number of events to return (default 8)

    Returns:
        list[dict] or None: Timeline events sorted by date ascending, or None
                            if fewer than 3 events (to avoid sparse timelines).
                            Each dict contains: event_type, title, subtitle,
                            date, color, priority.
    """
    # Collect all candidate events
    candidates = []
    candidates.extend(_get_joined_event(profile))
    candidates.extend(_get_first_trophy_event(profile))
    candidates.extend(_get_fastest_plat_event(profile))
    candidates.extend(_get_rarest_plat_event(profile))
    candidates.extend(_get_badge_events(profile))

    if len(candidates) < 3:
        return None

    selected = _select_events(candidates, max_events)

    if len(selected) < 3:
        return None

    return selected


def get_cached_timeline_events(profile):
    """Return cached timeline events, building and caching on miss.

    Cache key: profile:timeline:{profile_id}
    TTL: 1 hour (invalidated on sync completion via invalidate_timeline_cache)
    """
    cache_key = f"profile:timeline:{profile.id}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    events = build_timeline_events(profile)
    cache.set(cache_key, events, timeout=3600)
    return events


def invalidate_timeline_cache(profile_id):
    """Delete cached timeline for a profile (call after sync completion)."""
    cache.delete(f"profile:timeline:{profile_id}")
