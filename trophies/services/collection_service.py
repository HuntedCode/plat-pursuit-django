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


def _live_badges():
    """All live badges with every FK build_badge_frame touches select_related, so the
    batched frame build issues zero per-badge FK queries."""
    return list(
        Badge.objects.filter(is_live=True).select_related(
            'base_badge', 'franchise', 'collection', 'developer', 'funded_by',
            'base_badge__franchise', 'base_badge__collection',
            'base_badge__developer', 'base_badge__funded_by',
        )
    )


def _build_pages(profile):
    badges = _live_badges()
    if not badges:
        return [], {'total': 0, 'earned': 0, 'pct': 0, 'by_tier': {}}

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

    pages, page_no, palette_i = [], 0, 0
    for btype in ordered_types:
        section = sorted(
            by_type[btype],
            key=lambda b: ((b.effective_display_series or b.series_slug or '').lower(), b.tier),
        )
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
            # Use the badge id (globally unique) for the DOM anchor, since set_number is now
            # only unique WITHIN a badge type (Series #1 and Franchise #1 both exist).
            frame['dom_id'] = f"card-{b.id}"
            frames.append(frame)

        # A new badge type always starts a fresh page so the page tab labels one section.
        for start in range(0, len(frames), PAGE_SIZE):
            page_no += 1
            pages.append({
                'number': page_no,
                'theme': label,
                'palette': palette,
                'frames': frames[start:start + PAGE_SIZE],
            })

    earned_ids = set(earned_map.keys())
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
    }
    return pages, summary


def build_collection_context(profile):
    """Assemble the Collection album context. Read-only + whale-safe (see module docstring)."""
    context = {'pages': [], 'summary': {'total': 0, 'earned': 0, 'pct': 0, 'by_tier': {}}}
    try:
        pages, summary = _build_pages(profile)
        context['pages'] = pages
        context['summary'] = summary
        context['total_pages'] = len(pages)
    except Exception:
        logger.exception("Collection album build failed for profile %s", getattr(profile, 'id', '?'))
        context['total_pages'] = 0
    return context
