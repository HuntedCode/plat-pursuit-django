"""Badge evaluation ORCHESTRATOR (rebuild).

The DB-reading half of the engine. It's split so a BATCH (many profiles, same badges) fetches the immutable
catalog ONCE and only re-reads the two per-profile completion signals per profile:

  build_catalog(group_badges)        -> the stage/game graph + default trophy-group ids (identical per profile)
  evaluate_with_catalog(profile, c)  -> the two bounded completion reads + the pure engine, for one profile
  evaluate_profile(profile, badges)  -> single-profile convenience (build_catalog + evaluate_with_catalog)

Completion signals (both denormalized + indexed):
  base_complete <- ProfileTrophyGroup.progress == 100 on the game's DEFAULT trophy group.
  full_complete <- ProfileGame.progress == 100 (whole game incl DLC).
The base read is driven from the small catalog side (pre-resolved default trophy-group ids) so it's a bounded
index seek even for a whale with 250k ProfileTrophyGroups.
"""
from collections import defaultdict

from django.db.models import Prefetch, QuerySet

from trophies.models import (
    Stage, Concept, Game, ConceptBundle, ProfileGame, ProfileTrophyGroup, GroupBadge, TrophyGroup,
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


def resolve_group_badges(group_badges):
    """Normalize the group_badges arg to a list (None -> all live; queryset -> select_related). Callers resolve
    exactly ONCE and thread the list through (build_catalog / batch)."""
    if group_badges is None:
        group_badges = GroupBadge.objects.filter(is_live=True)
    if isinstance(group_badges, QuerySet):
        group_badges = group_badges.select_related('series', 'platform_group')
    return list(group_badges)


def build_catalog(group_badges):
    """Fetch the IMMUTABLE catalog once for a resolved list of group badges (identical across all profiles):
    the stage graph, the referenced game-id set, and the default trophy-group ids. A batch calls this ONCE, so
    the ~6 catalog prefetch queries happen a single time instead of per profile."""
    series_slugs = {gb.series.series_slug for gb in group_badges}
    stages = list(
        Stage.objects.filter(series_slug__in=series_slugs).prefetch_related(
            _game_prefetch(),
            Prefetch('concept_bundles', queryset=ConceptBundle.objects.prefetch_related(_game_prefetch())),
        )
    )
    game_ids = set()
    for st in stages:
        for c in st.concepts.all():
            game_ids.update(g.id for g in c.games.all())
        for b in st.concept_bundles.all():
            for c in b.concepts.all():
                game_ids.update(g.id for g in c.games.all())
    # Pre-resolve the default trophy-group ids so the per-profile base read is a single-hop seek on the
    # ProfileTrophyGroup unique index (small-side-first -> whale-safe regardless of the planner).
    default_tg_ids = list(
        TrophyGroup.objects.filter(game_id__in=game_ids, trophy_group_id='default').values_list('id', flat=True)
    )
    return {'group_badges': group_badges, 'stages': stages, 'game_ids': game_ids, 'default_tg_ids': default_tg_ids}


def evaluate_with_catalog(profile, catalog):
    """Evaluate one profile against a PRE-BUILT catalog. The ONLY per-profile DB work is the two completion
    reads, both bounded to catalog games (whale-safe). Returns {group_badge_id: GroupBadgeResult}."""
    game_ids = catalog['game_ids']
    full_map = {
        gid: (prog, last)
        for gid, prog, last in ProfileGame.objects.filter(
            profile=profile, game_id__in=game_ids,
        ).values_list('game_id', 'progress', 'most_recent_trophy_date')
    }
    # Filter on the pre-resolved default trophy-group ids: a bounded seek on PTG (profile, trophy_group).
    base_map = {
        gid: (prog, last)
        for gid, prog, last in ProfileTrophyGroup.objects.filter(
            profile=profile, trophy_group_id__in=catalog['default_tg_ids'],
        ).values_list('trophy_group__game_id', 'progress', 'last_trophy_at')
    }

    def game_state(g):
        base_prog, base_date = base_map.get(g.id, (0, None))
        full_prog, full_date = full_map.get(g.id, (0, None))
        full_complete = full_prog == 100
        # Invariant: 100% of the whole game implies the base list is done -- enforced here so the engine stays
        # bar-agnostic and a missing/stale default ProfileTrophyGroup row can't yield "holo without base".
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
    for st in catalog['stages']:
        units = []
        for c in st.concepts.all():
            units.extend(game_state(g) for g in c.games.all())
        for b in st.concept_bundles.all():
            bundle = _bundle_state(b, game_state)
            if bundle is not None:
                units.append(bundle)
        stages_by_series[st.series_slug].append(StageInput(st.stage_number, tuple(units)))

    results = {}
    for gb in catalog['group_badges']:
        series_input = SeriesInput(gb.series.completion_policy, gb.series.min_required)
        group_input = GroupInput(frozenset(gb.platform_group.platforms), gb.platform_group.exclude_delisted)
        results[gb.id] = evaluate_group_badge(series_input, group_input, stages_by_series.get(gb.series.series_slug, []))
    return results


def evaluate_profile(profile, group_badges=None):
    """Single-profile convenience: resolve + build_catalog + evaluate. Batch callers should build_catalog ONCE
    and call evaluate_with_catalog per profile, to avoid re-fetching the catalog."""
    group_badges = resolve_group_badges(group_badges)
    if not group_badges:
        return {}
    return evaluate_with_catalog(profile, build_catalog(group_badges))


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
