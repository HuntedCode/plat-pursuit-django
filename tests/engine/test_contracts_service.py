"""Tests for the Contracts board (contracts_service.contracts_page).

The Contracts board lists live Contracts to pursue. Pins: the contract card shape (games as
the focal point + jobs + fixed-T reward split), the per-viewer status machine
(available / pursuing / claimable / accepted) derived from EXISTING EarnedContract rows
(never written on this read path), jobless/non-live contracts hidden, and claimable_count.
"""
import pytest

from trophies.models import Contract, ContractMembership, Job, Trophy
from trophies.services import contract_service
from trophies.services.contracts_service import (
    board_facets, build_contract_modal, claimable_count, contracts_page, suggest_relaxation,
)
from trophies.util_modules.constants import CONTRACT_XP_TOTAL
from tests.factories import (
    ConceptFactory, EarnedTrophyFactory, GameFactory, ProfileFactory, ProfileGameFactory,
)

pytestmark = pytest.mark.django_db


def _contract(slug, job_slugs=('gunslinger',), live=True, title='My Game'):
    contract = Contract.objects.create(name=slug, slug=slug, is_live=live)
    contract.jobs.set(Job.objects.filter(slug__in=job_slugs))
    concept = ConceptFactory(unified_title=title)
    game = GameFactory(concept=concept)
    ContractMembership.objects.create(contract=contract, concept=concept)
    return contract, concept, game


def _project(ctx, slug):
    return next(p for p in ctx['contracts'] if p['slug'] == slug)


def test_lists_live_project_with_games_elements_and_split():
    profile = ProfileFactory()
    _c, _concept, _game = _contract('p-list', ('gunslinger', 'mage'), title='Cool Game')

    ctx = contracts_page(profile)

    p = _project(ctx, 'p-list')
    assert p['name'] == 'p-list'            # the contract name wins over the member concept title
    assert p['game_count'] == 1
    assert len(p['elements']) == 2
    assert p['xp_total'] == CONTRACT_XP_TOTAL
    assert p['xp_each'] == CONTRACT_XP_TOTAL // 2
    assert len(p['ring_segments']) == 2                       # one SVG arc per element (the even split)
    assert {s['slug'] for s in p['ring_segments']} == {e['slug'] for e in p['elements']}  # tagged per job
    assert all('dash' in s and 'offset' in s and 'disc_slug' in s for s in p['ring_segments'])
    assert p['family_color'].startswith('var(--disc-')
    # gunslinger + mage are different families -> the accent bar is a multi-family gradient.
    assert p['family_gradient'].startswith('linear-gradient')


def test_jobless_project_is_hidden():
    profile = ProfileFactory()
    _contract('p-jobless', job_slugs=())  # no elements -> awards nothing
    _contract('p-keep')                   # a control that MUST still appear

    slugs = {p['slug'] for p in contracts_page(profile)['contracts']}

    assert 'p-keep' in slugs        # distinguishes "hidden" from "everything is gone"
    assert 'p-jobless' not in slugs


def test_non_live_project_is_excluded():
    profile = ProfileFactory()
    _contract('p-dormant', live=False)
    _contract('p-live')             # a control that MUST still appear

    slugs = {p['slug'] for p in contracts_page(profile)['contracts']}

    assert 'p-live' in slugs
    assert 'p-dormant' not in slugs


def test_status_available_when_untouched():
    profile = ProfileFactory()
    _contract('p-avail')

    p = _project(contracts_page(profile), 'p-avail')

    assert p['status'] == 'available'
    assert p['progress'] == 0


def test_status_pursuing_with_partial_progress():
    profile = ProfileFactory()
    _contract2 = _contract('p-pursue')
    _c, _concept, game = _contract2
    ProfileGameFactory(profile=profile, game=game, progress=60)

    p = _project(contracts_page(profile), 'p-pursue')

    assert p['status'] == 'pursuing'
    assert p['progress'] == 60


def test_status_pursuing_when_complete_but_reach_not_yet_stamped():
    """The documented seam: a member game is 100% but the sync hasn't run
    mark_contract_reached yet (no EarnedContract). The read-only render shows 'pursuing'
    at 100%, not 'claimable' -- the next sync flips it."""
    profile = ProfileFactory()
    _c, _concept, game = _contract('p-precommit')
    ProfileGameFactory(profile=profile, game=game, progress=100, has_plat=True)

    p = _project(contracts_page(profile), 'p-precommit')

    assert p['status'] == 'pursuing'
    assert p['progress'] == 100


def test_xp_total_override_drives_reward_split():
    profile = ProfileFactory()
    contract, _concept, _game = _contract('p-override', ('gunslinger', 'mage'))
    contract.xp_total_override = 1000
    contract.save(update_fields=['xp_total_override'])

    p = _project(contracts_page(profile), 'p-override')

    assert p['xp_total'] == 1000           # the override, not CONTRACT_XP_TOTAL
    assert p['xp_each'] == 500             # 1000 // 2 elements


def test_status_claimable_when_reached_not_accepted():
    profile = ProfileFactory()
    contract, concept, game = _contract('p-claim')
    plat = Trophy.objects.create(game=game, trophy_id=1, trophy_type='platinum', trophy_name='Plat')
    EarnedTrophyFactory(profile=profile, trophy=plat, earned=True)
    ProfileGameFactory(profile=profile, game=game, progress=100, has_plat=True)
    contract_service.mark_contract_reached(profile, contract)  # reached, not accepted

    ctx = contracts_page(profile)
    p = _project(ctx, 'p-claim')

    assert p['status'] == 'claimable'
    assert claimable_count(profile) == 1


def test_status_accepted_after_accept():
    profile = ProfileFactory()
    contract, concept, game = _contract('p-accepted')
    plat = Trophy.objects.create(game=game, trophy_id=1, trophy_type='platinum', trophy_name='Plat')
    EarnedTrophyFactory(profile=profile, trophy=plat, earned=True)
    ProfileGameFactory(profile=profile, game=game, progress=100, has_plat=True)
    contract_service.mark_contract_reached(profile, contract)
    contract_service.accept_contract(profile, contract)

    ctx = contracts_page(profile)
    p = _project(ctx, 'p-accepted')

    assert p['status'] == 'accepted'
    assert claimable_count(profile) == 0


def test_anonymous_viewer_sees_available_projects():
    _contract('p-anon')

    ctx = contracts_page(None)

    assert _project(ctx, 'p-anon')['status'] == 'available'


# --- Server-side board (annotate/filter/sort/paginate in the DB) ----------------------

def _slugs(page):
    return [c['slug'] for c in page['contracts']]


def _find(page, slug):
    return next((c for c in page['contracts'] if c['slug'] == slug), None)


def test_server_status_derived_in_sql_matches_all_states():
    """The DB-annotated status must resolve every state (available/pursuing/claimable/accepted)."""
    profile = ProfileFactory()
    _contract('s-avail')
    _c, _con, g_p = _contract('s-pursue')
    ProfileGameFactory(profile=profile, game=g_p, progress=60)
    c_cl, _con2, g_cl = _contract('s-claim')
    plat = Trophy.objects.create(game=g_cl, trophy_id=1, trophy_type='platinum', trophy_name='Plat')
    EarnedTrophyFactory(profile=profile, trophy=plat, earned=True)
    ProfileGameFactory(profile=profile, game=g_cl, progress=100, has_plat=True)
    contract_service.mark_contract_reached(profile, c_cl)
    c_ac, _con3, g_ac = _contract('s-acc')
    plat2 = Trophy.objects.create(game=g_ac, trophy_id=1, trophy_type='platinum', trophy_name='Plat')
    EarnedTrophyFactory(profile=profile, trophy=plat2, earned=True)
    ProfileGameFactory(profile=profile, game=g_ac, progress=100, has_plat=True)
    contract_service.mark_contract_reached(profile, c_ac)
    contract_service.accept_contract(profile, c_ac)

    page = contracts_page(profile)
    assert _find(page, 's-avail')['status'] == 'available'
    assert _find(page, 's-pursue')['status'] == 'pursuing' and _find(page, 's-pursue')['progress'] == 60
    assert _find(page, 's-claim')['status'] == 'claimable'
    assert _find(page, 's-acc')['status'] == 'accepted'
    assert claimable_count(profile) == 1


def test_server_status_filter():
    profile = ProfileFactory()
    _contract('f-avail')
    _c, _con, g = _contract('f-pursue')
    ProfileGameFactory(profile=profile, game=g, progress=40)
    slugs = _slugs(contracts_page(profile, status='pursuing'))
    assert 'f-pursue' in slugs and 'f-avail' not in slugs


def test_server_discipline_filter():
    profile = ProfileFactory()
    _contract('d-combat', ('gunslinger',))   # combat
    _contract('d-heart', ('mage',))          # heart
    slugs = _slugs(contracts_page(profile, disciplines=['combat']))
    assert 'd-combat' in slugs and 'd-heart' not in slugs


def test_server_search_by_name_and_game_title():
    profile = ProfileFactory()
    _c, _con, game = _contract('srch-a', title='Zelda Quest')
    _contract('srch-b', title='Other')
    assert 'srch-a' in _slugs(contracts_page(profile, q='srch-a'))                  # by contract name
    assert 'srch-a' in _slugs(contracts_page(profile, q=game.title_name.lower()))   # by member game title
    assert 'srch-b' not in _slugs(contracts_page(profile, q='srch-a'))


def test_server_relevance_orders_untouched_by_weak_discipline():
    profile = ProfileFactory()
    _contract('rel-combat', ('gunslinger',))   # combat
    _contract('rel-heart', ('mage',))          # heart
    page = contracts_page(profile, disc_levels={'combat': 10, 'heart': 0})   # weak in heart
    slugs = _slugs(page)
    assert slugs.index('rel-heart') < slugs.index('rel-combat')


def test_server_pagination():
    profile = ProfileFactory()
    for i in range(30):
        _contract('pg-%02d' % i)
    p1 = contracts_page(profile, page=1)
    assert len(p1['contracts']) == 24 and p1['has_next'] and p1['total'] == 30
    p2 = contracts_page(profile, page=2)
    assert len(p2['contracts']) == 6 and not p2['has_next']
    p_end = contracts_page(profile, page=999)   # past the end -> empty so infinite scroll stops
    assert p_end['contracts'] == [] and not p_end['has_next']


def test_modal_builder_has_per_game_progress():
    profile = ProfileFactory()
    _c, _con, game = _contract('m-1', ('gunslinger', 'mage'), title='Modal Game')
    ProfileGameFactory(profile=profile, game=game, progress=50)
    modal = build_contract_modal(profile, 'm-1')
    assert modal['name'] == 'm-1' and modal['game_count'] == 1 and len(modal['elements']) == 2
    assert modal['games'][0]['profile_game'].progress == 50
    assert build_contract_modal(profile, 'nope') is None


def test_server_platform_default_is_modern():
    profile = ProfileFactory()
    _contract('plat-ps5')                                   # PS5 (factory default)
    _c, _con, g_ps3 = _contract('plat-ps3')
    g_ps3.title_platform = ['PS3']
    g_ps3.save(update_fields=['title_platform'])
    slugs = _slugs(contracts_page(profile))                 # default = current-gen
    assert 'plat-ps5' in slugs and 'plat-ps3' not in slugs
    assert 'plat-ps3' in _slugs(contracts_page(profile, platforms=['PS3']))   # opt-in to legacy


def test_server_job_drilldown():
    profile = ProfileFactory()
    _contract('job-gs', ('gunslinger', 'mage'))
    _contract('job-mage', ('mage',))
    slugs = _slugs(contracts_page(profile, jobs=['gunslinger']))
    assert 'job-gs' in slugs and 'job-mage' not in slugs


def test_server_multi_job_is_anded():
    profile = ProfileFactory()
    _contract('mj-both', ('gunslinger', 'mage'))   # levels BOTH
    _contract('mj-one', ('gunslinger',))           # only one of them
    slugs = _slugs(contracts_page(profile, jobs=['gunslinger', 'mage']))
    assert 'mj-both' in slugs and 'mj-one' not in slugs   # AND (a game with both), not OR


def test_server_sort_by_job_count():
    profile = ProfileFactory()
    _contract('jc-many', ('gunslinger', 'mage'))   # 2 jobs
    _contract('jc-few', ('gunslinger',))           # 1 job
    most = _slugs(contracts_page(profile, sort='jobs'))
    assert most.index('jc-many') < most.index('jc-few')
    fewest = _slugs(contracts_page(profile, sort='fewest'))
    assert fewest.index('jc-few') < fewest.index('jc-many')


def test_server_pushing_orders_untouched_by_strong_discipline():
    profile = ProfileFactory()
    _contract('push-combat', ('gunslinger',))   # combat
    _contract('push-heart', ('mage',))          # heart
    page = contracts_page(profile, disc_levels={'combat': 10, 'heart': 0}, sort='pushing')  # strong in combat
    slugs = _slugs(page)
    assert slugs.index('push-combat') < slugs.index('push-heart')   # "keep pushing" = strongest first


def test_board_facets_counts_status_and_platforms():
    profile = ProfileFactory()
    _contract('f-combat', ('gunslinger',))   # combat, PS5 (factory default)
    _contract('f-heart', ('mage',))          # heart, PS5
    facets = board_facets(profile)
    assert facets['status']['available'] == 2 and facets['status']['all'] == 2
    assert facets['platform']['PS5'] == 2 and facets['platform']['PS3'] == 0
    # Status counts exclude their OWN dimension but respect the others: filtering to combat leaves 1.
    scoped = board_facets(profile, disciplines=['combat'])
    assert scoped['status']['all'] == 1
    # Platform counts ignore the platform filter -- PS5's total shows even while defaulted to legacy.
    assert board_facets(profile, platforms=['PS3'])['platform']['PS5'] == 2


def test_board_facets_discipline_and_job_counts():
    profile = ProfileFactory()
    _contract('dj-a', ('gunslinger', 'mage'))   # gunslinger=combat, mage=heart
    _contract('dj-b', ('gunslinger',))          # combat only
    facets = board_facets(profile)
    assert facets['job']['gunslinger'] == 2 and facets['job']['mage'] == 1
    assert facets['discipline']['combat'] == 2 and facets['discipline']['heart'] == 1


def test_board_facets_job_counts_refine_with_selection():
    profile = ProfileFactory()
    _contract('r-both', ('gunslinger', 'mage'))   # levels both
    _contract('r-gun', ('gunslinger',))           # only gunslinger
    # With gunslinger selected, each job count is the REFINEMENT: of the current results (both have
    # gunslinger), how many ALSO level this job. mage narrows to the one contract that has both.
    f = board_facets(profile, jobs=['gunslinger'])
    assert f['job']['gunslinger'] == 2   # the selection itself: all current results have it
    assert f['job']['mage'] == 1         # only r-both would survive adding mage
    assert f['discipline']['heart'] == 1  # same refinement for the discipline (head) count


def test_board_facets_status_respects_current_gen_default():
    profile = ProfileFactory()
    _contract('bf-ps5', ('gunslinger',))                       # PS5 (factory default)
    _c, _con, g3 = _contract('bf-ps3', ('mage',))
    g3.title_platform = ['PS3']
    g3.save(update_fields=['title_platform'])
    facets = board_facets(profile)                             # default -> current-gen (matches the board total)
    assert facets['status']['all'] == 1                        # only the PS5 contract is on the default board
    assert facets['platform']['PS3'] == 1                      # but the PS3 chip still shows its true total


def test_suggest_relaxation_offers_widening_platforms():
    profile = ProfileFactory()
    _c, _con, g3 = _contract('sw-ps3', ('gunslinger',))
    g3.title_platform = ['PS3']
    g3.save(update_fields=['title_platform'])
    # Default board is current-gen, so the lone PS3 contract yields 0. Dropping the job stays current-gen
    # (still 0); widening platforms reveals it -> that's the suggestion.
    s = suggest_relaxation(profile, jobs=['gunslinger'])       # platforms=None -> current-gen
    assert s is not None and s['kind'] == 'platform' and s['count'] == 1


def test_suggest_relaxation_picks_the_best_drop():
    profile = ProfileFactory()
    _contract('sug-combat', ('gunslinger',))   # levels gunslinger only
    # Requiring BOTH gunslinger and mage returns nothing; dropping mage frees the one combat contract.
    s = suggest_relaxation(profile, jobs=['gunslinger', 'mage'])
    assert s is not None
    assert s['kind'] == 'job' and s['value'] == 'mage' and s['count'] == 1


def test_suggest_relaxation_none_when_nothing_helps():
    profile = ProfileFactory()
    _contract('sug-solo', ('gunslinger',))   # combat, PS5
    # mage (no such contract) AND PS3 (none): dropping EITHER single filter still yields 0 -> no suggestion.
    assert suggest_relaxation(profile, jobs=['mage'], platforms=['PS3']) is None


def test_server_sort_by_progress_and_name():
    profile = ProfileFactory()
    _c, _con, g_hi = _contract('prog-hi')
    ProfileGameFactory(profile=profile, game=g_hi, progress=80)
    _c2, _con2, g_lo = _contract('prog-lo')
    ProfileGameFactory(profile=profile, game=g_lo, progress=20)
    prog = _slugs(contracts_page(profile, sort='progress'))
    assert prog.index('prog-hi') < prog.index('prog-lo')
    _contract('zzz-last')
    _contract('aaa-first')
    names = _slugs(contracts_page(profile, sort='name'))
    assert names.index('aaa-first') < names.index('zzz-last')
