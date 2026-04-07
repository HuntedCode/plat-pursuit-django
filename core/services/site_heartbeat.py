"""
Site Heartbeat: aggregated platform statistics for the dashboard's
"Built for Hunters" section.

Computed once per hour by refresh_homepage_hourly cron, cached under a
date+hour key, read by the home page (HomeView and the dashboard it serves
for synced users) at render time. Never computed synchronously on a user
request.

The dict shape is intentionally stable so the template can do simple
nested lookups (e.g. heartbeat.always.trophies_total.value).
"""
import logging
from datetime import timedelta

from django.db.models import Sum
from django.utils import timezone

from core.services.stats import compute_community_stats
from trophies.models import EarnedTrophy, ProfileGame

logger = logging.getLogger(__name__)


def _humanize_compact(n):
    """Format a number into compact form: 1234 -> '1.2K', 1_234_567 -> '1.2M'."""
    if n is None:
        return '0'
    n = int(n)
    if n < 1_000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1_000:.1f}K".replace('.0K', 'K')
    if n < 1_000_000_000:
        return f"{n / 1_000_000:.1f}M".replace('.0M', 'M')
    return f"{n / 1_000_000_000:.1f}B".replace('.0B', 'B')


def _query_trophies_24h():
    """Total trophies earned across all profiles in the last 24 hours."""
    window = timezone.now() - timedelta(hours=24)
    return EarnedTrophy.objects.filter(
        earned=True,
        earned_date_time__gte=window,
    ).count()


def _query_hours_hunted():
    """Total real PSN-tracked playtime across all profiles, in whole hours."""
    result = ProfileGame.objects.filter(
        user_hidden=False,
        play_duration__isnull=False,
    ).aggregate(total=Sum('play_duration'))
    total = result['total'] or timedelta(0)
    return int(total.total_seconds() // 3600)


def compute_site_heartbeat() -> dict:
    """
    Compute the full site-heartbeat dict.

    Returns a structured dict with `meta`, `always`, `expanded`, and
    `flavor` keys. Each stat is wrapped in its own try/except so a
    single failed query never blanks the whole section; failed stats
    return None and `meta.is_partial` flips to True.
    """
    is_partial = False
    now = timezone.now()

    # Reuse the existing community stats for 6 of 8 values
    try:
        community = compute_community_stats()
    except Exception:
        logger.exception("compute_community_stats failed inside compute_site_heartbeat")
        community = None
        is_partial = True

    def _community_value(*path, default=0):
        """Safely walk the community stats dict, returning default on any miss."""
        if community is None:
            return default
        node = community
        for key in path:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node if node is not None else default

    # 24h trophies (live pulse)
    try:
        trophies_24h = _query_trophies_24h()
    except Exception:
        logger.exception("trophies_24h query failed")
        trophies_24h = None
        is_partial = True

    # Real hours hunted from PSN play_duration
    try:
        hours_hunted = _query_hours_hunted()
    except Exception:
        logger.exception("hours_hunted query failed")
        hours_hunted = None
        is_partial = True

    # Pull reused values
    trophies_total = _community_value('trophies', 'total')
    games_total = _community_value('games', 'total')
    games_weekly = _community_value('games', 'weekly')
    profiles_total = _community_value('profiles', 'total')
    profiles_weekly = _community_value('profiles', 'weekly')
    platinums_total = _community_value('platinums', 'total')
    badges_total = _community_value('badge_series', 'total')
    badge_xp_total = _community_value('badge_xp', 'total')

    # Flavor line: pre-formatted in service so template stays dumb
    flavor_numbers = (
        f"{_humanize_compact(badge_xp_total)} XP earned · "
        f"{profiles_total:,} hunters · "
        f"Synced hourly."
    )

    return {
        'meta': {
            'computed_at': now.isoformat(),
            'is_partial': is_partial,
        },
        'always': {
            'trophies_total': {
                'value': trophies_total,
                'label': 'Trophies tracked',
                'sublabel': 'all-time',
            },
            'games_total': {
                'value': games_total,
                'label': 'Games in catalog',
                'sublabel': f"+{games_weekly:,} this week" if games_weekly else "growing",
                'delta': games_weekly,
            },
            'profiles_total': {
                'value': profiles_total,
                'label': 'Hunters tracked',
                'sublabel': f"+{profiles_weekly:,} this week" if profiles_weekly else "growing",
                'delta': profiles_weekly,
            },
            'trophies_24h': {
                'value': trophies_24h,
                'label': 'Earned in last 24h',
                'sublabel': 'live',
            },
        },
        'expanded': {
            'platinums_total': {
                'value': platinums_total,
                'label': 'Platinums earned',
                'sublabel': 'all-time',
            },
            'badges_total': {
                'value': badges_total,
                'label': 'Unique badges',
                'sublabel': 'to earn',
            },
            'badge_xp_total': {
                'value': badge_xp_total,
                'label': 'Badge XP awarded',
                'sublabel': 'all-time',
            },
            'hours_hunted': {
                'value': hours_hunted,
                'label': 'Hours hunted',
                'sublabel': 'real PSN playtime',
            },
        },
        'flavor': {
            'tagline': 'Built by trophy hunters, for trophy hunters.',
            'numbers': flavor_numbers,
        },
    }
