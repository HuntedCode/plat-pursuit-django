"""The synced Home page context builder.

The synced Home (`/`) is the LOBBY: where every login lands, and the one page above the four
hubs rather than inside one. Its job is narrow on purpose -- tell you your data is fresh, show
you what you came for (trophies), and put the two things that make this site worth using one
click away. It ROUTES; it never re-implements the pages it points at.

It follows the `career_service` pattern: a single `build_home_context(profile)` entry point
delegating to one helper per zone, each wrapped so a broken zone degrades to a missing section
rather than a 500.

Zones, in the order the page reads them:
- **sync** -- when the library last updated and when the next update is due. The freshness
  answer, which is the first thing a returning Pursuer wants.
- **glances.snapshot** + **recent** -- the trophy floor: headline numbers and a recent-platinum
  strip. First on the page deliberately -- it is what everyone arrives for, and it is the one
  block that is already full on the day someone finishes their first sync.
- **hero** -- Pursuer name, Level, rank title and the discipline-ring arcs, reused verbatim from
  Career (`career_service`) so the lobby's CTA teases exactly what Career pays off.
- **glances.closest_badge** -- the series nearest completion: the Collection CTA's forward-looking reason to click.
- **recent_badges** -- the last few medallions earned (REBUILT `UserGroupBadge`, bounded slice): the
  Collection CTA's backward-looking proof. Proof + next goal reads stronger than either alone.
- **community** -- the cached site heartbeat.

Deliberately NOT here: the full Pursuer Card (identity is Career's hero now, and the card was the
single most expensive thing on this page), and the five-tile navigator (the sub-nav rail covers
wayfinding; the lobby points at the two moats and nothing else).

Every read is cheap by construction: the hero is bounded by the ~25-job catalog, the
providers are the same ones the dashboard used, and the glances are counts / single rows /
denormalized Profile fields -- nothing iterates a whale's trophy set (the whale-OOM rule).
"""
import logging
from datetime import timedelta

from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from core.services.site_heartbeat import get_cached_heartbeat
from trophies.services import (
    career_service, collection_service, contract_service, profile_stats_service,
)

logger = logging.getLogger(__name__)


def _recent_platinums(profile, settings=None):
    """Last N platinums earned, with cover art and rarity -- the Home recent strip.

    Moved here from `dashboard_service` when the dashboard was retired; Home is its only caller.

    NOTE: `showcase_service.provide_recent_platinums` runs the same query for the showcase registry, with a
    different result shape. Two implementations of one idea is how they drift -- worth converging, but that
    is its own change with its own risk, so it is recorded rather than done here.
    """
    from trophies.models import EarnedTrophy

    settings = settings or {}
    limit = settings.get('limit', 6)
    plats = (
        EarnedTrophy.objects
        .filter(profile=profile, trophy__trophy_type='platinum', earned=True)
        .select_related('trophy__game__concept', 'trophy__game__concept__igdb_match')
        .order_by('-earned_date_time')[:limit]
    )

    platinums = []
    for et in plats:
        game = et.trophy.game
        concept = getattr(game, 'concept', None) if game else None
        platinums.append({
            'game_name': concept.unified_title if concept else game.title_name if game else 'Unknown',
            'icon_url': concept.cover_url if concept else (game.title_image if game else ''),
            'earned_date': et.earned_date_time,
            'earn_rate': et.trophy.trophy_earn_rate,
            'np_communication_id': game.np_communication_id if game else None,
        })

    return {'platinums': platinums}


# How many recent platinums feed the auto-scrolling marquee. Each is a game-cover image
# (lazy-loaded + duplicated for the seamless loop), so this is a deliberate balance between
# "show off a big library" and image load on the busiest page -- tune here.
RECENT_LIMIT = 20


def _safe(zone, profile, fn, default):
    """Run a zone builder, degrading to `default` (and logging) on any failure so one broken
    section never blanks the whole page."""
    try:
        return fn()
    except Exception:
        logger.exception("Home %s build failed for profile %s", zone, getattr(profile, 'id', '?'))
        return default



def _build_glances(profile):
    """The thin status row -- each a cheap read (a bounded summary, a few rows, denormalized
    fields): pending contract rewards (count + total XP waiting + a peek; the claim itself lives
    on the Research Panel), the badges closest to their next tier, and the headline trophy snapshot."""
    return {
        'claimable': _safe(
            'claimable', profile,
            lambda: contract_service.claimable_summary(profile),
            {'count': 0, 'total_xp': 0, 'items': [], 'more': 0}),
        # The series they are nearest to finishing. Reads the REBUILT subsystem's materialized
        # `SeriesBadgeStanding.progress_bp`; it used to borrow `dashboard_service.provide_badge_progress`,
        # which queried the LEGACY UserBadgeProgress table and rendered "the next earnable TIER per series"
        # -- a shape this system does not have. The provider already returns one series, so the
        # de-duplication that wrapped it is gone too.
        'closest_badge': _safe(
            'closest_badge', profile, lambda: collection_service.closest_badge(profile), None),
        'snapshot': _safe(
            'snapshot', profile,
            lambda: profile_stats_service.trophy_snapshot(profile), None),
    }



def _compact_num(n):
    """Compact a large community total for a small cell: 1.2K / 2.1M."""
    if not n:
        return '0'
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return f"{int(n):,}"


def _build_community(heartbeat):
    """A curated community-pulse strip from the cached site heartbeat (computed hourly by
    cron + cached -- free to read). Big totals are compacted for the small cells; each carries an
    icon for identity, and the 24h cell is flagged `live` (the "happening right now" pulse)."""
    if not heartbeat:
        return None
    always = heartbeat.get('always') or {}
    expanded = heartbeat.get('expanded') or {}
    # (heartbeat cell, icon, is_live)
    picks = [
        (expanded.get('platinums_total'), 'platinum', False),
        (always.get('trophies_24h'),      'pulse',    True),
        (always.get('profiles_total'),    'users',    False),
        (always.get('trophies_total'),    'trophy',   False),
    ]
    pulse = [
        {'value': _compact_num(p.get('value')), 'label': p.get('label'), 'sub': p.get('sublabel'),
         'icon': icon, 'live': live}
        for p, icon, live in picks if p
    ]
    return pulse or None


def _build_sync(profile):
    """Sync status for the trophy card: when the library last updated, and when the next
    automatic update is due (the cadence -- 12h for Discord-verified, else 24h)."""
    info = {'last_synced': getattr(profile, 'last_synced', None), 'next_sync_time': None, 'ready': True}
    secs = profile.get_seconds_to_next_sync()
    if secs and secs > 0:
        info['ready'] = False
        info['next_sync_time'] = timezone.now() + timedelta(seconds=secs)
    return info


def _recent_medallions(profile, limit=3):
    """The badges this Pursuer earned most recently, as medallion frames for the Collection CTA.

    Reads the REBUILT badge system (`UserGroupBadge`) -- NOT the legacy `UserBadge` table that
    `dashboard_service.provide_recent_badges` still queries. That table has not been retired, so a reuse of
    that helper would not fail loudly; it would quietly render badges from the retired system.

    Deliberately a bounded `[:limit]` slice rather than a collection build: `build_collection_context` is
    O(engaged series) and needs the materialized progress read-model, which is the whale-timeout shape the
    Collection page itself had to be redesigned around. A recently EARNED badge is always in the `earned`
    state, so none of that progress derivation is needed here -- just the art, the tier and the date.
    """
    from trophies.models import UserGroupBadge
    from trophies.services.badge_detail_service import group_medallion_layers

    rows = (
        UserGroupBadge.objects
        .filter(profile=profile, earned_at__isnull=False)
        .select_related('group_badge', 'group_badge__series', 'group_badge__platform_group')
        .order_by('-earned_at')[:limit]
    )
    out = []
    for row in rows:
        gb = row.group_badge
        tier, layers, is_avatar = group_medallion_layers(gb)
        out.append({
            'earned_at': row.earned_at,
            'series_slug': gb.series.series_slug,
            'frame': {
                'tier': tier,
                'state': 'earned',
                'is_holographic': bool(getattr(row, 'is_holo', False)),
                'is_avatar': is_avatar,
                'art_layers': layers,
                'series_name': gb.series.name,
                'badge_name': gb.series.name,
            },
        })
    return out


def build_home_context(profile):
    """Assemble the synced Home context for `profile`. Each zone is isolated so a single
    failure degrades to a missing section rather than a 500."""
    # The Career build is what makes the lobby personal: name, Pursuer Level, rank title and the
    # discipline-ring arcs all come off its hero, and it is cheap (6 queries / ~3ms on a real profile).
    career_ctx = _safe('career', profile, lambda: career_service.build_career_context(profile), {})
    hero = (career_ctx or {}).get('hero')
    glances = _build_glances(profile)
    return {
        # The identity data (name, Pursuer Level, rank title, discipline ring) for the Career CTA. The
        # full Pursuer Card that used to lead this page is gone: identity is Career's hero now, and the
        # card was the single most expensive thing here (8 queries / ~10ms on a real profile) for a
        # second rendering of what the CTA below already says.
        'hero': hero,
        'glances': glances,
        # Proof for the Collection CTA: the medallions you most recently earned. Bounded, and off the
        # REBUILT badge tables.
        'recent_badges': _safe('recent_badges', profile, lambda: _recent_medallions(profile), []),
        'sync': _safe('sync', profile, lambda: _build_sync(profile), None),
        'community': _safe('community', profile, lambda: _build_community(get_cached_heartbeat()), None),
        'recent': _safe(
            'recent', profile,
            lambda: _recent_platinums(profile, {'limit': RECENT_LIMIT})
            .get('platinums', []), []),
        # The trophy-snapshot card bridges gamification-first home -> trophy-data profile.
        'profile_url': _safe(
            'profile_url', profile,
            lambda: reverse('profile_detail', args=[profile.psn_username]), None),
    }
