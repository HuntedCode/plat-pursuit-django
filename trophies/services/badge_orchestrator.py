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

from django.db import transaction
from django.db.models import Prefetch, Q, QuerySet

from trophies.models import (
    Stage, Concept, Game, ConceptBundle, ProfileGame, ProfileTrophyGroup, GroupBadge, TrophyGroup,
)
from trophies.services.badge_engine import (
    GameState, StageInput, GroupInput, SeriesInput, evaluate_group_badge, evaluate_stage,
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

    # THE SAME TWO ID SETS AS SUBQUERIES, for the per-profile reads only.
    #
    # THE INVARIANT: a per-profile query's SQL TEXT must be constant-size. It was not. The two reads in
    # `evaluate_with_catalog` passed the Python collections above straight into `__in`, which Django
    # renders as one placeholder per element -- so every one of ~300,000 profiles in a nightly
    # `evaluate_badges --all` re-sent a statement carrying the entire live badge catalogue's game ids.
    # Django disables prepared statements (`prepare_threshold=None`), so each of those was parsed and
    # planned from scratch: a ~140 KB statement, twice per profile, ~600,000 times.
    #
    # The Python `game_ids` set stays -- it is catalogue-bounded, built once, and the in-memory
    # `game_state` lookups need it. Only the FILTER ARGUMENT moves server-side.
    in_series = (Q(concept__stages__series_slug__in=series_slugs)
                 | Q(concept__bundles__stage__series_slug__in=series_slugs))
    game_ids_qs = Game.objects.filter(in_series).values('id')
    default_tg_qs = TrophyGroup.objects.filter(
        game_id__in=game_ids_qs, trophy_group_id='default',
    ).values('id')

    return {'group_badges': group_badges, 'stages': stages, 'game_ids': game_ids,
            'default_tg_ids': default_tg_ids,
            'game_ids_qs': game_ids_qs, 'default_tg_qs': default_tg_qs}


def recompute_required_stages(catalog) -> int:
    """Write `GroupBadge.required_stages` from the catalog. Returns the number of rows changed.

    WHY THIS EXISTS: the column's help_text promised a recompute and nothing ever performed one, so every
    row sat at its `default=0` from the day the model was created. `badge_list_service` reads it as
    `stages_total`, and the medallion renders its "X / Y" count behind `{% if total %}` -- so a zero does
    not show as "0 / 0", it removes the count from every card on Browse Badges and the Series view. It read
    as a design choice rather than a bug, which is why it survived the rebuild. Badge DETAIL was unaffected
    because it takes `result.gating_count` from a live evaluation, and its `or stage_count` fallback masked
    the dead column there too.

    WHY IT CAN BE COMPUTED WITHOUT A PROFILE: gating is `_qualifies` (platform overlap) and `_gates`
    (obtainable, and delisted vs the group's exclude_delisted) -- catalog facts only. Nothing in the gating
    decision reads completion, so the stand-in GameState below passes `base_complete=False,
    full_complete=False` and the count is identical for every hunter. That is also why this is NOT written
    from `apply_changes`: that step only visits badges whose HELD state changed, which would leave every
    unchanged badge at zero forever.

    WHY PER GROUP and not a `Stage` count per series: a stage stops gating on an edition where its only
    games are delisted (and the group excludes delisted) or unobtainable. Counting `Stage` rows would
    over-report on exactly the editions where the difference is visible.
    """
    counts = {}
    stages_by_series = defaultdict(list)
    for st in catalog['stages']:
        units = []
        for c in st.concepts.all():
            units.extend(_catalog_game_state(g) for g in c.games.all())
        for b in st.concept_bundles.all():
            bundle = _bundle_state(b, _catalog_game_state)
            if bundle is not None:
                units.append(bundle)
        stages_by_series[st.series_slug].append(StageInput(st.stage_number, tuple(units)))

    for gb in catalog['group_badges']:
        group_input = GroupInput(frozenset(gb.platform_group.platforms), gb.platform_group.exclude_delisted)
        stages = stages_by_series.get(gb.series.series_slug, [])
        results = [evaluate_stage(s, group_input) for s in stages if s.stage_number > 0]
        counts[gb.id] = sum(1 for r in results if r.gates)

    stale = [gb for gb in catalog['group_badges'] if gb.required_stages != counts[gb.id]]
    for gb in stale:
        gb.required_stages = counts[gb.id]
    if stale:
        # Atomic: batch_size splits this into several statements, and a failure part-way through would
        # otherwise leave the catalogue half-updated -- some cards showing a real count, others still 0.
        with transaction.atomic():
            GroupBadge.objects.bulk_update(stale, ['required_stages'], batch_size=500)
    return len(stale)


def _catalog_game_state(g):
    """A GameState carrying only the catalog facts gating reads. Completion is stubbed False because
    `_gates`/`_qualifies` never look at it -- see recompute_required_stages."""
    return GameState(
        game_id=g.id,
        platforms=frozenset(g.title_platform or []),
        is_obtainable=g.is_obtainable,
        is_delisted=g.is_delisted,
        base_complete=False,
        full_complete=False,
        completion_date=None,
    )


def evaluate_with_catalog(profile, catalog):
    """Evaluate one profile against a PRE-BUILT catalog. The ONLY per-profile DB work is the two completion
    reads, both bounded to catalog games (whale-safe). Returns {group_badge_id: GroupBadgeResult}."""
    # SUBQUERIES, not the Python id collections -- see the note in build_catalog. These two statements
    # run once per profile across the whole userbase, so their SQL text has to be constant-size.
    full_map = {
        gid: (prog, last)
        for gid, prog, last in ProfileGame.objects.filter(
            profile=profile, game_id__in=catalog['game_ids_qs'],
        ).values_list('game_id', 'progress', 'most_recent_trophy_date')
    }
    # Still small-side-first: the subquery resolves the default trophy groups, and the outer filter is
    # a bounded seek on PTG (profile, trophy_group).
    base_map = {
        gid: (prog, last)
        for gid, prog, last in ProfileTrophyGroup.objects.filter(
            profile=profile, trophy_group_id__in=catalog['default_tg_qs'],
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
