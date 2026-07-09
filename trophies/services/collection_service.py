"""Collection album page context builder.

The Collection (`/my-pursuit/collection/`) is the Pursuer's badge album -- the product
mount of the **Binder Surface** (see docs/design/binder-surface.md). It shows the FULL set
of live badges as framed trading cards: earned ones framed, unearned shown as named slots
(the collector's pull). Grouped into binder pages by badge type.

Whale-safe: the per-viewer UserBadge / UserBadgeProgress reads are bulk-fetched ONCE and
passed into `build_badge_frame(..., earned=, progress=, include_live_stats=False)`, so
building hundreds of frames issues no per-badge queries/Redis (the Frame docstring's
prescribed batch path). Read-only.
"""
import logging
from collections import defaultdict

from trophies.models import Badge, UserBadge, UserBadgeProgress
from trophies.services.frame_service import build_badge_frame
from trophies.services.redis_leaderboard_service import get_earners_ranks

logger = logging.getLogger(__name__)

# Section order (badge types) + the binder page palettes that cycle per section. Each badge
# type is its own "set" (its own page run + independent set numbering).
_SECTION_ORDER = ['series', 'franchise', 'collection', 'megamix', 'developer', 'user', 'event']
_SECTION_LABELS = {
    'series': 'Series', 'franchise': 'Franchises', 'collection': 'Collections',
    'megamix': 'Mega Mixes', 'developer': 'Developers', 'user': 'Community', 'event': 'Events',
}
_PALETTES = ['cobalt', 'amber', 'emerald', 'violet']
_TIER_NAME = {1: 'bronze', 2: 'silver', 3: 'gold', 4: 'platinum'}
PAGE_SIZE = 16  # frames per binder page = 4 series x 4 tiers (a new badge type starts a fresh page)

# Binder sort options (applied WITHIN each set). Both keep a series' four tiers
# contiguous so a series still reads as one row. (key, label) -- order = dropdown order.
COLLECTION_SORTS = [
    ('set_number', 'Set number'),
    ('series', 'Series name'),
]
DEFAULT_SORT = 'set_number'


def _sort_key(sort):
    if sort == 'series':
        return lambda b: ((b.effective_display_series or b.series_slug or '').lower(), b.tier)
    # set_number: the admin-assigned edition order (a series is a contiguous block of 4,
    # blocks run in numbering order). Not-yet-numbered badges fall to the end (alpha, tier).
    return lambda b: (
        b.set_number is None, b.set_number or 0,
        (b.effective_display_series or b.series_slug or '').lower(), b.tier,
    )


def _live_badges():
    """All live badges with every FK build_badge_frame touches select_related, so the
    batched frame build issues zero per-badge FK queries."""
    return list(
        Badge.objects.filter(is_live=True).select_related(
            'base_badge', 'franchise', 'collection', 'developer', 'funded_by', 'submitted_by',
            'base_badge__franchise', 'base_badge__collection',
            'base_badge__developer', 'base_badge__funded_by', 'base_badge__submitted_by',
        )
    )


def _build_sets(profile, sort=DEFAULT_SORT):
    """Group live badges into binder SETS (one per badge type). Each set is its own
    sub-binder: its pages are numbered within the set, and the page (Series / Developers /
    ...) is selected as a distinct binder view on the page. Returns (binder_sets, summary).
    """
    badges = _live_badges()
    if not badges:
        return [], {'total': 0, 'earned': 0, 'pct': 0, 'by_tier': {}, 'tiers': []}

    # Bulk per-viewer state (one query each), keyed by badge id.
    badge_ids = [b.id for b in badges]
    earned_map = {
        ub.badge_id: ub
        for ub in UserBadge.objects.filter(profile=profile, badge_id__in=badge_ids)
    }
    progress_map = {
        pr.badge_id: pr
        for pr in UserBadgeProgress.objects.filter(profile=profile, badge_id__in=badge_ids)
    }

    # Back-of-card live stats, batched once (not per badge): series XP from the denormalized
    # ProfileGamification.series_badge_xp (one read), and current earners rank from a single
    # pipelined Redis round-trip over the EARNED series only.
    gam = getattr(profile, 'gamification', None)
    series_xp_map = (getattr(gam, 'series_badge_xp', None) or {}) if gam else {}
    earned_series = {b.series_slug for b in badges if b.id in earned_map and b.series_slug}
    rank_map = get_earners_ranks(earned_series, profile.id)

    by_type = defaultdict(list)
    for b in badges:
        by_type[b.badge_type].append(b)
    ordered_types = (
        [t for t in _SECTION_ORDER if t in by_type]
        + [t for t in by_type if t not in _SECTION_ORDER]
    )

    earned_ids = set(earned_map.keys())
    sort_key = _sort_key(sort)
    binder_sets, palette_i = [], 0
    for btype in ordered_types:
        section = sorted(by_type[btype], key=sort_key)
        palette = _PALETTES[palette_i % len(_PALETTES)]
        palette_i += 1
        label = _SECTION_LABELS.get(btype, btype.title())

        frames = []
        for b in section:
            frame = build_badge_frame(
                b, profile,
                earned=earned_map.get(b.id), progress=progress_map.get(b.id),
                include_live_stats=False, allow_flip=True,
                current_rank=rank_map.get(b.series_slug), series_xp=series_xp_map.get(b.series_slug),
            )
            # Use the badge id (globally unique) for the binder card's DOM anchor, since
            # set_number is now only unique WITHIN a badge type (Series #1 and Franchise #1
            # both exist). The list "View ->" deep-links to it. series_slug powers the
            # per-series badge-detail link in both views.
            frame['dom_id'] = f"card-{b.id}"
            frame['series_slug'] = b.series_slug
            frame['badge_id'] = b.id   # the collection detail modal fetches by id
            # Epoch of the earn (0 when not held) -- powers the Gallery's "Recently earned" sort.
            ub = earned_map.get(b.id)
            frame['earned_ts'] = int(ub.earned_at.timestamp()) if ub and ub.earned_at else 0
            frames.append(frame)

        # Each set is its own binder view; pages are numbered WITHIN the set.
        pages = [
            {'number': i, 'frames': frames[start:start + PAGE_SIZE]}
            for i, start in enumerate(range(0, len(frames), PAGE_SIZE), start=1)
        ]
        # Spreads pair facing pages for the flipbook (desktop) view: left + right,
        # with an empty right on an odd final page. Single view uses `pages` directly.
        spreads = [
            {'number': i // 2 + 1, 'left': pages[i], 'right': pages[i + 1] if i + 1 < len(pages) else None}
            for i in range(0, len(pages), 2)
        ]
        # Series groups: the 4 tiers (bronze -> platinum) of a series stay together as one unit, never
        # split across a row. Frames are already sorted series-then-tier, and series_slug groups the tiers.
        groups = []
        for fr in frames:
            slug = fr.get('series_slug') or ''
            if slug and groups and groups[-1]['slug'] == slug:
                groups[-1]['tiers'].append(fr)
            else:
                groups.append({'name': fr.get('series_name'), 'slug': slug, 'tiers': [fr]})
        set_total = len(section)
        set_earned = sum(1 for b in section if b.id in earned_ids)
        # Per-series progress for the group headers (a series = 4 tiers, bronze -> platinum). "Held" =
        # earned or maintenance (you earned it, it may have lapsed) so the completion ring counts it.
        for g in groups:
            g['total'] = len(g['tiers'])
            g['earned'] = sum(1 for t in g['tiers'] if t.get('state') in ('earned', 'maintenance'))
            g['complete'] = g['total'] > 0 and g['earned'] >= g['total']
            # Aspirational "next": glow the next rung to climb -- the first tier you don't yet HOLD,
            # whether you've started it (in_progress) or not (unearned). A fully-held series has none.
            # Tiers are bronze -> platinum order, so the first non-held one is the lowest rung left.
            for t in g['tiers']:
                if t.get('state') not in ('earned', 'maintenance'):
                    t['is_next'] = True
                    break
        binder_sets.append({
            'key': btype,
            'label': label,
            'palette': palette,
            'groups': groups,
            'pages': pages,
            'spreads': spreads,
            'total': set_total,
            'earned': set_earned,
            'pct': round(set_earned / set_total * 100) if set_total else 0,
            'complete': set_total > 0 and set_earned >= set_total,
        })

    by_tier = defaultdict(int)
    for b in badges:
        if b.id in earned_ids:
            by_tier[_TIER_NAME.get(b.tier, 'gold')] += 1
    earned = len(earned_ids)
    total = len(badges)
    summary = {
        'total': total,
        'earned': earned,
        'pct': round(earned / total * 100) if total else 0,
        'by_tier': dict(by_tier),
        # Ordered bronze->platinum breakdown for the header's at-a-glance composition row.
        'tiers': [
            {'key': name, 'label': name.title(), 'count': by_tier.get(name, 0)}
            for name in ('bronze', 'silver', 'gold', 'platinum')
        ],
    }
    return binder_sets, summary


def _flatten_for_list(binder_sets):
    """Flatten the binder sets into the row list + theme set the sibling list view needs.

    Same data, different presentation (the Binder is the display piece, the list is the
    hunting tool). Each row carries its set's label/palette, the frame's binder `dom_id`
    (the list "View ->" deep-links to it), and `series_slug` for the detail link.
    """
    list_badges, themes = [], {}
    for s in binder_sets:
        themes.setdefault(s['label'], s['palette'])
        for page in s['pages']:
            for frame in page['frames']:
                # series_slug already rides along on the frame (set in _build_sets).
                list_badges.append({**frame, 'theme': s['label'], 'palette': s['palette']})
    return list_badges, [{'name': name, 'palette': palette} for name, palette in themes.items()]


def build_collection_context(profile, sort=DEFAULT_SORT):
    """Assemble the Collection album context. Read-only + whale-safe (see module docstring).

    `sort` orders the badges within each set; unknown values fall back to the default.
    """
    if sort not in dict(COLLECTION_SORTS):
        sort = DEFAULT_SORT
    context = {
        'binder_sets': [], 'summary': {'total': 0, 'earned': 0, 'pct': 0, 'by_tier': {}, 'tiers': []},
        'list_badges': [], 'themes': [], 'total_pages': 0,
        'sort': sort, 'sort_options': COLLECTION_SORTS,
    }
    try:
        binder_sets, summary = _build_sets(profile, sort)
        context['binder_sets'] = binder_sets
        context['summary'] = summary
        context['total_pages'] = sum(len(s['pages']) for s in binder_sets)
        context['list_badges'], context['themes'] = _flatten_for_list(binder_sets)
    except Exception:
        logger.exception("Collection album build failed for profile %s", getattr(profile, 'id', '?'))
    return context
