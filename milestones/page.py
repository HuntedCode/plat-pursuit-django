"""Read-side assembly for the /milestones/ page.

`build_milestones_context(profile)` turns the materialized read-models (UserMilestone progress +
EarnedMilestoneTier records + the Milestone/MilestoneTier catalogue + the cached rarity denominator) into the
template-ready display data. Whale-safe: it reads O(catalogue) rows + two per-viewer queries, and NEVER
live-evaluates a metric on the request path. `profile=None` (anon / unlinked) renders the ladders with no
progress.
"""
from .models import EarnedMilestoneTier, Milestone, UserMilestone
from .services import tier_rarity_pct, total_hunters


def build_milestones_context(profile):
    milestones = list(Milestone.objects.filter(is_active=True).prefetch_related('tiers'))
    denom = total_hunters()

    progress_by_milestone = {}
    earned_at_by_tier = {}
    if profile is not None:
        progress_by_milestone = {
            um.milestone_id: um for um in UserMilestone.objects.filter(profile=profile)
        }
        earned_at_by_tier = dict(
            EarnedMilestoneTier.objects.filter(profile=profile).values_list('tier_id', 'earned_at')
        )

    cards = []
    total_tiers = total_earned = started = 0

    for milestone in milestones:
        tiers = sorted(milestone.tiers.all(), key=lambda t: t.index)
        if not tiers:
            continue

        progress = progress_by_milestone.get(milestone.id)
        value = progress.current_value if progress else 0
        # Earned rungs are contiguous 1..highest (ascending thresholds + ratcheting highest_tier_index).
        highest = progress.highest_tier_index if progress else 0
        maxed = highest >= len(tiers)

        tier_rows = [{
            'index': t.index,
            'threshold': t.threshold,
            'name': t.name,
            'earned': t.index <= highest,
            'earned_at': earned_at_by_tier.get(t.id),
            'rarity_pct': tier_rarity_pct(t.earned_count, denom),
            'is_next': (not maxed) and t.index == highest + 1,
        } for t in tiers]

        next_row = None if maxed else tier_rows[highest]     # tier_rows is 0-based -> position `highest` = next rung
        if next_row is not None:
            prev_threshold = tier_rows[highest - 1]['threshold'] if highest > 0 else 0
            span = next_row['threshold'] - prev_threshold
            progress_pct = round(min(max((value - prev_threshold) / span, 0), 1) * 100) if span > 0 else 0
        else:
            progress_pct = 100

        cards.append({
            'slug': milestone.slug,
            'name': milestone.name,
            'description': milestone.description,
            'icon': milestone.icon,
            'category': milestone.category,
            'value': value,
            'tiers': tier_rows,
            'earned_count': highest,
            'total_tiers': len(tiers),
            'highest_earned': tier_rows[highest - 1] if highest > 0 else None,   # for the rarity/prestige line
            'next_tier': next_row,
            'next_threshold': next_row['threshold'] if next_row else None,
            'progress_pct': progress_pct,
            'maxed': maxed,
        })
        total_tiers += len(tiers)
        total_earned += highest
        started += int(highest > 0)

    return {
        'milestone_cards': cards,
        'ms_total_milestones': len(cards),
        'ms_milestones_started': started,
        'ms_total_tiers': total_tiers,
        'ms_earned_tiers': total_earned,
        'ms_total_hunters': denom,
        'ms_has_progress': profile is not None,
    }
