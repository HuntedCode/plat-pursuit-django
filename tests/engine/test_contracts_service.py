"""Tests for contracts_service.build_contracts_context.

The Contracts board lists live Contracts to pursue. Pins: the contract shape (games as
the focal point + jobs + fixed-T reward split), the per-viewer status machine
(available / pursuing / claimable / accepted) derived from EXISTING EarnedContract rows
(never written on this read path), jobless/non-live contracts hidden, and claimable_count.
"""
import pytest

from trophies.models import Contract, ContractMembership, Job, Trophy
from trophies.services import contract_service
from trophies.services.contracts_service import (
    build_contract_modal, build_contracts_context, claimable_count, contracts_page,
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
    _c, _concept, game = _contract('p-list', ('gunslinger', 'mage'), title='Cool Game')

    ctx = build_contracts_context(profile)

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
    # toolbar fodder: lowercased search haystack (contract name + game title) + distinct disciplines
    assert 'p-list' in p['search_text'] and game.title_name.lower() in p['search_text']
    assert p['discipline_slugs'] == ['combat', 'heart']   # gunslinger=combat, mage=heart, sorted


def test_jobless_project_is_hidden():
    profile = ProfileFactory()
    _contract('p-jobless', job_slugs=())  # no elements -> awards nothing
    _contract('p-keep')                   # a control that MUST still appear

    slugs = {p['slug'] for p in build_contracts_context(profile)['contracts']}

    assert 'p-keep' in slugs        # distinguishes "hidden" from "everything is gone"
    assert 'p-jobless' not in slugs


def test_non_live_project_is_excluded():
    profile = ProfileFactory()
    _contract('p-dormant', live=False)
    _contract('p-live')             # a control that MUST still appear

    slugs = {p['slug'] for p in build_contracts_context(profile)['contracts']}

    assert 'p-live' in slugs
    assert 'p-dormant' not in slugs


def test_status_available_when_untouched():
    profile = ProfileFactory()
    _contract('p-avail')

    p = _project(build_contracts_context(profile), 'p-avail')

    assert p['status'] == 'available'
    assert p['progress'] == 0


def test_status_pursuing_with_partial_progress():
    profile = ProfileFactory()
    _contract2 = _contract('p-pursue')
    _c, _concept, game = _contract2
    ProfileGameFactory(profile=profile, game=game, progress=60)

    p = _project(build_contracts_context(profile), 'p-pursue')

    assert p['status'] == 'pursuing'
    assert p['progress'] == 60


def test_status_pursuing_when_complete_but_reach_not_yet_stamped():
    """The documented seam: a member game is 100% but the sync hasn't run
    mark_contract_reached yet (no EarnedContract). The read-only render shows 'pursuing'
    at 100%, not 'claimable' -- the next sync flips it."""
    profile = ProfileFactory()
    _c, _concept, game = _contract('p-precommit')
    ProfileGameFactory(profile=profile, game=game, progress=100, has_plat=True)

    p = _project(build_contracts_context(profile), 'p-precommit')

    assert p['status'] == 'pursuing'
    assert p['progress'] == 100
    assert p['completed'] is True


def test_xp_total_override_drives_reward_split():
    profile = ProfileFactory()
    contract, _concept, _game = _contract('p-override', ('gunslinger', 'mage'))
    contract.xp_total_override = 1000
    contract.save(update_fields=['xp_total_override'])

    p = _project(build_contracts_context(profile), 'p-override')

    assert p['xp_total'] == 1000           # the override, not CONTRACT_XP_TOTAL
    assert p['xp_each'] == 500             # 1000 // 2 elements


def test_status_claimable_when_reached_not_accepted():
    profile = ProfileFactory()
    contract, concept, game = _contract('p-claim')
    plat = Trophy.objects.create(game=game, trophy_id=1, trophy_type='platinum', trophy_name='Plat')
    EarnedTrophyFactory(profile=profile, trophy=plat, earned=True)
    ProfileGameFactory(profile=profile, game=game, progress=100, has_plat=True)
    contract_service.mark_contract_reached(profile, contract)  # reached, not accepted

    ctx = build_contracts_context(profile)
    p = _project(ctx, 'p-claim')

    assert p['status'] == 'claimable'
    assert ctx['claimable_count'] == 1


def test_status_accepted_after_accept():
    profile = ProfileFactory()
    contract, concept, game = _contract('p-accepted')
    plat = Trophy.objects.create(game=game, trophy_id=1, trophy_type='platinum', trophy_name='Plat')
    EarnedTrophyFactory(profile=profile, trophy=plat, earned=True)
    ProfileGameFactory(profile=profile, game=game, progress=100, has_plat=True)
    contract_service.mark_contract_reached(profile, contract)
    contract_service.accept_contract(profile, contract)

    ctx = build_contracts_context(profile)
    p = _project(ctx, 'p-accepted')

    assert p['status'] == 'accepted'
    assert ctx['claimable_count'] == 0


def test_anonymous_viewer_sees_available_projects():
    _contract('p-anon')

    ctx = build_contracts_context(None)

    assert _project(ctx, 'p-anon')['status'] == 'available'


# --- Server-side board (annotate/filter/sort/paginate in the DB) ----------------------

def _slugs(page):
    return [c['slug'] for c in page['contracts']]


def _find(page, slug):
    return next((c for c in page['contracts'] if c['slug'] == slug), None)


def test_server_status_derived_in_sql_matches_all_states():
    """The DB-annotated status must equal the Python _project_status for every state."""
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
    slugs = _slugs(contracts_page(profile, discipline='combat'))
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
    slugs = _slugs(contracts_page(profile, job='gunslinger'))
    assert 'job-gs' in slugs and 'job-mage' not in slugs


def test_server_sort_by_xp():
    profile = ProfileFactory()
    c_hi, _con, _g = _contract('sort-hi')
    c_hi.xp_total_override = 9999
    c_hi.save(update_fields=['xp_total_override'])
    _contract('sort-lo')
    slugs = _slugs(contracts_page(profile, sort='xp'))
    assert slugs.index('sort-hi') < slugs.index('sort-lo')


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
