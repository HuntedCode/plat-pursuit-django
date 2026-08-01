"""Badge evaluation ORCHESTRATOR (rebuild).

The DB-reading half of the engine: it prefetches the badge catalog's Stages/Concepts/Games, reads the
profile's two completion signals (bounded to those games, so it stays whale-safe no matter how large the
profile's library), maps everything into the pure engine's input dataclasses, and runs the pure core per
group badge. It has NO earn logic of its own — all rules live in badge_engine (single source of truth).

Completion signals (both denormalized + indexed, so this is a couple of bounded queries, never a per-trophy
aggregation):
  base_complete <- ProfileTrophyGroup.progress == 100 on the game's DEFAULT trophy group (the base list).
  full_complete <- ProfileGame.progress == 100 (the whole game incl DLC).
  earn date      <- the default group's last_trophy_at (when the base list was finished).

Returns {group_badge_id: GroupBadgeResult} — the DesiredState the apply() step (Phase 3) will diff + write.
"""
from collections import defaultdict

from django.db.models import Prefetch, QuerySet

from trophies.models import (
    Stage, Concept, Game, ConceptBundle, ProfileGame, ProfileTrophyGroup, GroupBadge,
)
from trophies.services.badge_engine import (
    GameState, StageInput, GroupInput, SeriesInput, evaluate_group_badge,
)

_GAME_FIELDS = ('id', 'title_platform', 'is_obtainable', 'is_delisted', 'concept_id')


def _game_prefetch():
    """Prefetch a stage/bundle's concepts -> games with only the fields the engine reads."""
    return Prefetch('concepts', queryset=Concept.objects.prefetch_related(
        Prefetch('games', queryset=Game.objects.only(*_GAME_FIELDS)),
    ))


def evaluate_profile(profile, group_badges=None):
    """Evaluate group badges for a profile. `group_badges` defaults to all live ones; pass a subset (e.g. the
    series touched by a sync) to scope the work. Returns {group_badge_id: GroupBadgeResult}."""
    if group_badges is None:
        group_badges = GroupBadge.objects.filter(is_live=True)
    if isinstance(group_badges, QuerySet):
        group_badges = group_badges.select_related('series', 'platform_group')   # avoid N+1 on gb.series/group
    group_badges = list(group_badges)
    if not group_badges:
        return {}

    series_slugs = {gb.series.series_slug for gb in group_badges}

    stages = list(
        Stage.objects.filter(series_slug__in=series_slugs).prefetch_related(
            _game_prefetch(),
            Prefetch('concept_bundles', queryset=ConceptBundle.objects.prefetch_related(_game_prefetch())),
        )
    )

    # Every game the catalog references (standalone + bundle members). Bounds the per-profile reads below.
    game_ids = set()
    for st in stages:
        for c in st.concepts.all():
            game_ids.update(g.id for g in c.games.all())
        for b in st.concept_bundles.all():
            for c in b.concepts.all():
                game_ids.update(g.id for g in c.games.all())

    # Two bounded, indexed reads of the profile's completion — whale-safe (scoped to catalog games).
    full_map = {
        gid: (prog, last)
        for gid, prog, last in ProfileGame.objects.filter(
            profile=profile, game_id__in=game_ids,
        ).values_list('game_id', 'progress', 'most_recent_trophy_date')
    }
    base_map = {
        gid: (prog, last)
        for gid, prog, last in ProfileTrophyGroup.objects.filter(
            profile=profile, trophy_group__game_id__in=game_ids, trophy_group__trophy_group_id='default',
        ).values_list('trophy_group__game_id', 'progress', 'last_trophy_at')
    }

    def game_state(g):
        base_prog, base_date = base_map.get(g.id, (0, None))
        full_prog, full_date = full_map.get(g.id, (0, None))
        full_complete = full_prog == 100
        # Invariant: 100% of the whole game implies the base list is done. Enforce it HERE (the data-mapping
        # boundary) so the pure engine can stay bar-agnostic, and so a missing/stale default ProfileTrophyGroup
        # row (e.g. a sync that wrote one denorm but not the other) can't produce an impossible "holo without
        # base". Date prefers the base-group completion; falls back to the game's last trophy when inferred.
        base_complete = base_prog == 100 or full_complete
        completion_date = base_date if base_prog == 100 else (full_date if full_complete else None)
        return GameState(
            game_id=g.id,
            platforms=frozenset(g.title_platform or []),
            is_obtainable=g.is_obtainable,
            is_delisted=g.is_delisted,
            base_complete=base_complete,
            full_complete=full_complete,
            completion_date=completion_date,
        )

    stages_by_series = defaultdict(list)
    for st in stages:
        units = []
        for c in st.concepts.all():
            units.extend(game_state(g) for g in c.games.all())
        for b in st.concept_bundles.all():
            bundle = _bundle_state(b, game_state)
            if bundle is not None:
                units.append(bundle)
        stages_by_series[st.series_slug].append(StageInput(st.stage_number, tuple(units)))

    results = {}
    for gb in group_badges:
        series_input = SeriesInput(gb.series.completion_policy, gb.series.min_required)
        group_input = GroupInput(frozenset(gb.platform_group.platforms), gb.platform_group.exclude_delisted)
        results[gb.id] = evaluate_group_badge(series_input, group_input, stages_by_series.get(gb.series.series_slug, []))
    return results


def _bundle_state(bundle, game_state_fn):
    """Collapse a ConceptBundle (episodic set) into one synthetic qualifying 'game' for the engine. The bundle
    is base/full complete only when EVERY member concept has a base/full-complete game; its date is the
    'tipper' (the last member to complete). Platforms/obtainable/delisted aggregate over members so the engine
    routes + gates it like any other qualifier."""
    members = list(bundle.concepts.all())
    if not members:
        return None
    platforms, any_delisted = set(), False
    base_all, full_all, obtainable_all = True, True, True
    member_dates = []
    for c in members:
        states = [game_state_fn(g) for g in c.games.all()]
        for s in states:
            platforms |= set(s.platforms)
        if not any(s.is_obtainable for s in states):
            obtainable_all = False
        if any(s.is_delisted for s in states):
            any_delisted = True
        base_states = [s for s in states if s.base_complete]
        if base_states:
            dates = [s.completion_date for s in base_states if s.completion_date is not None]
            member_dates.append(min(dates) if dates else None)
        else:
            base_all = False
        if not any(s.full_complete for s in states):
            full_all = False
    bundle_date = None
    if base_all and member_dates and all(d is not None for d in member_dates):
        bundle_date = max(member_dates)
    return GameState(
        game_id=-bundle.id,               # synthetic; game_id is identity-only in the engine
        platforms=frozenset(platforms),
        is_obtainable=obtainable_all,
        is_delisted=any_delisted,
        base_complete=base_all,
        full_complete=full_all,
        completion_date=bundle_date,
    )
