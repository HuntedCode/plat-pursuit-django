"""Read-side assembly for the /milestones/ page.

`build_milestones_context(profile)` turns the materialized read-models (UserMilestone progress +
EarnedMilestoneTier records + the Milestone/MilestoneTier catalogue + the cached rarity denominator) into the
template-ready display data. Whale-safe: it reads O(catalogue) rows + two per-viewer queries, and NEVER
live-evaluates a metric on the request path. `profile=None` (anon / unlinked) renders the ladders with no
progress.
"""
from django.urls import NoReverseMatch, reverse

from .metrics import metric_unit
from .models import Milestone, UserMilestone
from .services import tier_rarity_pct, total_hunters

# Where each milestone points ("how do I move this?"), keyed by its metric (the stable identity). The trophy-
# facing ladders link to the viewer's own profile (their library/trophy home); the rest to their themed hub.
# `'profile'` is resolved against the viewer's psn_username. A milestone on an unmapped metric isn't a link.
# (name, label) -> resolved lazily so a renamed/removed route degrades to "no link", never a 500.
_METRIC_ACTION = {
    'lifetime_platinums': ('profile', 'View your profile'),
    'lifetime_trophies': ('profile', 'View your profile'),
    'full_completions': ('profile', 'View your profile'),
    'playtime_hours': ('profile', 'View your profile'),
    'total_badges_earned': ('badge_collection', 'View your collection'),
    'pursuer_level': ('career', 'View your career'),
    'community_months': ('support_hub', 'Visit the support hub'),
    'premium_months': ('subscription_management', 'Manage your membership'),
}


def _metric_action(metric, profile):
    """(url, label) a milestone card links to, or (None, None) when unmapped / route missing / no profile."""
    entry = _METRIC_ACTION.get(metric)
    if not entry:
        return None, None
    name, label = entry
    try:
        if name == 'profile':
            username = getattr(profile, 'psn_username', None)
            if not username:
                return None, None
            return reverse('profile_detail', kwargs={'psn_username': username}), label
        return reverse(name), label
    except NoReverseMatch:
        return None, None


def build_milestones_context(profile):
    milestones = list(Milestone.objects.filter(is_active=True).prefetch_related('tiers'))
    denom = total_hunters()

    progress_by_milestone = {}
    if profile is not None:
        progress_by_milestone = {
            um.milestone_id: um for um in UserMilestone.objects.filter(profile=profile)
        }

    cards = []
    total_tiers = total_earned = started = 0

    for milestone in milestones:
        tiers = sorted(milestone.tiers.all(), key=lambda t: t.index)
        if not tiers:
            continue

        progress = progress_by_milestone.get(milestone.id)
        value = progress.current_value if progress else 0
        # Earned rungs are contiguous 1..highest (ascending thresholds + ratcheting highest_tier_index).
        # Clamp to the current ladder length so a manually-shrunk ladder (a stale index that outruns the
        # rungs before the next recompute rewrites it) can never index past tier_rows.
        highest = min(progress.highest_tier_index if progress else 0, len(tiers))
        maxed = highest >= len(tiers)

        tier_rows = [{
            'index': t.index,
            'threshold': t.threshold,
            'name': t.name,
            'earned': t.index <= highest,
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

        action_url, action_label = _metric_action(milestone.metric, profile)
        cards.append({
            'slug': milestone.slug,
            'name': milestone.name,
            'description': milestone.description,
            'icon': milestone.icon,
            'accent': milestone.accent,
            'category': milestone.category,
            'value': value,
            'unit': metric_unit(milestone.metric),
            'tiers': tier_rows,
            'earned_count': highest,
            'total_tiers': len(tiers),
            'highest_earned': tier_rows[highest - 1] if highest > 0 else None,   # for the rarity/prestige line
            'next_tier': next_row,
            'next_threshold': next_row['threshold'] if next_row else None,
            'progress_pct': progress_pct,
            'maxed': maxed,
            'action_url': action_url,
            'action_label': action_label,
        })
        total_tiers += len(tiers)
        total_earned += highest
        started += int(highest > 0)

    nearest, rarest = _spotlights(cards) if profile is not None else (None, None)

    return {
        'milestone_cards': cards,
        'ms_nearest': nearest,
        'ms_rarest': rarest,
        'ms_total_milestones': len(cards),
        'ms_milestones_started': started,
        'ms_total_tiers': total_tiers,
        'ms_earned_tiers': total_earned,
        'ms_total_hunters': denom,
        'ms_has_progress': profile is not None,
    }


def _spotlights(cards):
    """The two header hooks, derived from the already-assembled cards (no extra queries, whale-safe):

    - nearest: the non-maxed ladder closest to its next rung -- furthest-along first (progress_pct),
      ties broken by fewest units remaining. The page's forward pull.
    - rarest:  the rarest tier already earned across every ladder (smallest "% of hunters"). The brag.
    """
    nearest = None
    non_maxed = [c for c in cards if not c['maxed']]
    if non_maxed:
        c = min(non_maxed, key=lambda c: (-c['progress_pct'], c['next_threshold'] - c['value']))
        nearest = {
            'slug': c['slug'], 'name': c['name'], 'icon': c['icon'], 'accent': c['accent'],
            'unit': c['unit'], 'remaining': max(c['next_threshold'] - c['value'], 0),
            'progress_pct': c['progress_pct'], 'action_url': c['action_url'], 'action_label': c['action_label'],
        }

    rarest = None
    earned = [(c, t) for c in cards for t in c['tiers'] if t['earned'] and t['rarity_pct'] is not None]
    if earned:
        c, t = min(earned, key=lambda ct: ct[1]['rarity_pct'])
        rarest = {
            'slug': c['slug'], 'name': c['name'], 'icon': c['icon'], 'accent': c['accent'],
            'threshold': t['threshold'], 'rarity_pct': t['rarity_pct'],
            'action_url': c['action_url'], 'action_label': c['action_label'],
        }

    return nearest, rarest
