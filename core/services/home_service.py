"""The synced Home page context builder.

The synced Home (`/`) is the Pursuer's landing: a glanceable identity + status surface
that ROUTES into the functional My Pursuit pages, not a re-implementation of them. It
follows the `community_hub_service` / `lab_service` pattern: a single
`build_home_context(profile)` entry point delegating to one helper per zone, each wrapped
so a broken zone degrades to a missing section rather than a 500.

Zones:
- **hero** -- the Pursuer identity, reused verbatim from the Lab (`lab_service`).
- **glances** -- the thin status row: pending rewards (count), badges closest to their next
  tier, and the headline trophy numbers.
- **recent** -- a small recent-earnings strip.
- **launchers** -- cards into the functional pages (Lab, Collection, Research, ...).

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
    contract_service, dashboard_service, lab_service, milestone_service, pursuer_card_service,
)

logger = logging.getLogger(__name__)


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


def _unique_series(badges):
    """Keep one entry per badge series -- the closest-to-next-tier one. `badges` arrives
    sorted by completion desc, so the first occurrence of each series is its nearest tier."""
    seen, out = set(), []
    for b in badges:
        slug = b.get('series_slug')
        if slug in seen:
            continue
        seen.add(slug)
        out.append(b)
    return out


def _build_glances(profile):
    """The thin status row -- each a cheap read (a bounded summary, a few rows, denormalized
    fields): pending contract rewards (count + total XP waiting + a peek; the claim itself lives
    on the Research Panel), the badges closest to their next tier, and the headline trophy snapshot."""
    return {
        'claimable': _safe(
            'claimable', profile,
            lambda: contract_service.claimable_summary(profile),
            {'count': 0, 'total_xp': 0, 'items': [], 'more': 0}),
        'almost_badges': _safe(
            'almost_badges', profile,
            lambda: _unique_series(dashboard_service.provide_badge_progress(profile, {'limit': 12})
                                   .get('badges_in_progress', []))[:3], []),
        'milestones': _safe(
            'milestones', profile,
            lambda: milestone_service.earned_summary(profile),
            {'earned': 0, 'total': 0}),
        'snapshot': _safe(
            'snapshot', profile,
            lambda: dashboard_service.provide_trophy_snapshot(profile), None),
    }


# (url_name, label, icon, description) for the launcher cards -- the page's real job: getting
# the Pursuer to where the functionality lives. Icons mirror the My Pursuit sub-nav.
_LAUNCHERS = [
    ('lab',             'The Lab',        'flask',  'Your elements and Platinum DNA'),
    ('badge_collection', 'Collection',    'award',  'Your badge binder'),
    ('research_panel',  'Research Panel', 'beaker', 'Projects to pursue and rewards to claim'),
    ('milestones_list', 'Milestones',     'flag',   'Career milestones'),
    ('my_titles',       'Titles',         'crown',  'Earned and equipped titles'),
]


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


def _build_launchers(profile, hero, glances, elements):
    """Navigator tiles into the functional pages, each carrying a live glance-stat drawn from the
    already-built glances (no extra queries here): the Lab shows your strongest element, the Research
    Panel the XP waiting to claim, Collection your closest badge, Milestones how many you've earned,
    Titles the equipped title. A route that doesn't resolve is dropped."""
    level = (hero or {}).get('pursuer_level')
    top_el = (elements or [None])[0]
    claim = (glances or {}).get('claimable') or {}
    almost = ((glances or {}).get('almost_badges') or [None])[0]
    ms = (glances or {}).get('milestones') or {}
    stats = {
        'lab': (f"{top_el['name']} · Lv {top_el['level']}"
                if top_el and top_el.get('name') and top_el.get('level')
                else (f"Level {level}" if level else None)),
        'research_panel': f"{claim.get('total_xp'):,} XP to claim" if claim.get('total_xp') else None,
        'badge_collection': (f"{almost['completed']}/{almost['required']} · {almost['tier_name']}"
                             if almost else None),
        'milestones_list': f"{ms['earned']}/{ms['total']} earned" if ms.get('total') else None,
        'my_titles': (hero or {}).get('active_title'),
    }
    launchers = []
    for url_name, label, icon, desc in _LAUNCHERS:
        try:
            url = reverse(url_name)
        except NoReverseMatch:
            continue
        launchers.append({
            'url': url, 'label': label, 'icon': icon,
            'desc': desc, 'stat': stats.get(url_name),
        })
    return launchers


def _build_elements(lab):
    """A compact, strongest-first strip of the Pursuer's elements (symbol + level + family),
    flattened from the Lab build the hero already computed -- no extra query."""
    if not lab:
        return []
    tiles = [t for d in lab.get('disciplines', []) for t in d.get('jobs', [])]
    tiles.sort(key=lambda t: (-t.get('level', 0), t.get('name', '')))
    return [
        {'symbol': t.get('symbol'), 'level': t.get('level'), 'disc_slug': t.get('disc_slug'),
         'name': t.get('name'), 'shape': t.get('shape')}
        for t in tiles
    ]


def build_home_context(profile):
    """Assemble the synced Home context for `profile`. Each zone is isolated so a single
    failure degrades to a missing section rather than a 500."""
    # One Lab build feeds both the identity hero and the navigator's Lab stat (no double work).
    lab_ctx = _safe('lab', profile, lambda: lab_service.build_lab_context(profile), {})
    hero = (lab_ctx or {}).get('hero')
    elements = _build_elements((lab_ctx or {}).get('lab'))
    glances = _build_glances(profile)
    return {
        'hero': hero,
        # The identity signature; reuses the already-built Lab context (no second build).
        'pursuer_card': _safe('pursuer_card', profile,
                              lambda: pursuer_card_service.build_pursuer_card(profile, lab_ctx=lab_ctx), None),
        'glances': glances,
        'sync': _safe('sync', profile, lambda: _build_sync(profile), None),
        'community': _safe('community', profile, lambda: _build_community(get_cached_heartbeat()), None),
        'recent': _safe(
            'recent', profile,
            lambda: dashboard_service.provide_recent_platinums(profile, {'limit': RECENT_LIMIT})
            .get('platinums', []), []),
        'launchers': _build_launchers(profile, hero, glances, elements),
        # The trophy-snapshot card bridges gamification-first home -> trophy-data profile.
        'profile_url': _safe(
            'profile_url', profile,
            lambda: reverse('profile_detail', args=[profile.psn_username]), None),
    }
