"""The Challenge Optimizer's data side: the cached pool, badge stages, run state, the bulk hatch, and the plan.

The optimisation itself is pinned in `test_challenge_plan_solver.py`. What is pinned HERE is that the planner
feeds the solver the same rules the picker and `assign` use: the pool's exclusions, the shape of a fit, the
hatch's threshold (key by key, against `hatch_is_open`), and which badge stages count as uncleared.
"""
import pytest
from django.utils import timezone

from challenges.models import CHALLENGE_TYPE_AZ, CHALLENGE_TYPE_JOBS, HATCH_THRESHOLD
from challenges.services import challenge_service as svc
from challenges.services import eligibility
from challenges.services import planner
from tests.factories import (
    BadgeSeriesFactory,
    ConceptBundleFactory,
    ConceptFactory,
    GameFactory,
    GroupBadgeFactory,
    IGDBMatchFactory,
    ProfileFactory,
    ProfileGameFactory,
    StageFactory,
)
from trophies.models import Contract, EarnedContract, Job, SeriesBadgeStanding

pytestmark = pytest.mark.django_db

AZ, JOBS = CHALLENGE_TYPE_AZ, CHALLENGE_TYPE_JOBS
_SEQ = {'n': 0}


def _member():
    return ProfileFactory(user_is_premium=True)


def _contract(name, *, jobs=(), platforms=('PS5',), live=True):
    """A live contract with one anchored, trusted member concept and game. `.concept` / `.game` ride along."""
    _SEQ['n'] += 1
    contract = Contract.objects.create(name=name, slug='pc-%d' % _SEQ['n'], is_live=live, igdb_id=830_000 + _SEQ['n'])
    if jobs:
        contract.jobs.set(jobs)
    contract.concept = ConceptFactory(anchor_migration_completed_at=timezone.now())
    IGDBMatchFactory(concept=contract.concept, igdb_id=contract.igdb_id, status='accepted')
    contract.game = GameFactory(concept=contract.concept, title_platform=list(platforms))
    return contract


def _completed(profile, contract):
    EarnedContract.objects.create(profile=profile, contract=contract, has_platinum=True,
                                  platinum_reached_at=timezone.now())


def _two_jobs():
    return list(Job.objects.order_by('slug')[:2])


def _options(**kw):
    kw.setdefault('badge_started', False)
    return planner.PlannerOptions(**kw)


def _ids(pool):
    return [c['id'] for c in pool['candidates']]


def _build(profile, options):
    """A freshly built pool in the form the planner reads (`get_pool` hydrates the stored form)."""
    return planner._hydrate(planner.build_pool(profile, options))


# --- pool -------------------------------------------------------------------------------------------------

def test_the_pool_lists_every_square_key_a_contract_fits_per_type():
    profile = _member()
    job_a, job_b = _two_jobs()
    contract = _contract('Bloodborne', jobs=[job_a, job_b])

    pool = _build(profile, _options())

    [cand] = pool['candidates']
    assert cand['id'] == contract.id
    assert cand['fits'] == {AZ: ['B'], JOBS: sorted([job_a.slug, job_b.slug])}


def test_the_pool_excludes_completed_unpublished_and_unfittable_contracts():
    profile = _member()
    keep = _contract('Astro Bot')
    _completed(profile, _contract('Bloodborne'))
    _contract('Celeste', live=False)
    _contract('1979 Revolution')   # a digit fits no letter, and it has no jobs

    assert _ids(_build(profile, _options())) == [keep.id]


def test_the_pool_honours_the_platform_filter():
    profile = _member()
    ps4 = _contract('Alpha', platforms=('PS4',))
    _contract('Beta', platforms=('PS3',))

    assert _ids(_build(profile, _options(platforms=('PS4', 'PS5')))) == [ps4.id]


@pytest.mark.parametrize('source, expected', [
    (planner.SOURCE_BACKLOG, ['Started']),
    (planner.SOURCE_NEW, ['Fresh']),
    (planner.SOURCE_BOTH, ['Started', 'Fresh']),   # backlog first when the hunter wants both
])
def test_the_source_choice_splits_the_pool_on_progress(source, expected):
    profile = _member()
    started = _contract('Started')
    fresh = _contract('Fresh')
    ProfileGameFactory(profile=profile, game=started.game, progress=40)
    ids = {'Started': started.id, 'Fresh': fresh.id}

    pool = _build(profile, _options(source=source))

    assert _ids(pool) == [ids[name] for name in expected]


def test_get_pool_builds_once_then_reads_the_cache():
    profile = _member()
    _contract('Astro Bot')
    options = _options()

    first, built = planner.get_pool(profile, options)
    _contract('Another')   # not in the cached pool until it expires
    second, built_again = planner.get_pool(profile, options)

    assert built and not built_again
    assert _ids(second) == _ids(first)


def test_the_cache_key_ignores_chip_order():
    a = planner.PlannerOptions(platforms=('PS5', 'PS4'))
    b = planner.PlannerOptions(platforms=('PS4', 'PS5'))
    assert a.digest() == b.digest()


# --- badge stages -----------------------------------------------------------------------------------------

def _badge(*stage_contracts, live=True, bundle=None, badge_type='series', platform_group=None):
    """A series with one edition; `.edition` is its GroupBadge."""
    series = BadgeSeriesFactory(badge_type=badge_type)
    extra = {'platform_group': platform_group} if platform_group else {}
    series.edition = GroupBadgeFactory(series=series, is_live=live, **extra)
    for number, contract in enumerate(stage_contracts, start=1):
        stage = StageFactory(series_slug=series.series_slug, stage_number=number, title=f'Stage {number}')
        stage.concepts.add(contract.concept)
    if bundle:
        stage = StageFactory(series_slug=series.series_slug, stage_number=len(stage_contracts) + 1)
        b = ConceptBundleFactory(stage=stage)
        b.concepts.add(*(c.concept for c in bundle))
    return series


def _picks(*series):
    return _options(badge_picks=tuple(s.edition.id for s in series))


def test_uncleared_stages_ride_on_their_contracts_and_cleared_ones_do_not():
    profile = _member()
    todo = _contract('Todo')
    done = _contract('Done')
    series = _badge(todo, done)
    ProfileGameFactory(profile=profile, game=done.game, progress=100)

    pool = _build(profile, _picks(series))

    by_id = {c['id']: c for c in pool['candidates']}
    [stage] = by_id[todo.id]['stages']
    assert stage['stage'] == 1 and stage['editions'] == [series.edition.platform_group.name]
    assert by_id[done.id]['stages'] == []


def test_a_bundle_member_is_marked_as_part_of_its_stage():
    profile = _member()
    ep1, ep2 = _contract('Episode One'), _contract('Episode Two')
    series = _badge(bundle=[ep1, ep2])

    stages = _build(profile, _picks(series))['stages']

    assert stages[ep1.id][0]['part'] is True


def test_a_dormant_badge_contributes_nothing_even_under_all_badges():
    profile = _member()
    contract = _contract('Hidden')
    _badge(contract, live=False)

    assert _build(profile, _options(badge_all=True))['stages'] == {}


def test_started_editions_are_read_per_edition():
    # Legacy done, Ultra half-way: the series-level `progress_bp` (best edition) would call the whole series
    # finished. Per edition, Ultra is started and Legacy is not.
    from tests.factories import PlatformGroupFactory
    profile = _member()
    series = BadgeSeriesFactory()
    legacy = GroupBadgeFactory(series=series, platform_group=PlatformGroupFactory(key='legacy-t', platforms=['PS3']))
    ultra = GroupBadgeFactory(series=series, platform_group=PlatformGroupFactory(key='ultra-t', platforms=['PS5']))
    untouched = _badge(_contract('Untouched'))
    SeriesBadgeStanding.objects.create(profile=profile, series_slug=series.series_slug, progress_bp=10000,
                                       group_progress={'legacy-t': [3, 3], 'ultra-t': [2, 5]})

    started = planner.started_edition_ids(profile)

    assert started == [ultra.id]
    assert legacy.id not in started and untouched.edition.id not in started


def test_a_whole_badge_type_is_a_source():
    profile = _member()
    dev_game, series_game = _contract('Dev Game'), _contract('Series Game')
    _badge(dev_game, badge_type='developer')
    _badge(series_game, badge_type='series')

    stages = _build(profile, _options(badge_types=('developer',)))['stages']

    assert set(stages) == {dev_game.id}


def test_all_badges_covers_every_live_type():
    profile = _member()
    a, b = _contract('Alpha'), _contract('Beta')
    _badge(a, badge_type='developer')
    _badge(b, badge_type='franchise')

    assert set(_build(profile, _options(badge_all=True))['stages']) == {a.id, b.id}


def test_sources_add_up():
    profile = _member()
    picked, typed = _contract('Picked'), _contract('Typed')
    pick_series = _badge(picked, badge_type='series')
    _badge(typed, badge_type='developer')

    stages = _build(profile, _options(badge_types=('developer',), badge_picks=(pick_series.edition.id,)))['stages']

    assert set(stages) == {picked.id, typed.id}


def test_broad_sources_follow_the_platforms_but_hand_picks_do_not():
    from tests.factories import PlatformGroupFactory
    profile = _member()
    legacy_game = _contract('Legacy Only', platforms=('PS3',))
    series = _badge(legacy_game, platform_group=PlatformGroupFactory(key='legacy-only', platforms=['PS3']))
    _badge(_contract('Ultra Anchor'))   # an Ultra HD edition exists, so PS5 is a platform editions cover

    assert legacy_game.id not in planner._badge_stages(profile, _options(badge_all=True, platforms=('PS5',)))
    assert legacy_game.id in planner._badge_stages(profile, _options(badge_all=True))
    # A hunter who picked the Legacy edition by hand meant it, whatever step 2 says.
    assert legacy_game.id in planner._badge_stages(profile, _options(badge_picks=(series.edition.id,), platforms=('PS5',)))


def test_badges_off_skips_the_evaluation_entirely(django_assert_num_queries):
    profile = _member()
    contract = _contract('Todo')
    _badge(contract)
    with django_assert_num_queries(0):
        stages = planner._badge_stages(profile, _options(badge_started=False))
    assert stages == {}


def test_broad_sources_read_the_whole_catalogue_shape_and_give_the_same_answer(monkeypatch):
    # "All badges" is a whole-catalogue question (subquery reads); the same editions picked by hand are a handful
    # (inlined ids). Same question, different query shape: the answer must not change. The spy proves which
    # shape each one actually ran.
    from trophies.services import badge_orchestrator
    profile = _member()
    todo, done = _contract('Todo'), _contract('Done')
    a, b = _badge(todo), _badge(done)
    ProfileGameFactory(profile=profile, game=done.game, progress=100)
    shapes = []
    real = badge_orchestrator.build_catalog
    monkeypatch.setattr(badge_orchestrator, 'build_catalog',
                        lambda gbs, whole_catalogue=False: shapes.append(whole_catalogue) or real(gbs, whole_catalogue=whole_catalogue))

    whole = planner._badge_stages(profile, _options(badge_all=True))
    small = planner._badge_stages(profile, _options(badge_picks=(a.edition.id, b.edition.id)))

    assert shapes == [True, False]
    assert whole == small and set(whole) == {todo.id}


def test_a_stage_lists_every_selected_edition_it_gates():
    from tests.factories import PlatformGroupFactory
    profile = _member()
    contract = _contract('Both Editions', platforms=('PS3', 'PS5'))
    series = BadgeSeriesFactory()
    legacy = GroupBadgeFactory(series=series, platform_group=PlatformGroupFactory(key='leg-2', name='Legacy HD', platforms=['PS3']))
    ultra = GroupBadgeFactory(series=series, platform_group=PlatformGroupFactory(key='ult-2', name='Ultra HD', platforms=['PS5']))
    StageFactory(series_slug=series.series_slug, stage_number=1).concepts.add(contract.concept)

    [stage] = planner._badge_stages(profile, _options(badge_picks=(legacy.id, ultra.id)))[contract.id]

    assert stage['editions'] == ['Legacy HD', 'Ultra HD']


def test_a_held_edition_is_not_started_even_with_a_stale_pair():
    from trophies.models import UserGroupBadge
    profile = _member()
    series = _badge(_contract('Held'))
    SeriesBadgeStanding.objects.create(profile=profile, series_slug=series.series_slug, progress_bp=4000,
                                       group_progress={series.edition.platform_group.key: [2, 5]})
    UserGroupBadge.objects.create(profile=profile, group_badge=series.edition)

    assert planner.started_edition_ids(profile) == []


@pytest.mark.parametrize('progress', [{'k': [None, 5]}, {'k': ['2', '5']}, {'k': [True, 5]}, ['not', 'a dict']])
def test_malformed_progress_is_skipped_not_a_500(progress):
    profile = _member()
    series = _badge(_contract('Odd'))
    key = series.edition.platform_group.key
    if isinstance(progress, dict):
        progress = {key: progress['k']}
    SeriesBadgeStanding.objects.create(profile=profile, series_slug=series.series_slug, progress_bp=1,
                                       group_progress=progress)
    assert planner.started_edition_ids(profile) == []


def test_started_editions_are_scoped_to_the_platforms_too():
    from tests.factories import PlatformGroupFactory
    profile = _member()
    contract = _contract('Legacy Started', platforms=('PS3',))
    series = _badge(contract, platform_group=PlatformGroupFactory(key='leg-3', platforms=['PS3']))
    _badge(_contract('Ultra Anchor'))   # an Ultra HD edition exists, so PS5 is a platform editions cover
    SeriesBadgeStanding.objects.create(profile=profile, series_slug=series.series_slug, progress_bp=3000,
                                       group_progress={'leg-3': [1, 3]})

    assert planner._badge_stages(profile, _options(badge_started=True, platforms=('PS5',))) == {}
    assert contract.id in planner._badge_stages(profile, _options(badge_started=True, platforms=('PS3',)))


def test_platforms_no_edition_covers_do_not_scope_badges_to_nothing():
    profile = _member()
    contract = _contract('Anything')
    _badge(contract)
    assert contract.id in planner._badge_stages(profile, _options(badge_all=True, platforms=('PSVR',)))


@pytest.mark.parametrize('change', [{'badge_started': True}, {'badge_all': True}, {'badge_types': ('developer',)}])
def test_every_badge_source_is_part_of_the_cache_key(change):
    base = planner.PlannerOptions(badge_started=False)
    assert planner.PlannerOptions(**{'badge_started': False, **change}).digest() != base.digest()


def test_a_whole_catalogue_build_waits_in_line_when_the_slots_are_full():
    import time
    from challenges.services import build_line
    profile = _member()
    _badge(_contract('Busy'))
    r = build_line.redis_client
    r.zadd(build_line.HOLDERS_KEY, {f'busy-{i}': time.time() for i in range(build_line.SLOTS)})

    with pytest.raises(build_line.LineWait) as wait:
        planner.build_pool(profile, _options(badge_all=True))
    assert wait.value.position == 1 and build_line.valid_ticket(wait.value.ticket, profile.user_id)

    # The slots free: the ticket at the front builds, leaves the line, and gives its slot back afterwards.
    r.delete(build_line.HOLDERS_KEY)
    assert planner.build_pool(profile, _options(badge_all=True), line_ticket=wait.value.ticket)['stages']
    assert not build_line.valid_ticket(wait.value.ticket, profile.user_id)
    assert r.zcard(build_line.HOLDERS_KEY) == 0


def test_the_whole_broad_build_runs_inside_its_slot(monkeypatch):
    from challenges.services import build_line
    profile = _member()
    _badge(_contract('Busy'))
    held = []
    real = planner._filtered_contracts
    monkeypatch.setattr(planner, '_filtered_contracts', lambda *a, **k: (
        held.append(build_line.redis_client.zcard(build_line.HOLDERS_KEY)), real(*a, **k))[1])
    planner.build_pool(profile, _options(badge_all=True))
    assert held == [1]   # the contract scan, not just the badge evaluation, holds the slot


def test_a_broad_build_that_selects_nothing_still_uses_up_its_ticket():
    # Otherwise one ticket would build any number of differently-keyed pools past the build limit.
    import time
    from challenges.services import build_line
    profile = _member()
    r = build_line.redis_client
    r.zadd(build_line.HOLDERS_KEY, {f'busy-{i}': time.time() for i in range(build_line.SLOTS)})
    with pytest.raises(build_line.LineWait) as wait:
        planner.build_pool(profile, _options(badge_types=('developer',)))   # no live developer editions
    r.delete(build_line.HOLDERS_KEY)
    planner.build_pool(profile, _options(badge_types=('developer',)), line_ticket=wait.value.ticket)
    with pytest.raises(build_line.TicketLost):
        planner.build_pool(profile, _options(badge_types=('developer',), platforms=('PS4',)),
                           line_ticket=wait.value.ticket)


def test_a_whole_catalogue_build_that_must_not_wait_raises_busy():
    import time
    from challenges.services import build_line
    profile = _member()
    _badge(_contract('Busy'))
    build_line.redis_client.zadd(build_line.HOLDERS_KEY, {f'busy-{i}': time.time() for i in range(build_line.SLOTS)})
    with pytest.raises(build_line.LineBusy):
        planner.build_pool(profile, _options(badge_all=True), join_line=False)


def test_a_narrow_badge_build_never_touches_the_line():
    import time
    from challenges.services import build_line
    profile = _member()
    contract = _contract('Picked')
    series = _badge(contract)
    build_line.redis_client.zadd(build_line.HOLDERS_KEY, {f'busy-{i}': time.time() for i in range(build_line.SLOTS)})
    # Every slot is taken, but picked badges are a small build: it goes ahead.
    assert planner.build_pool(profile, _picks(series))['stages']


def test_a_direct_member_of_a_stage_is_not_marked_part():
    profile = _member()
    ep1, ep2 = _contract('Episode One'), _contract('Episode Two')
    series = _badge(ep1, bundle=[ep1, ep2])   # ep1 is a direct member of stage 1 AND in stage 2's bundle

    stages = _build(profile, _picks(series))['stages']
    by_stage = {s['stage']: s['part'] for s in stages[ep1.id]}

    assert by_stage == {1: False, 2: True}


def test_badge_stages_rank_ahead_of_backlog_progress():
    # The solver ranks stages above progress, so the pool must too, or pruning drops the stage game first.
    profile = _member()
    started = _contract('Started')
    staged = _contract('Staged')
    ProfileGameFactory(profile=profile, game=started.game, progress=60)
    series = _badge(staged)

    pool = planner.build_pool(profile, _picks(series))

    assert [row[0] for row in pool['candidates']] == [staged.id, started.id]


def test_the_cached_pool_survives_a_json_round_trip():
    # Production's cache serializes to JSON, which turns int dict keys into strings. A pin from outside the
    # pool reads its stages from the map, so a string key would silently lose them.
    import json
    profile = _member()
    staged = _contract('Staged', platforms=('PS4',))
    series = _badge(staged)
    options = _options(badge_picks=(series.edition.id,), platforms=('PS5',))

    pool = planner._hydrate(json.loads(json.dumps(planner.build_pool(profile, options))))
    [pinned] = planner.pin_candidates(profile, options, pool, {staged.id})

    assert [s['stage'] for s in pinned['stages']] == [1]


def test_two_orderings_of_picks_share_one_pool_and_the_weight_never_splits_it():
    a = planner.PlannerOptions(badge_picks=tuple(range(1, 80)))
    b = planner.PlannerOptions(badge_picks=tuple(reversed(range(1, 80))), badge_weight=planner.WEIGHT_PRIORITY)
    assert a.digest() == b.digest()
    assert a.picked_badges == b.picked_badges and len(a.picked_badges) == planner.MAX_PICKED_BADGES


def test_priority_lets_a_badge_game_win_at_the_cost_of_a_double():
    # One double (A + job) with no stage, or a letter-only game that clears a badge stage plus a job-only game.
    # Bonus keeps the double; Priority takes the stage even though it needs two games.
    profile = _member()
    job, _ = _two_jobs()
    _contract('Alpha', jobs=[job])
    staged = _contract('Apex')
    filler = _contract('7 Days', jobs=[job])   # a digit fits no letter, so it can only ever take the job
    series = _badge(staged)
    _runs(profile, AZ, JOBS)

    bonus = _plan(profile, _options(badge_picks=(series.edition.id,)))
    priority = _plan(profile, _options(badge_picks=(series.edition.id,), badge_weight=planner.WEIGHT_PRIORITY))

    assert bonus.assignments[(AZ, 'A')] != staged.id
    assert priority.assignments[(AZ, 'A')] == staged.id
    assert priority.assignments[(JOBS, job.slug)] == filler.id


# --- bulk hatch -------------------------------------------------------------------------------------------

@pytest.mark.parametrize('challenge_type', [AZ, JOBS])
def test_hatch_open_keys_agrees_with_hatch_is_open_square_by_square(challenge_type):
    profile = _member()
    job_a, job_b = _two_jobs()
    # A thin A (2 games), a B right at the threshold, a fat C, and jobs spread unevenly.
    for i in range(2):
        _contract(f'A game {i}', jobs=[job_a])
    for i in range(HATCH_THRESHOLD):
        _contract(f'B game {i}', jobs=[job_a, job_b])
    for i in range(HATCH_THRESHOLD + 2):
        _contract(f'C game {i}', jobs=[job_b])
    _completed(profile, Contract.objects.get(name='C game 0'))

    challenge = svc.start(profile, challenge_type)
    # A game placed in another square leaves this square's pool; its own square keeps it.
    placed = Contract.objects.get(name='B game 0')
    key = 'B' if challenge_type == AZ else job_b.slug
    svc.assign(challenge, profile, key, placed)

    keys = list(challenge.slots.values_list('key', flat=True))
    expected = {k for k in keys if eligibility.hatch_is_open(profile, challenge, k)}
    assert eligibility.hatch_open_keys(profile, challenge, keys) == expected


def test_a_game_placed_in_another_job_square_tips_this_one_to_the_hatch():
    # The boundary the parity test above cannot see: job X has threshold+1 games, and one of them sits in job
    # Y's square. `_slot_pool` drops it from X's pool, so X is AT the threshold and open. Without the placed
    # subtraction the bulk form would count threshold+1 and call it shut.
    profile = _member()
    job_x, job_y = _two_jobs()
    shared = _contract('Shared', jobs=[job_x, job_y])
    for i in range(HATCH_THRESHOLD):
        _contract(f'X only {i}', jobs=[job_x])
    run = svc.start(profile, JOBS)
    svc.assign(run, profile, job_y.slug, shared)

    assert eligibility.hatch_is_open(profile, run, job_x.slug)
    assert job_x.slug in eligibility.hatch_open_keys(profile, run, [job_x.slug])


# --- runs and plans ---------------------------------------------------------------------------------------

def _runs(profile, *types):
    return [svc.start(profile, t) for t in types]


def test_run_states_split_open_unfinished_and_in_run():
    profile = _member()
    contract = _contract('Bloodborne')
    [run] = _runs(profile, AZ)
    svc.assign(run, profile, 'B', contract)

    state = planner.run_states(profile, (AZ, JOBS))[AZ]

    assert set(planner.run_states(profile, (AZ, JOBS))) == {AZ}
    assert 'B' not in state.open_keys and 'A' in state.open_keys
    assert state.unfinished == {'B': contract.id}
    assert state.in_run == frozenset({contract.id})


def _plan(profile, options=None, tweaks=planner.Tweaks()):
    options = options or _options()
    pool, _ = planner.get_pool(profile, options)
    return planner.make_plan(profile, options, pool, planner.run_states(profile, options.types), tweaks)


def test_a_game_that_fits_a_letter_and_a_job_is_planned_into_both_runs():
    profile = _member()
    job, _ = _two_jobs()
    double = _contract('Bloodborne', jobs=[job])
    _contract('Brothers')
    _runs(profile, AZ, JOBS)

    plan = _plan(profile)

    assert plan.assignments[(AZ, 'B')] == double.id
    assert plan.assignments[(JOBS, job.slug)] == double.id


def test_a_pin_off_the_chosen_platforms_is_still_placed():
    profile = _member()
    off = _contract('Astro Bot', platforms=('PS4',))
    _runs(profile, AZ)

    plan = _plan(profile, _options(types=(AZ,), platforms=('PS5',)), planner.Tweaks(pins=frozenset({off.id})))

    assert plan.assignments == {(AZ, 'A'): off.id}
    assert off.id in plan.candidates


def test_an_excluded_game_is_left_out_of_the_plan():
    profile = _member()
    first, second = _contract('Alpha'), _contract('Apex')
    _runs(profile, AZ)

    plan = _plan(profile, _options(types=(AZ,)), planner.Tweaks(excluded=frozenset({first.id})))

    assert plan.assignments[(AZ, 'A')] == second.id


def test_unplaced_pins_carry_a_reason():
    profile = _member()
    finished = _contract('Alpha')
    _completed(profile, finished)
    nowhere = _contract('Beta')
    [run] = _runs(profile, AZ)
    svc.assign(run, profile, 'B', _contract('Bravo'))
    svc.mark_slot_completed(run.slots.get(key='B'))

    plan = _plan(profile, _options(types=(AZ,)), planner.Tweaks(pins=frozenset({finished.id, nowhere.id})))

    assert plan.unplaced_pins == {finished.id: planner.UNPLACED_FINISHED, nowhere.id: planner.UNPLACED_NO_SQUARE}


def test_a_swap_is_suggested_when_it_lets_one_game_cover_both_runs():
    profile = _member()
    job, _ = _two_jobs()
    single = _contract('Bloodborne')
    double = _contract('Brothers', jobs=[job])
    az_run, _ = _runs(profile, AZ, JOBS)
    svc.assign(az_run, profile, 'B', single)

    plan = _plan(profile)

    assert plan.swaps == {(AZ, 'B'): (single.id, double.id, [(JOBS, job.slug)])}
    assert plan.assignments[(JOBS, job.slug)] == double.id


def test_accepting_a_swap_plans_it_into_the_unfinished_square():
    profile = _member()
    job, _ = _two_jobs()
    single = _contract('Bloodborne')
    double = _contract('Brothers', jobs=[job])
    az_run, _ = _runs(profile, AZ, JOBS)
    svc.assign(az_run, profile, 'B', single)

    plan = _plan(profile, tweaks=planner.Tweaks(square_pins={(AZ, 'B'): double.id}))

    assert plan.assignments[(AZ, 'B')] == double.id
    assert plan.swaps == {}
    assert (AZ, 'B') not in plan.empty


def test_no_swap_is_suggested_when_keeping_the_game_is_as_good():
    profile = _member()
    current = _contract('Bloodborne')
    _contract('Brothers')
    [run] = _runs(profile, AZ)
    svc.assign(run, profile, 'B', current)

    assert _plan(profile, _options(types=(AZ,))).swaps == {}


def test_hatch_squares_are_reported_as_notes():
    profile = _member()
    _contract('Alpha')
    for i in range(HATCH_THRESHOLD + 1):
        _contract(f'Bravo {i}')
    _runs(profile, AZ)

    plan = _plan(profile, _options(types=(AZ,)))

    assert 'A' in plan.hatch_keys[AZ]
    assert 'B' not in plan.hatch_keys[AZ]


# --- pruning ----------------------------------------------------------------------------------------------

def _pool_of(*cands):
    return [{'id': cid, 'progress': 0, 'fits': fits, 'stages': []} for cid, fits in cands]


def test_pruning_does_not_spend_a_square_budget_on_games_already_in_that_run():
    # Five games already in the Jobs run all fit job0; spending job0's budget on them would prune the one game
    # that can actually go there.
    in_run_ids = {1, 2, 3, 4, 5}
    pool = _pool_of(*((cid, {JOBS: ['job0']}) for cid in sorted(in_run_ids)), (9, {JOBS: ['job0']}))
    kept = planner._prune(pool, [(JOBS, 'job0')], set(), frozenset(), {JOBS: frozenset(in_run_ids)}, {})
    assert 9 in {c.id for c in kept}


def test_a_job_square_keeps_enough_games_for_every_open_job_to_fill():
    # Six jobs open; the first five games fit all six; game 6 fits only job5. With a flat budget of 4 the
    # first four games take every budget and job5 loses its only distinct candidate.
    jobs = [f'job{i}' for i in range(6)]
    pool = _pool_of(*((cid, {JOBS: jobs}) for cid in range(5)), (6, {JOBS: ['job5']}))
    kept = planner._prune(pool, [(JOBS, j) for j in jobs], set(), frozenset(), {}, {})
    assert {c.id for c in kept} == {0, 1, 2, 3, 4, 6}


# --- reasons, swaps and alternatives ----------------------------------------------------------------------

def test_a_pin_already_in_the_run_it_fits_is_reported_as_in_run():
    profile = _member()
    job_a, job_b = _two_jobs()
    contract = _contract('Bloodborne', jobs=[job_a, job_b])
    [run] = _runs(profile, JOBS)
    svc.assign(run, profile, job_a.slug, contract)

    plan = _plan(profile, _options(types=(JOBS,)), planner.Tweaks(pins=frozenset({contract.id})))

    assert plan.unplaced_pins == {contract.id: planner.UNPLACED_IN_RUN}


def test_a_pin_off_the_job_board_is_reported_as_gone():
    profile = _member()
    gone = _contract('Alpha')
    Contract.objects.filter(pk=gone.pk).update(is_live=False)
    _runs(profile, AZ)

    plan = _plan(profile, _options(types=(AZ,)), planner.Tweaks(pins=frozenset({gone.id})))

    assert plan.unplaced_pins == {gone.id: planner.UNPLACED_GONE}


def test_a_square_pin_on_the_wrong_square_is_reported_as_no_square():
    profile = _member()
    alpha = _contract('Alpha')
    _runs(profile, AZ)

    plan = _plan(profile, _options(types=(AZ,)), planner.Tweaks(square_pins={(AZ, 'B'): alpha.id}))

    assert plan.unplaced_pins == {alpha.id: planner.UNPLACED_NO_SQUARE}


def test_a_square_pin_on_a_finished_square_is_reported_as_closed():
    profile = _member()
    bravo, other = _contract('Bravo'), _contract('Brothers')
    [run] = _runs(profile, AZ)
    svc.assign(run, profile, 'B', bravo)
    svc.mark_slot_completed(run.slots.get(key='B'))

    plan = _plan(profile, _options(types=(AZ,)), planner.Tweaks(square_pins={(AZ, 'B'): other.id}))

    assert plan.unplaced_pins == {other.id: planner.UNPLACED_SQUARE_CLOSED}


def test_pinning_a_squares_own_game_means_keep_it():
    profile = _member()
    bravo = _contract('Bravo')
    _contract('Brothers')
    [run] = _runs(profile, AZ)
    svc.assign(run, profile, 'B', bravo)

    plan = _plan(profile, _options(types=(AZ,)), planner.Tweaks(square_pins={(AZ, 'B'): bravo.id}))

    assert plan.unplaced_pins == {}
    assert (AZ, 'B') not in plan.assignments and (AZ, 'B') not in plan.swaps


def test_the_swap_solve_holds_every_square_the_plan_filled(monkeypatch):
    # The structural guarantee behind "a swap never contradicts the plan": the second solve receives every
    # primary assignment as a square pin. Captured directly, because which of two tied games the primary
    # picks is a HiGHS placement choice no tier decides, so a scenario test could pass on either branch.
    profile = _member()
    job_a, job_b = _two_jobs()
    occupant = _contract('Zeta', jobs=[job_a])
    _contract('Yonder', jobs=[job_a, job_b])
    _contract('Wander', jobs=[job_b])
    _, jobs_run = _runs(profile, AZ, JOBS)
    svc.assign(jobs_run, profile, job_a.slug, occupant)

    problems = []
    real_solve = planner.solve
    monkeypatch.setattr(planner, 'solve', lambda problem: problems.append(problem) or real_solve(problem))
    plan = _plan(profile)

    primary, swap = problems
    assert primary.occupants == {} and swap.occupants == {(JOBS, job_a.slug): occupant.id}
    assert plan.assignments and all(swap.square_pins.get(sq) == cid for sq, cid in plan.assignments.items())


def test_pruning_does_not_spend_other_budgets_on_a_square_pinned_game():
    # P1..P4 are each pinned to their own X square and also fit j0, ranked ahead of G. Charged against j0's
    # budget they would prune G, the one game that can actually stand there.
    pool = _pool_of(*((cid, {JOBS: ['j0', f'x{cid}']}) for cid in range(1, 5)), (9, {JOBS: ['j0']}))
    squares = [(JOBS, 'j0')] + [(JOBS, f'x{cid}') for cid in range(1, 5)]
    pins = {(JOBS, f'x{cid}'): cid for cid in range(1, 5)}
    kept = planner._prune(pool, squares, set(pins.values()), frozenset(), {}, pins)
    assert 9 in {c.id for c in kept}


def test_not_this_one_beats_a_pin():
    profile = _member()
    first, second = _contract('Alpha'), _contract('Apex')
    _runs(profile, AZ)

    plan = _plan(profile, _options(types=(AZ,)),
                 planner.Tweaks(pins=frozenset({first.id}), excluded=frozenset({first.id})))

    assert plan.assignments == {(AZ, 'A'): second.id}
    assert plan.unplaced_pins == {}


def test_a_game_placed_elsewhere_is_not_reported_for_its_closed_square():
    profile = _member()
    job, _ = _two_jobs()
    bravo = _contract('Bravo', jobs=[job])
    az_run, _ = _runs(profile, AZ, JOBS)
    az_run.slots.filter(key='B').update(contract_slug='filled-b', is_completed=True,
                                        completed_at=timezone.now(), completed_via='live')

    plan = _plan(profile, tweaks=planner.Tweaks(square_pins={(AZ, 'B'): bravo.id}))

    assert plan.assignments.get((JOBS, job.slug)) == bravo.id
    assert bravo.id not in plan.unplaced_pins


def test_a_dead_snapshot_counts_as_already_in_the_run():
    # `assign` refuses a game whose slug a deleted contract's square still carries; the plan must not offer it.
    profile = _member()
    [run] = _runs(profile, AZ)
    run.slots.filter(key='B').update(contract_slug='bravo-returns', contract_name='Bravo')
    back = _contract('Bravo Returns')
    Contract.objects.filter(pk=back.pk).update(slug='bravo-returns')

    assert back.id in planner.run_states(profile, (AZ,))[AZ].in_run


def test_an_occupant_outside_the_pool_is_kept_when_a_swap_gains_nothing():
    profile = _member()
    ps4 = _contract('Bravo', platforms=('PS4',))
    _contract('Brothers')
    [run] = _runs(profile, AZ)
    svc.assign(run, profile, 'B', ps4)

    plan = _plan(profile, _options(types=(AZ,), platforms=('PS5',)))

    assert plan.swaps == {}


def test_alternatives_never_offer_a_double_into_a_run_that_already_holds_the_game():
    profile = _member()
    job_a, job_b = _two_jobs()
    held = _contract('Astro Bot', jobs=[job_a, job_b])
    az_run, jobs_run = _runs(profile, AZ, JOBS)
    svc.assign(jobs_run, profile, job_a.slug, held)
    options = _options()
    pool, _ = planner.get_pool(profile, options)

    halves = planner.alternatives(pool, planner.run_states(profile, options.types), (AZ, 'A'))

    assert halves['both'] == [] and [(c['id'], also) for c, also in halves['rest']] == [(held.id, [])]


def _ids_by_half(halves):
    return {half: [c['id'] for c, _ in rows] for half, rows in halves.items()}


def _two_run_pool(profile):
    _runs(profile, AZ, JOBS)
    options = _options()
    pool, _ = planner.get_pool(profile, options)
    return pool, planner.run_states(profile, options.types)


def test_alternatives_put_games_that_fit_the_lines_other_square_first():
    # Opened on A from a line that also holds job_a: a game fitting job_a can take over the whole line.
    profile = _member()
    job_a, job_b = _two_jobs()
    other_double = _contract('Alpha', jobs=[job_b])
    single = _contract('Apex')
    covers = _contract('Astro Bot', jobs=[job_a, job_b])
    pool, states = _two_run_pool(profile)

    halves = planner.alternatives(pool, states, (AZ, 'A'), partners=[(JOBS, job_a.slug)])

    assert _ids_by_half(halves) == {'both': [covers.id], 'rest': [other_double.id, single.id], 'partner': []}
    # The partner square is the line's own, so "also covers" names only the OTHER squares a game fits.
    assert [also for _, also in halves['both'] + halves['rest']] == [[(JOBS, job_b.slug)], [(JOBS, job_b.slug)], []]


def test_alternatives_without_partners_flag_nothing_and_keep_doubles_first():
    profile = _member()
    job_a, _ = _two_jobs()
    single = _contract('Apex')
    double = _contract('Astro Bot', jobs=[job_a])
    pool, states = _two_run_pool(profile)

    halves = planner.alternatives(pool, states, (AZ, 'A'))

    assert _ids_by_half(halves) == {'both': [], 'rest': [double.id, single.id], 'partner': []}


def test_alternatives_list_games_for_the_partner_alone_that_the_square_cannot_take():
    # Opened on A from an A + job_a line: "job_a only" holds games for the job that cannot take A (another
    # letter, or already in the A-Z run), each noting the other open squares it fits.
    profile = _member()
    job_a, _ = _two_jobs()
    both = _contract('Astro Bot', jobs=[job_a])
    job_only = _contract('Bloodborne', jobs=[job_a])
    letter_only = _contract('Apex')
    pool, states = _two_run_pool(profile)

    halves = planner.alternatives(pool, states, (AZ, 'A'), partners=[(JOBS, job_a.slug)])

    assert _ids_by_half(halves) == {'both': [both.id], 'rest': [letter_only.id], 'partner': [job_only.id]}
    assert halves['partner'][0][1] == [(AZ, 'B')]   # Bloodborne also fits B


def test_a_game_already_in_the_squares_run_is_offered_for_the_partner_alone():
    profile = _member()
    job_a, _ = _two_jobs()
    placed = _contract('Astro Bot', jobs=[job_a])
    az_run, _ = _runs(profile, AZ, JOBS)
    svc.assign(az_run, profile, 'B', _contract('Bastion'))
    svc.assign(az_run, profile, 'C', _contract('Celeste'))
    from challenges.models import ChallengeSlot
    ChallengeSlot.objects.filter(challenge=az_run, key='D').update(contract=placed, contract_slug=placed.slug)
    options = _options()
    pool, _ = planner.get_pool(profile, options)
    halves = planner.alternatives(pool, planner.run_states(profile, options.types), (AZ, 'A'),
                                  partners=[(JOBS, job_a.slug)])
    assert placed.id not in _ids_by_half(halves)['both'] and placed.id in _ids_by_half(halves)['partner']


def test_counts_toward_your_badges_keeps_only_stage_games_in_every_tab():
    profile = _member()
    job_a, _ = _two_jobs()
    staged = _contract('Astro Bot', jobs=[job_a])
    _contract('Alto', jobs=[job_a])
    plain_job = _contract('Bloodborne', jobs=[job_a])
    staged_job = _contract('Brothers', jobs=[job_a])
    staged_letter = _contract('Apex')
    plain_letter = _contract('Abzu')
    series = _badge(staged, staged_job, staged_letter)
    _runs(profile, AZ, JOBS)
    options = _options(badge_picks=(series.edition.id,))
    pool, _ = planner.get_pool(profile, options)
    states = planner.run_states(profile, options.types)

    halves = planner.alternatives(pool, states, (AZ, 'A'), partners=[(JOBS, job_a.slug)], badge_only=True)

    assert _ids_by_half(halves) == {'both': [staged.id], 'rest': [staged_letter.id], 'partner': [staged_job.id]}
    unfiltered = _ids_by_half(planner.alternatives(pool, states, (AZ, 'A'), partners=[(JOBS, job_a.slug)]))
    assert plain_job.id in unfiltered['partner'] and plain_letter.id in unfiltered['rest']


def test_alternatives_return_every_game_never_a_capped_few():
    # Owner, 2026-10-17: the sheet shows everything (its door pages it). Nothing here may cut the list short.
    profile = _member()
    job_a, _ = _two_jobs()
    covering = [_contract(f'Alpha {i}', jobs=[job_a]) for i in range(12)]
    singles = [_contract(f'Apex {i}') for i in range(12)]
    pool, states = _two_run_pool(profile)

    halves = planner.alternatives(pool, states, (AZ, 'A'), partners=[(JOBS, job_a.slug)])

    assert sorted(_ids_by_half(halves)['both']) == sorted(c.id for c in covering)
    assert sorted(_ids_by_half(halves)['rest']) == sorted(c.id for c in singles)


def test_a_square_left_open_is_never_also_covered():
    profile = _member()
    job_a, job_b = _two_jobs()
    _contract('Astro Bot', jobs=[job_a, job_b])
    pool, states = _two_run_pool(profile)

    halves = planner.alternatives(pool, states, (AZ, 'A'), left_open=frozenset({(JOBS, job_b.slug)}))

    assert [also for _, also in halves['rest']] == [[(JOBS, job_a.slug)]]


# --- leave a square open (owner, 2026-10-17) ----------------------------------------------------------------

def test_a_square_left_open_is_never_planned():
    profile = _member()
    apex = _contract('Apex')
    _runs(profile, AZ)
    options = _options(types=(AZ,))
    plan = _plan(profile, options, tweaks=planner.Tweaks(left_open=frozenset({(AZ, 'A')})))
    assert (AZ, 'A') not in plan.assignments
    assert plan.left_open == [(AZ, 'A')] and (AZ, 'A') not in plan.empty
    assert apex.id not in plan.candidates


def test_leaving_a_square_open_beats_a_lock_on_it_and_reports_nothing_unplaced():
    profile = _member()
    apex = _contract('Apex')
    _runs(profile, AZ)
    tweaks = planner.Tweaks(square_pins={(AZ, 'A'): apex.id}, left_open=frozenset({(AZ, 'A')}))
    plan = _plan(profile, _options(types=(AZ,)), tweaks=tweaks)
    assert plan.assignments == {} and plan.unplaced_pins == {}


def test_a_square_left_open_then_filled_by_hand_is_forgotten_and_planned_as_usual():
    # Once the hunter fills it themselves it is theirs: no longer "left open", and its swap is offered as for
    # any unfinished square.
    profile = _member()
    job, _ = _two_jobs()
    single = _contract('Bastion')
    _contract('Bloodborne', jobs=[job])
    az_run, _ = _runs(profile, AZ, JOBS)
    svc.assign(az_run, profile, 'B', single)
    plan = _plan(profile, _options(), tweaks=planner.Tweaks(left_open=frozenset({(AZ, 'B')})))
    assert plan.left_open == [] and (AZ, 'B') in plan.swaps


def test_a_must_have_whose_only_square_is_left_open_says_so():
    profile = _member()
    apex = _contract('Apex')
    _runs(profile, AZ)
    tweaks = planner.Tweaks(pins=frozenset({apex.id}), left_open=frozenset({(AZ, 'A')}))
    plan = _plan(profile, _options(types=(AZ,)), tweaks=tweaks)
    assert plan.unplaced_pins == {apex.id: planner.UNPLACED_LEFT_OPEN}


def test_a_left_open_square_that_is_no_longer_plannable_is_forgotten():
    profile = _member()
    _runs(profile, AZ)
    plan = _plan(profile, _options(types=(AZ,)), tweaks=planner.Tweaks(left_open=frozenset({(JOBS, 'archer')})))
    assert plan.left_open == []


def test_the_covering_half_keeps_pool_order():
    # Astro Bot also fits job_b, but the pick pins a covering game to the partner, so that fit is moot and must
    # not push the earlier Alpha down (or off the sheet at the limit).
    profile = _member()
    job_a, job_b = _two_jobs()
    alpha = _contract('Alpha', jobs=[job_a])
    astro = _contract('Astro Bot', jobs=[job_a, job_b])
    pool, states = _two_run_pool(profile)

    halves = planner.alternatives(pool, states, (AZ, 'A'), partners=[(JOBS, job_a.slug)])

    assert _ids_by_half(halves)['both'] == [alpha.id, astro.id]


def test_an_unfinished_partner_square_is_matched_on_fit():
    # A line holding an accepted swap has an UNFINISHED square as its partner: not open, still coverable.
    profile = _member()
    job_a, _ = _two_jobs()
    occupant = _contract('Bastion', jobs=[job_a])
    covers = _contract('Astro Bot', jobs=[job_a])
    az_run, jobs_run = _runs(profile, AZ, JOBS)
    svc.assign(jobs_run, profile, job_a.slug, occupant)
    options = _options()
    pool, _ = planner.get_pool(profile, options)
    states = planner.run_states(profile, options.types)
    assert job_a.slug not in states[JOBS].open_keys

    halves = planner.alternatives(pool, states, (AZ, 'A'), partners=[(JOBS, job_a.slug)])

    assert _ids_by_half(halves) == {'both': [covers.id], 'rest': [], 'partner': []}


def test_a_partner_in_a_run_already_holding_the_game_is_not_covered():
    profile = _member()
    job_a, job_b = _two_jobs()
    held = _contract('Astro Bot', jobs=[job_a, job_b])
    az_run, jobs_run = _runs(profile, AZ, JOBS)
    svc.assign(jobs_run, profile, job_b.slug, held)
    options = _options()
    pool, _ = planner.get_pool(profile, options)

    halves = planner.alternatives(pool, planner.run_states(profile, options.types), (AZ, 'A'),
                                  partners=[(JOBS, job_a.slug)])

    assert _ids_by_half(halves) == {'both': [], 'rest': [held.id], 'partner': []}


def test_all_badges_covers_started_and_types_in_the_cache_key():
    a = planner.PlannerOptions(badge_all=True, badge_started=True, badge_types=('developer',))
    b = planner.PlannerOptions(badge_all=True, badge_started=False)
    assert a.digest() == b.digest()


# --- badge weight: Only -----------------------------------------------------------------------------------

def test_bonus_and_priority_fill_a_square_no_badge_game_fits():
    profile = _member()
    badge_game = _contract('Astro Bot')
    plain = _contract('Bugsnax')
    series = _badge(badge_game)
    _runs(profile, AZ)
    for weight in (planner.WEIGHT_BONUS, planner.WEIGHT_PRIORITY):
        plan = _plan(profile, _options(types=(AZ,), badge_picks=(series.edition.id,), badge_weight=weight))
        assert plan.assignments[(AZ, 'A')] == badge_game.id
        assert plan.assignments[(AZ, 'B')] == plain.id, weight


def test_only_plans_badge_games_and_leaves_the_rest_open():
    profile = _member()
    badge_game = _contract('Astro Bot')
    _contract('Bugsnax')
    series = _badge(badge_game)
    _runs(profile, AZ)

    plan = _plan(profile, _options(types=(AZ,), badge_picks=(series.edition.id,), badge_weight=planner.WEIGHT_ONLY))

    assert plan.assignments == {(AZ, 'A'): badge_game.id}
    assert (AZ, 'B') in plan.empty


def test_a_must_have_beats_badge_games_only():
    profile = _member()
    series = _badge(_contract('Astro Bot'))
    plain = _contract('Bugsnax')
    _runs(profile, AZ)
    options = _options(types=(AZ,), badge_picks=(series.edition.id,), badge_weight=planner.WEIGHT_ONLY)

    plan = _plan(profile, options, planner.Tweaks(pins=frozenset({plain.id})))

    assert plan.assignments[(AZ, 'B')] == plain.id


def test_only_with_no_badges_selected_plans_like_any_other_weight():
    # "Only" with no badge source would plan nothing at all; it falls back to every game.
    profile = _member()
    plain = _contract('Bugsnax')
    _runs(profile, AZ)
    options = _options(types=(AZ,), badge_weight=planner.WEIGHT_ONLY)
    assert not options.badges_only
    assert _plan(profile, options).assignments == {(AZ, 'B'): plain.id}


def test_other_options_under_only_offer_badge_games_alone():
    profile = _member()
    badge_game = _contract('Astro Bot')
    _contract('Alto')
    series = _badge(badge_game)
    _runs(profile, AZ)
    options = _options(types=(AZ,), badge_picks=(series.edition.id,), badge_weight=planner.WEIGHT_ONLY)
    pool, _ = planner.get_pool(profile, options)
    states = planner.run_states(profile, options.types)

    assert _ids_by_half(planner.alternatives(pool, states, (AZ, 'A'), options=options))['rest'] == [badge_game.id]
    assert len(planner.alternatives(pool, states, (AZ, 'A'))['rest']) == 2   # without the answers: every game


def test_the_weight_is_not_part_of_the_pool_key():
    # Switching Bonus / Priority / Only re-solves the cached pool; it never rebuilds it.
    keys = {_options(badge_all=True, badge_weight=w).digest() for w in planner.BADGE_WEIGHTS}
    assert len(keys) == 1


@pytest.mark.parametrize('weight', [planner.WEIGHT_PRIORITY, planner.WEIGHT_ONLY])
def test_the_weight_means_nothing_without_a_badge_source(weight):
    # The None chip (or nothing ticked) greys the weight out; a Priority left set underneath must not still
    # reorder the solver's tiers.
    assert not _options(badge_weight=weight).stages_first
    assert _options(badge_weight=weight, badge_all=True).stages_first


def test_with_no_badges_a_priority_plan_is_solved_as_bonus(monkeypatch):
    profile = _member()
    _contract('Astro Bot')
    _runs(profile, AZ)
    seen = []
    real = planner.solve
    monkeypatch.setattr(planner, 'solve', lambda problem: (seen.append(problem.stages_first), real(problem))[1])
    _plan(profile, _options(types=(AZ,), badge_weight=planner.WEIGHT_PRIORITY))
    assert seen and not any(seen)


# --- unobtainable contracts are never suggested (owner, 2026-10-17) -----------------------------------------

def test_an_unobtainable_contract_never_reaches_the_pool_but_a_delisted_one_does():
    profile = _member()
    gone = _contract('Apex')
    gone.game.is_obtainable = False
    gone.game.save(update_fields=['is_obtainable'])
    delisted = _contract('Astro Bot')
    delisted.game.is_delisted = True
    delisted.game.save(update_fields=['is_delisted'])
    kept = _contract('Alto')
    _runs(profile, AZ)

    ids = _ids(planner._hydrate(planner._build_pool(profile, _options(types=(AZ,)))))

    assert gone.id not in ids and delisted.id in ids and kept.id in ids


def test_an_unobtainable_contract_can_still_be_a_must_have():
    # The hunter named it: they mean it, and the line carries the Unobtainable chip.
    profile = _member()
    gone = _contract('Apex')
    gone.game.is_obtainable = False
    gone.game.save(update_fields=['is_obtainable'])
    _runs(profile, AZ)
    plan = _plan(profile, _options(types=(AZ,)), tweaks=planner.Tweaks(pins=frozenset({gone.id})))
    assert plan.assignments == {(AZ, 'A'): gone.id}


def test_the_unobtainable_set_is_cached_site_wide():
    from django.core.cache import cache
    from challenges.services import picker
    gone = _contract('Apex')
    gone.game.is_obtainable = False
    gone.game.save(update_fields=['is_obtainable'])
    assert picker.unobtainable_contract_ids() == {gone.id}
    assert cache.get(picker.UNOBTAINABLE_CACHE_KEY) == [gone.id]


def test_the_unobtainable_set_matches_the_pickers_own_flag():
    # Parity with the chip (`picker._decorations`), across the shapes that differ: no games at all, mixed stacks,
    # all unobtainable.
    from challenges.services import picker
    no_games = _contract('Apex')
    no_games.game.delete()
    mixed = _contract('Astro Bot')
    mixed.game.is_obtainable = False
    mixed.game.save(update_fields=['is_obtainable'])
    GameFactory(concept=mixed.concept, title_platform=['PS4'], is_obtainable=True)
    gone = _contract('Alto')
    gone.game.is_obtainable = False
    gone.game.save(update_fields=['is_obtainable'])
    contracts = [no_games, mixed, gone]
    flagged = {cid for cid, row in picker.contract_rows(contracts).items() if 'unobtainable' in row['availability']}
    assert flagged == {gone.id}
    assert picker.unobtainable_contract_ids() & {c.id for c in contracts} == flagged


def test_an_unfinished_game_the_pool_leaves_out_keeps_its_stages_in_the_swap_solve():
    # A held game outside the pool (here: off the chosen platforms) reaches the swap solve through the pin read,
    # with its badge stages. Without them, Priority took a hunter's own badge game for a plain one and offered to
    # swap it for another badge game.
    profile = _member()
    held = _contract('Bastion', platforms=('PS4',))
    rival = _contract('Bloodborne')
    series = _badge(held, rival)
    az_run, = _runs(profile, AZ)
    svc.assign(az_run, profile, 'B', held)
    options = _options(types=(AZ,), platforms=('PS5',), badge_picks=(series.edition.id,),
                       badge_weight=planner.WEIGHT_PRIORITY)
    assert _plan(profile, options).swaps == {}


def test_a_contract_with_one_obtainable_way_through_is_not_unobtainable():
    from challenges.services import picker
    contract = _contract('Apex')
    contract.game.is_obtainable = False
    contract.game.save(update_fields=['is_obtainable'])
    GameFactory(concept=contract.concept, title_platform=['PS5'], is_obtainable=True)   # another stack, obtainable
    assert contract.id not in picker.unobtainable_contract_ids()
