"""Badge evaluation ORCHESTRATOR (rebuild).

The DB-reading half of the engine. It's split so a BATCH (many profiles, same badges) fetches the immutable
catalog ONCE and only re-reads the two per-profile completion signals per profile:

  build_catalog(group_badges)        -> the stage/game graph + default trophy-group ids (identical per profile)
  evaluate_with_catalog(profile, c)  -> the two bounded completion reads + the pure engine, for one profile
  evaluate_profile(profile, badges)  -> single-profile convenience (build_catalog + evaluate_with_catalog)

Completion signals (both denormalized + indexed):
  base_complete <- ProfileTrophyGroup.progress == 100 on the game's DEFAULT trophy group.
  full_complete <- ProfileGame.progress == 100 (whole game incl DLC).
A TROPHY-GROUP member (`Stage.trophy_groups`) reads its own group's ProfileTrophyGroup for both bars instead.
The group read is driven from the small catalog side (pre-resolved trophy-group ids) so it's a bounded index
seek even for a whale with 250k ProfileTrophyGroups.
EXCUSED TROPHIES (`Trophy.is_unobtainable`) can also meet either bar: every trophy NOT excused earned counts as
finishing the list, read by trophies/services/excused_trophies.py only for flagged lists the hunter has not
already finished.
"""
from collections import defaultdict

from django.db import transaction
from django.db.models import Prefetch, Q, QuerySet

from trophies.models import (
    Stage, Concept, ConceptTrophyGroup, Game, ConceptBundle, ProfileGame, ProfileTrophyGroup, GroupBadge,
    TrophyGroup,
)
from trophies.services.badge_engine import (
    GameState, StageInput, GroupInput, SeriesInput, evaluate_group_badge, evaluate_stage,
)
from trophies.services.excused_trophies import BASE_GROUP, ExcusedProgress, candidates, excused_lists
from trophies.services.stage_membership import in_series_q

_GAME_FIELDS = ('id', 'title_platform', 'is_obtainable', 'is_delisted', 'concept_id')



def _game_prefetch():
    """Prefetch a stage/bundle's concepts -> games with only the fields the engine reads.

    CONCEPTS AS IDS ONLY: every reader of a catalogue (the engine, `badge_detail_service`, the Challenge
    Optimizer) reads a concept's `id` and `.games` and nothing else, and a full Concept row carries several JSON
    fields (media, descriptions, genres, title ids). Loading those for a whole catalogue on a request path is the
    memory shape behind the May 2026 OOMs."""
    return Prefetch('concepts', queryset=Concept.objects.only('id').prefetch_related(
        Prefetch('games', queryset=Game.objects.only(*_GAME_FIELDS)),
    ))


def _group_prefetch():
    """Prefetch a stage's trophy-group members -> their concept's games -> each game's trophy groups, as slim
    rows. A ConceptTrophyGroup is concept-level, so the game-level TrophyGroup (what ProfileTrophyGroup keys
    on) is found per version here; a version without that group is not a way to clear the stage. Costs one
    query when no stage has a group member: the nested levels only run for rows that exist."""
    games = Game.objects.only(*_GAME_FIELDS).prefetch_related(
        Prefetch('trophy_groups', queryset=TrophyGroup.objects.only(
            'id', 'game_id', 'trophy_group_id', 'trophy_group_name',
        )),
    )
    return Prefetch('trophy_groups', queryset=ConceptTrophyGroup.objects.only(
        'id', 'concept_id', 'trophy_group_id', 'display_name', 'icon_url',
    ).prefetch_related(
        Prefetch('concept', queryset=Concept.objects.only('id').prefetch_related(Prefetch('games', queryset=games))),
    ))


def group_members(st):
    """(concept trophy group, game, trophy group) for each version that carries one of the stage's trophy-group
    members. Reads `_group_prefetch`: no queries."""
    for ctg in st.trophy_groups.all():
        for g in ctg.concept.games.all():
            for tg in g.trophy_groups.all():
                if tg.trophy_group_id == ctg.trophy_group_id:
                    yield ctg, g, tg


def stage_units(st, game_state_fn, group_state_fn):
    """The `GameState` list the engine evaluates for one stage: every version of a whole-list member, one
    synthetic unit per ConceptBundle, and every version of a trophy-group member. The ONE place a stage is
    turned into units, so required-stage counts, a hunter's evaluation and the badge page cannot disagree."""
    units = []
    for c in st.concepts.all():
        units.extend(game_state_fn(g) for g in c.games.all())
    for b in st.concept_bundles.all():
        bundle = _bundle_state(b, game_state_fn)
        if bundle is not None:
            units.append(bundle)
    units.extend(group_state_fn(g, tg) for _, g, tg in group_members(st))
    return units


def resolve_group_badges(group_badges):
    """Normalize the group_badges arg to a list (None -> all live; queryset -> select_related). Callers resolve
    exactly ONCE and thread the list through (build_catalog / batch)."""
    if group_badges is None:
        group_badges = GroupBadge.objects.filter(is_live=True)
    if isinstance(group_badges, QuerySet):
        group_badges = group_badges.select_related('series', 'platform_group')
    return list(group_badges)


def build_catalog(group_badges, *, whole_catalogue=False):
    """Fetch the IMMUTABLE catalog once for a resolved list of group badges (identical across all profiles):
    the stage graph, the referenced game-id set, and the default trophy-group ids. A batch calls this ONCE, so
    the ~6 catalog prefetch queries happen a single time instead of per profile."""
    series_slugs = {gb.series.series_slug for gb in group_badges}
    stages = list(
        Stage.objects.filter(series_slug__in=series_slugs).prefetch_related(
            _game_prefetch(),
            Prefetch('concept_bundles', queryset=ConceptBundle.objects.prefetch_related(_game_prefetch())),
            _group_prefetch(),
        )
    )
    game_ids = set()
    group_game_ids, group_tg_ids = set(), set()
    for st in stages:
        for c in st.concepts.all():
            game_ids.update(g.id for g in c.games.all())
        for b in st.concept_bundles.all():
            for c in b.concepts.all():
                game_ids.update(g.id for g in c.games.all())
        for _, g, tg in group_members(st):
            group_game_ids.add(g.id)
            group_tg_ids.add(tg.id)
    group_tg_ids = sorted(group_tg_ids)
    # Pre-resolve the default trophy-group ids so the per-profile base read is a single-hop seek on the
    # ProfileTrophyGroup unique index (small-side-first -> whale-safe regardless of the planner). Over the
    # WHOLE-LIST games only: a list that is on a stage just for one of its groups never reads its base bar.
    default_tg_ids = list(
        TrophyGroup.objects.filter(game_id__in=game_ids, trophy_group_id='default').values_list('id', flat=True)
    )
    game_ids |= group_game_ids

    # HOW THE PER-PROFILE READS FILTER. `whole_catalogue` is the CALLER telling us which regime this
    # is, because the caller already knows and nothing else does as reliably:
    #
    #   `evaluate_badges --all` sweeps every live badge for every profile. Big catalogue, 300k profiles.
    #   Everything else is series-scoped and evaluates one profile -- `evaluate_for_touched_games` on a
    #   sync, `get_badge_detail` on the badge-page REQUEST PATH, `evaluate_badges --series`, the DLC
    #   sweep. Small catalogue, one profile.
    #
    # That split does not move as the site grows. Adding badge series makes the `--all` catalogue bigger
    # and leaves every per-series catalogue exactly where it is, because a series' size is an authoring
    # decision, not a growth curve. (This was briefly a `len(game_ids) >= THRESHOLD` heuristic with a
    # guessed constant, which is a proxy for the caller intent that was available for free.)
    #
    # NEITHER SHAPE WINS IN BOTH REGIMES, which is why it branches instead of picking one:
    #
    #   Inlining the id list sends one bound parameter per game. On `evaluate_badges --all` the
    #   catalogue is every live badge, so each of ~300,000 profiles re-sent a statement carrying the
    #   whole thing: measured ~440 ms per profile for these two reads, which is over a day of wall
    #   clock for the nightly run.
    #
    #   Filtering on a SUBQUERY makes that statement constant-size and is ~60x faster at a large
    #   catalogue. But Postgres pulls the `IN` up into a semi-join rather than hashing it once, so the
    #   subquery's whole join tree is paid per statement -- and on a SMALL catalogue against a profile
    #   with a large library that is ~12x SLOWER than inlining (measured 98 ms vs 8 ms).
    #
    # Optimising for `--all` alone slowed down every sync and every badge page view, which is how the
    # first version of this went in. The default is therefore the SMALL regime: a new caller that does
    # not think about this gets the shape that is safe on a request path.
    #
    # TROPHY-GROUP MEMBERS never enter the subquery. They are hand-authored, a handful at most, so their games
    # join the game read as an explicit id list (empty, Django drops the branch, so the measured shape above
    # is untouched until a group is authored), and their groups get a read of their own in
    # evaluate_with_catalog. Routing them through the subquery instead joined every concept's trophy groups
    # into a statement run once per profile, ~300,000 times a night.
    if whole_catalogue:
        list_games = in_series_q(series_slugs, 'concept__', lists_only=True)
        game_filter = Game.objects.filter(list_games | Q(id__in=sorted(group_game_ids))).values('id')
        tg_filter = TrophyGroup.objects.filter(
            game_id__in=Game.objects.filter(list_games).values('id'), trophy_group_id='default',
        ).values('id')
    else:
        game_filter, tg_filter = game_ids, default_tg_ids

    # `game_ids` (the Python set) stays regardless: it is catalogue-bounded, built once, and both the
    # in-memory game_state lookups and badge_detail_service read it. `group_tg_ids` is always the explicit
    # list: the group read below and the badge page's group cards both use it. `excused` is the catalogue's
    # lists with a flagged trophy, empty until staff flag one (a seek on a partial index, once per catalogue).
    return {'group_badges': group_badges, 'stages': stages, 'game_ids': game_ids, 'group_tg_ids': group_tg_ids,
            'game_filter': game_filter, 'tg_filter': tg_filter, 'uses_subquery': whole_catalogue,
            'excused': excused_lists(game_ids)}


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
        units = catalog_stage_units(st, catalog)
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


def _catalog_game_state(g, obtainable=None):
    """A GameState carrying only the catalog facts gating reads. Completion is stubbed False because
    `_gates`/`_qualifies` never look at it -- see recompute_required_stages. `obtainable` overrides the
    list's own flag (`unit_obtainable`)."""
    return GameState(
        game_id=g.id,
        platforms=frozenset(g.title_platform or []),
        is_obtainable=g.is_obtainable if obtainable is None else obtainable,
        is_delisted=g.is_delisted,
        base_complete=False,
        full_complete=False,
        completion_date=None,
    )


def unit_obtainable(g, excused, group_id=BASE_GROUP):
    """Can this unit still be finished? The list must be obtainable, AND the bar it is judged on (its base
    group, or a trophy-group member's own group) must hold a trophy that is not excused: a bar with every
    trophy excused has nothing left to earn, so it must not gate (see excused_trophies)."""
    return g.is_obtainable and not excused.dead(g.id, group_id)


def catalog_stage_units(st, catalog):
    """`stage_units` with catalog facts only, for every gating question asked without a hunter (the required
    stage count, the badge page's anonymous ladder). Reads the catalogue's excused lists, no queries."""
    excused = ExcusedProgress.read(None, catalog['excused'], [])
    return stage_units(
        st,
        lambda g: _catalog_game_state(g, unit_obtainable(g, excused)),
        lambda g, tg: _catalog_game_state(g, unit_obtainable(g, excused, tg.trophy_group_id)),
    )


def evaluate_with_catalog(profile, catalog, excused=None):
    """Evaluate one profile against a PRE-BUILT catalog. The ONLY per-profile DB work is the two completion
    reads, both bounded to catalog games (whale-safe), plus a third tiny one when a stage has a trophy-group
    member. Returns {group_badge_id: GroupBadgeResult}. `excused`: an `ExcusedProgress` the caller already read
    for this profile and catalogue (the badge page shares its own), else it is read here."""
    # `game_filter` / `tg_filter` are either the precomputed id collections or subqueries selecting the
    # same rows, chosen by the caller's regime in build_catalog -- see the note there. Only the query SHAPE
    # differs; both select identically. (On the games side "identically" means every game that can reach a
    # unit: the subquery's version list for a group member is the explicit one the small regime uses.)
    full_rows = ProfileGame.objects.filter(
        profile=profile, game_id__in=catalog['game_filter'],
    ).values_list('game_id', 'progress', 'most_recent_trophy_date', 'earned_trophies_count')
    full_map, standing = {}, {}
    for gid, prog, last, earned in full_rows:
        full_map[gid] = (prog, last)
        standing[gid] = (prog, earned)
    # Small-side-first either way: the filter resolves the DEFAULT trophy groups, so the outer read
    # stays a bounded seek on PTG (profile, trophy_group) even for a 250k-row whale.
    base_map = {
        gid: (prog, last)
        for gid, prog, last in ProfileTrophyGroup.objects.filter(
            profile=profile, trophy_group_id__in=catalog['tg_filter'],
        ).values_list('trophy_group__game_id', 'progress', 'last_trophy_at')
    }
    # The trophy-group members' own rows, keyed by trophy group. An explicit id list in both regimes; with no
    # group on any stage the list is empty and Django never sends the query.
    group_map = {
        tg_id: (prog, last)
        for tg_id, prog, last in ProfileTrophyGroup.objects.filter(
            profile=profile, trophy_group_id__in=catalog['group_tg_ids'],
        ).values_list('trophy_group_id', 'progress', 'last_trophy_at')
    }
    # Excused trophies: only a flagged list this hunter has started, not finished, and earned enough of to
    # finish can change anything, so nearly every hunter sends no query at all.
    if excused is None:
        excused = ExcusedProgress.read(profile, catalog['excused'], candidates(catalog['excused'], standing))

    def game_state(g):
        base_prog, base_date = base_map.get(g.id, (0, None))
        full_prog, full_date = full_map.get(g.id, (0, None))
        full_excused, full_excused_date = excused.full(g.id)
        base_excused, base_excused_date = excused.base(g.id)
        full_complete = full_prog == 100 or full_excused
        # Invariant: 100% of the whole game implies the base list is done -- enforced here so the engine stays
        # bar-agnostic and a missing/stale default ProfileTrophyGroup row can't yield "holo without base".
        # An excused full bar usually implies an excused base bar, but not when every base trophy is excused
        # (a dead base group): then the full bar alone carries it, and the date below must too.
        base_complete = base_prog == 100 or full_complete or base_excused
        if base_prog == 100:
            completion_date = base_date
        elif full_prog == 100:
            completion_date = full_date
        elif base_excused:
            # PSN dates every earned trophy in practice; the list's own latest is the fallback, so an excused
            # clear is never undated (an undated clear re-stamps the badge on every run).
            completion_date = base_excused_date or full_date
        elif full_excused:
            completion_date = full_excused_date or full_date
        else:
            completion_date = None
        # The full bar's own date, for mastery: the list's last trophy, or the last required one when excusal
        # carried it. Undated only when PSN sent no date at all.
        if full_prog == 100:
            full_bar_date = full_date
        elif full_excused:
            full_bar_date = full_excused_date or full_date
        else:
            full_bar_date = None
        return GameState(
            game_id=g.id,
            platforms=frozenset(g.title_platform or []),
            is_obtainable=unit_obtainable(g, excused),
            is_delisted=g.is_delisted,
            base_complete=base_complete,
            full_complete=full_complete,
            completion_date=completion_date,
            full_date=full_bar_date,
        )

    def group_state(g, tg):
        """A trophy-group member: its group at 100% is BOTH bars, because the group is the whole game it stands
        for. The rest of its list (the collection's other games, its platinum) never counts here."""
        group_prog, group_date = group_map.get(tg.id, (0, None))
        full_prog, full_date = full_map.get(g.id, (0, None))
        group_excused, group_excused_date = excused.group(g.id, tg.trophy_group_id)
        # game_state's invariant, one level down: the whole list at 100% means every group in it is too, so a
        # stale group row cannot deny credit for a finished list.
        complete = group_prog == 100 or full_prog == 100 or group_excused
        if group_prog == 100:
            completion_date = group_date
        elif full_prog == 100:
            completion_date = full_date
        elif group_excused:
            completion_date = group_excused_date or group_date or full_date
        else:
            completion_date = None
        return GameState(
            game_id=g.id,
            platforms=frozenset(g.title_platform or []),
            is_obtainable=unit_obtainable(g, excused, tg.trophy_group_id),
            is_delisted=g.is_delisted,
            base_complete=complete,
            full_complete=complete,
            completion_date=completion_date,
            full_date=completion_date,    # one bar: the group is the whole game it stands for
        )

    stages_by_series = defaultdict(list)
    for st in catalog['stages']:
        units = stage_units(st, game_state, group_state)
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
    'tipper' (the last member to complete). Obtainable/delisted aggregate over members so the engine routes +
    gates it like any other qualifier.

    PLATFORMS ARE THE INTERSECTION, not the union. The bundle is satisfied only when EVERY member is
    complete, so the platforms it can actually be completed on are the ones every member runs on. A union
    said otherwise and produced a badge nobody could ever earn: a bundle with a PS3-only member and a
    PS5-only member reported platforms {PS3, PS5}, so it QUALIFIED for Ultra HD, GATED it, and could never
    be satisfied by an Ultra HD hunter -- a permanent "0 / N" chase on the Collection wall with no path to
    completion. That directly violates the engine's own rule, stated in badge_engine's docstring: a badge
    must not demand work that cannot be done on its own platforms.

    An empty intersection (members share no platform) means the bundle is completable nowhere and falls out
    of scope for every group, which is the correct reading of an unsatisfiable qualifier.
    """
    members = list(bundle.concepts.all())
    if not members:
        return None
    platforms, any_delisted = None, False
    base_all, full_all, obtainable_all = True, True, True
    member_dates, member_full_dates = [], []
    for c in members:
        states = [game_state_fn(g) for g in c.games.all()]
        member_platforms = set()
        for s in states:
            member_platforms |= set(s.platforms)
        platforms = member_platforms if platforms is None else (platforms & member_platforms)
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
        full_states = [s for s in states if s.full_complete]
        if full_states:
            dates = [s.full_date for s in full_states if s.full_date is not None]
            member_full_dates.append(min(dates) if dates else None)
        else:
            full_all = False
    bundle_date = None
    if base_all and member_dates and all(d is not None for d in member_dates):
        bundle_date = max(member_dates)
    # The full bar's tipper, the same way: the last member to reach it (undated if any member is).
    full_date = None
    if full_all and member_full_dates and all(d is not None for d in member_full_dates):
        full_date = max(member_full_dates)
    return GameState(
        game_id=-bundle.id,               # synthetic; game_id is identity-only in the engine
        platforms=frozenset(platforms or ()),
        is_obtainable=obtainable_all,
        is_delisted=any_delisted,
        base_complete=base_all,
        full_complete=full_all,
        completion_date=bundle_date,
        full_date=full_date,
    )
