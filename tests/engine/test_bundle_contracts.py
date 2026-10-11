"""Bundle-only contracts on the Career board, the modal, the covers and the reverse map.

A bundle-only contract has no raw IGDB id (`igdb_id` NULL): its members are the concepts of its
`ContractBundle`s (an episodic season, a collection), satisfied when EVERY concept of one bundle is
done. The board read members from the IGDB key alone, so these rendered with no games, no art, no
platforms (hidden by the default platform filter), a one-bar tier split, and progress that never moved.
Owner decisions (2026-10-10): bundle games apply ONLY to bundle-only contracts (a normal contract with a
satisfier bundle is unchanged); progress is the best bundle's average over its concepts; an episode's own
game page maps to its bundle contract when it has no IGDB-keyed contract of its own.
"""
import itertools

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from trophies.models import Contract, ContractBundle, EarnedContract, Job
from trophies.services import contract_service
from trophies.services.contracts_service import (
    board_facets, build_contract_modal, contracts_page,
)
from tests.factories import (
    ConceptFactory, GameFactory, IGDBMatchFactory, ProfileFactory, ProfileGameFactory, TrophyFactory,
)

pytestmark = pytest.mark.django_db

_seq = itertools.count(95001)


def _bundle_contract(name, *bundles, live=True):
    """A bundle-only contract with one ContractBundle per list of concepts."""
    c = Contract.objects.create(name=name, slug=name, igdb_id=None, is_live=live)
    c.jobs.set(Job.objects.filter(slug='gunslinger'))
    for i, concepts in enumerate(bundles):
        b = ContractBundle.objects.create(contract=c, label=f'{name} {i}', sort_order=i)
        b.concepts.set(concepts)
    return c


def _episode(title='Episode', platform='PS5', played=0, plat=False):
    """A concept with one game. Deliberately NOT anchored or trusted: bundle concepts are ungated."""
    concept = ConceptFactory(unified_title=title)
    game = GameFactory(concept=concept, title_name=title, title_platform=[platform], played_count=played,
                       title_icon_url=f'https://cdn.example.test/{played}.png')
    if plat:
        TrophyFactory(game=game, trophy_type='platinum')
    return concept, game


def _card(profile, slug, **filters):
    filters.setdefault('platforms', [])
    return next((c for c in contracts_page(profile, **filters)['contracts'] if c['slug'] == slug), None)


# -- progress: the best bundle's average over its concepts ------------------------------------------

def test_a_bundles_progress_is_the_average_over_its_episodes_with_unplayed_at_zero():
    profile = ProfileFactory()
    (a, ga), (b, _gb) = _episode('Ep A'), _episode('Ep B')
    _bundle_contract('season-avg', [a, b])
    ProfileGameFactory(profile=profile, game=ga, progress=100)   # one of two episodes done, one unplayed

    card = _card(profile, 'season-avg')
    assert card['status'] == 'pursuing' and card['progress'] == 50, 'one finished episode is not the season'


def test_a_bundle_never_reads_100_until_every_episode_is_done():
    """Floored: 100, 100, 99 averages 99.67, which must read 99. Rounded (or cast, which rounds), it would
    read 100: a season shown finished with an episode still open."""
    profile = ProfileFactory()
    eps = [_episode(f'Ep {i}') for i in range(3)]
    _bundle_contract('season-floor', [c for c, _g in eps])
    for (_c, g), p in zip(eps, (100, 100, 99)):
        ProfileGameFactory(profile=profile, game=g, progress=p)
    assert _card(profile, 'season-floor')['progress'] == 99


def test_the_best_bundle_wins_and_a_concepts_best_game_counts():
    """Bundles are alternatives (any one completes the contract), so progress is the best bundle's. A
    concept with two games (PS4 and PS5 stacks) counts at its best game."""
    profile = ProfileFactory()
    (a, ga), (b, _gb) = _episode('Weak A'), _episode('Weak B')
    (c, gc) = _episode('Strong C')
    gc_ps4 = GameFactory(concept=c, title_name='Strong C (PS4)', title_platform=['PS4'])
    _bundle_contract('season-best', [a, b], [c])
    ProfileGameFactory(profile=profile, game=ga, progress=40)      # weak bundle: (40 + 0) / 2 = 20
    ProfileGameFactory(profile=profile, game=gc, progress=10)      # strong bundle: concept C at its best game
    ProfileGameFactory(profile=profile, game=gc_ps4, progress=80)
    assert _card(profile, 'season-best')['progress'] == 80


def test_no_play_at_all_reads_available():
    profile = ProfileFactory()
    _bundle_contract('season-none', [_episode('Ep X')[0], _episode('Ep Y')[0]])
    card = _card(profile, 'season-none')
    assert card['status'] == 'available' and card['progress'] == 0


# -- the board finds and renders it -----------------------------------------------------------------

def test_a_bundle_contract_is_on_the_default_board_with_its_games():
    """PS5 episodes put it on the default (current-gen) board, in the facets, in a search for an episode
    title, and on the card: cover, game count, platforms, and the platinum split its games define."""
    profile = ProfileFactory()
    (a, _ga), (b, _gb) = _episode('Season Opener', played=5, plat=True), _episode('Season Finale', played=9, plat=True)
    _bundle_contract('season-board', [a, b])

    card = _card(profile, 'season-board', platforms=None)   # None = the current-gen default
    assert card is not None, 'a PS5 bundle contract is missing from the default board'
    assert card['game_count'] == 2 and card['cover_game'] is not None
    assert card['cover_game'].title_name in ('Season Opener', 'Season Finale')
    assert [cell['lit'] for cell in card['platform_cells']][0] is True, 'its PS5 pill is dark'
    assert card['has_plat'] is True and card['plat_xp'] > 0, 'its episodes define a platinum'

    assert _card(profile, 'season-board', q='finale') is not None, 'searching an episode title misses it'
    assert board_facets(profile)['platform']['PS5'] >= 1


def test_the_modal_lists_the_bundle_games():
    (a, _ga), (b, _gb) = _episode('Modal One'), _episode('Modal Two')
    _bundle_contract('season-modal', [a, b])
    p = build_contract_modal(ProfileFactory(), 'season-modal')
    assert sorted(e['game'].title_name for e in p['games']) == ['Modal One', 'Modal Two']


def test_a_normal_contract_with_a_satisfier_bundle_is_unchanged():
    """Bundle-only, by decision: an IGDB-keyed contract that also carries a collection bundle keeps showing
    its own game (cover, count, platforms, progress), not the collection's."""
    profile = ProfileFactory()
    n = next(_seq)
    contract = Contract.objects.create(name='home-plus-bundle', slug='home-plus-bundle', igdb_id=n, is_live=True)
    contract.jobs.set(Job.objects.filter(slug='gunslinger'))
    home = ConceptFactory(anchor_migration_completed_at=timezone.now())
    IGDBMatchFactory(concept=home, igdb_id=n)
    home_game = GameFactory(concept=home, title_name='The Game', title_platform=['PS4'])
    collection, coll_game = _episode('The Collection', platform='PS5', played=99)
    ContractBundle.objects.create(contract=contract, label='covers this').concepts.set([collection])
    ProfileGameFactory(profile=profile, game=coll_game, progress=100)

    card = _card(profile, 'home-plus-bundle')
    assert card['game_count'] == 1 and card['cover_game'].pk == home_game.pk
    assert card['progress'] == 0, "the collection's progress leaked onto the home contract"
    assert _card(profile, 'home-plus-bundle', platforms=['PS5']) is None, "the collection's platform leaked"


def test_a_claimable_bundle_contract_is_on_the_claim_board():
    """The claim banner counts it, and the board it opens (Ready to Claim, every platform) now shows it."""
    from trophies.util_modules.constants import ALL_PLATFORMS

    profile = ProfileFactory()
    contract = _bundle_contract('season-claim', [_episode('Claim Ep')[0]])
    EarnedContract.objects.create(profile=profile, contract=contract, has_platinum=False,
                                  full_reached_at=timezone.now())
    assert contract_service.claimable_summary(profile)['count'] == 1
    board = contracts_page(profile, status='claimable', platforms=list(ALL_PLATFORMS))
    assert [c['slug'] for c in board['contracts']] == ['season-claim']


# -- covers and the reverse map ---------------------------------------------------------------------

def test_the_hero_and_the_announcement_use_the_most_played_bundle_game():
    from core.services.contract_announcer import cover_url_for
    from trophies.services import new_contracts_modal

    (a, ga), (b, gb) = _episode('Quiet Ep', played=1), _episode('Loud Ep', played=50)
    contract = _bundle_contract('season-art', [a, b])
    url = gb.display_image_url
    assert url and url.startswith('http') and url != ga.display_image_url, 'fixture: two distinct covers'
    assert cover_url_for(contract) == url
    assert new_contracts_modal._hero_covers([contract]) == {contract.pk: url}


def test_an_episodes_game_page_maps_to_its_bundle_contract():
    """Home wins; else the lowest-id bundle-only contract; a non-live one is skipped when live_only."""
    ep, _g = _episode('Mapped Ep')
    first = _bundle_contract('season-map-a', [ep])
    _bundle_contract('season-map-b', [ep])
    hidden_ep, _hg = _episode('Hidden Ep')
    _bundle_contract('season-map-hidden', [hidden_ep], live=False)

    mapping = contract_service.contract_by_concept_map([ep.id, hidden_ep.id])
    assert mapping[ep.id].pk == first.pk
    assert hidden_ep.id not in mapping

    n = next(_seq)
    home_contract = Contract.objects.create(name='ep-home', slug='ep-home', igdb_id=n, is_live=True)
    ep.anchor_migration_completed_at = timezone.now()
    ep.save(update_fields=['anchor_migration_completed_at'])
    IGDBMatchFactory(concept=ep, igdb_id=n)
    assert contract_service.contract_by_concept_map([ep.id])[ep.id].pk == home_contract.pk, 'the igdb home must win'


# -- cost: no DISTINCT, and no per-contract queries --------------------------------------------------

def test_bundle_contracts_add_no_distinct_and_no_per_card_queries():
    """The bundle halves are hashed id sets and CASE-chosen subqueries, never joins: no SELECT DISTINCT
    anywhere on the path, and a page costs the same number of queries with one bundle contract or four."""
    from tests.engine.test_contracts_service import _contract

    profile = ProfileFactory()
    _contract('nd-home')

    def capture():
        with CaptureQueriesContext(connection) as ctx:
            contracts_page(profile, platforms=['PS5'], q='ep', sort='progress', status='')
            contracts_page(profile, platforms=['PS5'], status='claimable')
            board_facets(profile, platforms=['PS5'], q='ep')
        return ctx.captured_queries

    _bundle_contract('cost-1', [_episode('Cost Ep 1')[0]])
    one = capture()
    for i in range(2, 5):
        _bundle_contract(f'cost-{i}', [_episode(f'Cost Ep {i}')[0], _episode(f'Cost Ep {i}b')[0]])
    four = capture()

    assert not [q for q in four if 'SELECT DISTINCT' in q['sql'].upper()]
    assert len(four) == len(one), f'{len(one)} queries with one bundle contract, {len(four)} with four'


def test_an_empty_bundle_does_not_hide_a_real_ones_progress():
    """Bundles are sorted by their average to pick the best. An empty bundle's average is NULL, and
    Postgres sorts NULL FIRST in a descending order, so without `nulls_last` it would win and read 0."""
    profile = ProfileFactory()
    a, ga = _episode('Real Ep')
    _bundle_contract('season-empty', [], [a])   # an empty bundle beside a real one
    ProfileGameFactory(profile=profile, game=ga, progress=60)
    assert _card(profile, 'season-empty')['progress'] == 60


def _normal_contract_with_satisfier(name, collection_concept):
    n = next(_seq)
    contract = Contract.objects.create(name=name, slug=name, igdb_id=n, is_live=True)
    contract.jobs.set(Job.objects.filter(slug='gunslinger'))
    home = ConceptFactory(anchor_migration_completed_at=timezone.now())
    IGDBMatchFactory(concept=home, igdb_id=n)
    GameFactory(concept=home, title_name=f'{name} home', title_platform=['PS4'])
    ContractBundle.objects.create(contract=contract, label='covers this').concepts.set([collection_concept])
    return contract


def test_a_normal_contracts_satisfier_bundle_defines_no_platinum_on_its_card():
    """Bundle-only, by decision, for the platinum split too: a normal contract whose home game has no
    platinum keeps its one-bar card even when the collection that covers it has one."""
    collection, _g = _episode('Platted Collection', plat=True)
    _normal_contract_with_satisfier('home-noplat', collection)
    card = _card(ProfileFactory(), 'home-noplat')
    assert card['has_plat'] is False, "the satisfier bundle's platinum leaked onto the home contract's card"


def test_a_concept_only_in_a_normal_contracts_satisfier_bundle_maps_to_nothing():
    """The reverse map's bundle pass is for bundle-only contracts: a collection that merely satisfies a
    normal contract is not that contract's game, so its game page shows no contract."""
    collection, _g = _episode('Unowned Collection')
    _normal_contract_with_satisfier('home-satisfied', collection)
    assert collection.id not in contract_service.contract_by_concept_map([collection.id])


def test_a_concept_whose_home_contract_is_unpublished_maps_to_its_live_bundle_contract():
    """With its own contract not live, the igdb pass resolves nothing on a live-only read, so the concept
    falls through to a live bundle-only contract that lists it (as sync would credit), and maps back to its
    home once that is published."""
    ep, _g = _episode('Draft Home Ep')
    n = next(_seq)
    home = Contract.objects.create(name='draft-home', slug='draft-home', igdb_id=n, is_live=False)
    ep.anchor_migration_completed_at = timezone.now()
    ep.save(update_fields=['anchor_migration_completed_at'])
    IGDBMatchFactory(concept=ep, igdb_id=n)
    season = _bundle_contract('season-fallthrough', [ep])

    assert contract_service.contract_by_concept_map([ep.id])[ep.id].pk == season.pk
    home.is_live = True
    home.save(update_fields=['is_live'])
    assert contract_service.contract_by_concept_map([ep.id])[ep.id].pk == home.pk
