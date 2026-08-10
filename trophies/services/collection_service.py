"""Collection Gallery page context builder (grouping-badge system).

The Collection (`/my-pursuit/collection/`) is the Pursuer's badge Gallery -- a single filter / sort / search
wall of the badges they've ENGAGED with: the live group badges (editions -- Legacy HD / Ultra HD) of every
series they either HOLD an edition of (a UserGroupBadge) or have STARTED (a SeriesBadgeStanding with progress).
Earned editions gleam; in-progress ones show that EDITION's own progress; the rest are waiting mounts. Full-catalog
discovery lives on the Browse badge Gallery, not here.

Grouping-badge model: no tiers. The medallion frames reuse group_medallion_layers (so metals / avatars / holo
match the rest of the site) + group_rarity, with per-viewer state layered on: earned = held; in_progress = THIS
edition has partial progress; else unearned. Per-edition progress is READ from the materialized
SeriesBadgeStanding.group_progress read-model (written by the sync's recompute_standing), NOT live-evaluated --
so the wall stays a cheap fixed-cost read regardless of account size, and it derives state through the SAME
shared helper (badge_xp.edition_display_state) the live badge-detail view uses, so the wall and the modal can't
disagree. Both reflect the last sync. See docs/design/rebuild/badge-backend-rebuild.md. Read-only, whale-safe.
"""
import logging
from collections import defaultdict
from datetime import timedelta

from django.db.models import Count
from django.utils import timezone

from trophies.models import GroupBadge, UserGroupBadge, SeriesBadgeStanding
from trophies.services.badge_detail_service import group_medallion_layers
from trophies.services.badge_rarity import group_rarity
from trophies.services.badge_xp import edition_display_state

logger = logging.getLogger(__name__)

_RECENT_DAYS = 7   # window for the header's "+N this week" pill + the per-badge "new" ping

# Badge-type sections (for the Set filter) + the palettes that cycle per section.
_SECTION_ORDER = ['series', 'franchise', 'collection', 'megamix', 'developer', 'user', 'event']
_SECTION_LABELS = {
    'series': 'Series', 'franchise': 'Franchises', 'collection': 'Collections',
    'megamix': 'Mega Mixes', 'developer': 'Developers', 'user': 'Community', 'event': 'Events',
}
_PALETTES = ['cobalt', 'amber', 'emerald', 'violet']

# Gallery sort options (client-side, applied by collection.js). Progress-forward by default so "what am I close
# on" is the first answer. (key, label) -- order = dropdown order.
COLLECTION_SORTS = [
    ('progress', 'Closest to complete'),
    ('earned', 'Recently earned'),
    ('rarity', 'Rarest first'),
    ('series', 'Series (A-Z)'),
    ('edition', 'Edition'),
    ('set_number', 'Set order'),
]
DEFAULT_SORT = 'progress'


def _engaged_series(profile):
    """The series the viewer is engaged with: they HOLD an edition (a UserGroupBadge) or have STARTED it (a
    SeriesBadgeStanding row exists only while xp>0, i.e. real progress). ONE bounded read of the held editions
    (with every field the wall needs off them) + one of the standings -- both catalog-bounded, not
    trophy-bounded. Returns (engaged_slugs, holds, earned_at, standings) where holds = {group_badge_id: is_holo},
    earned_at = {group_badge_id: dt}, standings = {series_slug: SeriesBadgeStanding} (carries group_progress)."""
    held_rows = list(
        UserGroupBadge.objects.filter(profile=profile)
        .values_list('group_badge_id', 'is_holo', 'group_badge__series__series_slug', 'earned_at')
    )
    holds = {gid: is_holo for gid, is_holo, _slug, _ea in held_rows}
    earned_at = {gid: ea for gid, _holo, _slug, ea in held_rows}
    held_slugs = {slug for _gid, _holo, slug, _ea in held_rows}
    standings = {sb.series_slug: sb for sb in SeriesBadgeStanding.objects.filter(profile=profile)}
    return (held_slugs | set(standings)), holds, earned_at, standings


def _badge_frame(gb, holds, standings, participants):
    """A collection Gallery frame for one group badge (edition), with the viewer's PER-EDITION state layered on.
    Progress is READ from the series' materialized SeriesBadgeStanding.group_progress read-model (per-edition
    {platform_group_key: [cleared, gating]}), then run through the shared badge_xp.edition_display_state -- the
    SAME derivation the live badge-detail view uses, so the wall and the modal can't disagree:
      - held                                    -> 'earned' (holo when mastered),
      - not held, THIS edition has partial progress -> 'in_progress' + its own progress,
      - else                                    -> 'unearned' (a waiting mount -- incl. an edition the viewer has
                                                   0% on, which the series furthest-along would wrongly paint).
    Reuses group_medallion_layers (art) + group_rarity (live rarity), so it matches the browse pages exactly."""
    series, pg = gb.series, gb.platform_group
    tier, layers, is_avatar = group_medallion_layers(gb)
    held = gb.id in holds
    st = standings.get(series.series_slug)
    # THIS edition's [cleared, gating] from the read-model. Shape-guard the value: a malformed row must fall
    # back to "no progress" for THIS badge, never raise (a raise here degrades the WHOLE wall, not one cell).
    raw = st.group_progress.get(pg.key) if st else None
    cleared, gating = raw if isinstance(raw, (list, tuple)) and len(raw) == 2 else (0, 0)

    state, progress_pct = edition_display_state(held, cleared, gating)
    is_holo = bool(holds.get(gb.id)) if held else False
    # Only an in-progress edition carries the "X / Y stages" count (earned/unearned show none).
    stages_done, stages_total = (cleared, gating) if state == 'in_progress' else (0, 0)

    pct, cls = group_rarity(gb.earned_count, participants.get(series.series_slug, 0),
                            floor_pct=gb.rarity_floor_pct)  # floor -> the ratchet: a grade may rise, never fall (see services/rarity.effective_pct)
    return {
        'tier': tier,
        'state': state,
        'art_layers': layers,
        'is_avatar': is_avatar,
        'is_holographic': held and is_holo,
        'series_name': series.name,
        'franchise': series.franchise.name if series.franchise_id else None,
        'collection': series.collection.name if series.collection_id else None,
        'developer': series.developer.name if series.developer_id else None,
        'set_number': gb.set_number,
        'badge_id': gb.id,
        'dom_id': f'card-{gb.id}',
        'series_slug': series.series_slug,
        'group_key': pg.key,
        'group_name': pg.name,          # "Legacy HD" / "Ultra HD" -- the edition, for search + the caption stat
        'badge_name': pg.name,
        'progress_pct': progress_pct,
        'stages_done': stages_done,
        'stages_total': stages_total,   # medallion renders "stages_done / stages_total" below the meter when > 0
        'rarity_pct': pct or 0,
        'rarity_class': cls,
    }


def build_collection_context(profile, sort=DEFAULT_SORT):
    """Assemble the Collection Gallery context: a flat `list_badges` of the viewer's engaged editions + a
    summary (held / in-progress / per-edition composition) + the Set themes. Read-only + whale-safe: a fixed
    handful of bulk reads (held rows, standings, pursuer counts, catalog count) -- no live eval, no per-badge
    queries. Per-edition progress comes from the standings' materialized group_progress read-model.
    `sort` picks the initial dropdown value (the wall is sorted client-side); unknown values fall back."""
    if sort not in dict(COLLECTION_SORTS):
        sort = DEFAULT_SORT
    context = {
        'list_badges': [], 'themes': [],
        'summary': {'total': 0, 'catalog_total': 0, 'earned': 0, 'in_progress': 0, 'holo': 0,
                    'pct': 0, 'recent': 0, 'editions': []},
        'sort': sort, 'sort_options': COLLECTION_SORTS,
    }
    try:
        engaged, holds, earned_at, standings = _engaged_series(profile)
        if not engaged:
            return context

        group_badges = list(
            GroupBadge.objects.filter(is_live=True, series__series_slug__in=engaged)
            .select_related('series', 'series__franchise', 'series__collection', 'series__developer',
                            'series__submitted_by', 'platform_group')
            .order_by('series__name', 'platform_group__sort_order', 'id')
        )
        if not group_badges:
            return context

        # Live rarity denominator: the series' pursuer base (one indexed count over all engaged series' slugs).
        slugs = {gb.series.series_slug for gb in group_badges}
        participants = dict(
            SeriesBadgeStanding.objects.filter(series_slug__in=slugs)
            .values('series_slug').annotate(n=Count('id')).values_list('series_slug', 'n')
        )
        recent_cutoff = timezone.now() - timedelta(days=_RECENT_DAYS)

        # Palette per badge type (for the Set filter theme swatches), in section order.
        by_type = defaultdict(list)
        for gb in group_badges:
            by_type[gb.series.badge_type].append(gb)
        ordered_types = ([t for t in _SECTION_ORDER if t in by_type]
                         + [t for t in by_type if t not in _SECTION_ORDER])
        palette_of = {t: _PALETTES[i % len(_PALETTES)] for i, t in enumerate(ordered_types)}

        catalog_total = GroupBadge.objects.filter(is_live=True).count()   # the whole earnable catalog (denominator)

        list_badges, edition_counts = [], {}
        earned = in_progress = recent = holo = 0
        for gb in group_badges:
            fr = _badge_frame(gb, holds, standings, participants)
            btype = gb.series.badge_type
            fr['theme'] = _SECTION_LABELS.get(btype, btype.title())
            fr['palette'] = palette_of.get(btype, _PALETTES[0])
            ea = earned_at.get(gb.id)
            fr['earned_ts'] = int(ea.timestamp()) if ea else 0
            fr['earned_date'] = ea.strftime('%b %d, %Y') if ea else ''
            fr['is_new'] = bool(ea and ea >= recent_cutoff)
            list_badges.append(fr)

            # Edition composition: EVERY engaged edition appears (held count, 0 allowed) so the header stat
            # grid is stable -- a series you've only started still contributes its Legacy HD / Ultra HD cards.
            ec = edition_counts.get(gb.platform_group.key)
            if ec is None:
                ec = edition_counts[gb.platform_group.key] = {
                    'key': gb.platform_group.key, 'label': gb.platform_group.name,
                    'count': 0, 'sort': gb.platform_group.sort_order, 'tier': fr['tier'],
                }

            if fr['state'] == 'earned':
                earned += 1
                ec['count'] += 1
                if fr['is_new']:
                    recent += 1
                if fr['is_holographic']:
                    holo += 1
            elif fr['state'] == 'in_progress':
                in_progress += 1

        total = len(list_badges)
        themes = [{'name': _SECTION_LABELS.get(t, t.title()), 'palette': palette_of[t]} for t in ordered_types]
        context.update({
            'list_badges': list_badges,
            'themes': themes,
            'summary': {
                'total': total,                     # engaged editions (gates the header's stat grid + tally)
                'catalog_total': catalog_total,     # ALL live group badges -- the "N of M collected" denominator
                'earned': earned,
                'in_progress': in_progress,
                'holo': holo,
                'pct': round(earned / catalog_total * 100) if catalog_total else 0,
                'recent': recent,
                # Held-badge composition by EDITION (Legacy HD / Ultra HD); every engaged edition present.
                'editions': [
                    {'key': v['key'], 'label': v['label'], 'count': v['count'], 'tier': v['tier']}
                    for v in sorted(edition_counts.values(), key=lambda e: e['sort'])
                ],
            },
        })
    except Exception:
        logger.exception("Collection Gallery build failed for profile %s", getattr(profile, 'id', '?'))
    return context
