"""Players counted once per game page (DS-08, docs/design/reviews/discovery.md).

`Concept.page_player_count` is the distinct players across every list on the concept's game page, filled
nightly by `recompute_tag_covers`; `Game.page_players` floors it with the live list counts. A game's Browse
card and its Game page both read it, so they agree, and a hunter who owns two versions counts once.
"""
import pytest
from django.core.management import call_command
from django.urls import reverse

from tests.factories import ConceptFactory, GameFactory, IGDBMatchFactory, ProfileFactory, ProfileGameFactory
from trophies.models import Concept

pytestmark = pytest.mark.django_db


def _recompute():
    call_command('recompute_tag_covers', verbosity=0)


def _count(concept):
    return Concept.objects.get(pk=concept.pk).page_player_count


def _own(profile, *games):
    for game in games:
        ProfileGameFactory(profile=profile, game=game)


def _igdb_page(igdb_id, status='auto_accepted', title='Split Game'):
    concept = ConceptFactory(unified_title=title)
    IGDBMatchFactory(concept=concept, igdb_id=igdb_id, status=status)
    return concept


def test_a_hunter_with_two_versions_counts_once():
    concept = ConceptFactory()
    ps4 = GameFactory(concept=concept, title_platform=['PS4'])
    ps5 = GameFactory(concept=concept, title_platform=['PS5'])
    both, ps4_only = ProfileFactory(), ProfileFactory()
    _own(both, ps4, ps5)
    _own(ps4_only, ps4)

    _recompute()

    assert _count(concept) == 2, 'summing the lists would say 3'


def test_an_igdb_page_counts_every_concept_on_it_including_untrusted_members():
    """The igdb page shows every list whose concept's match carries the id, trusted or not
    (GamePageView._resolve), so its count covers them all, and every trusted concept on it agrees."""
    first = _igdb_page(777001)
    second = _igdb_page(777001, title='Split Game DLC')      # a deliberate trophy-count split, same page
    member = _igdb_page(777001, status='pending', title='Split Game (untrusted)')
    g1 = GameFactory(concept=first)
    g2 = GameFactory(concept=second)
    g3 = GameFactory(concept=member)
    a, b, c = ProfileFactory(), ProfileFactory(), ProfileFactory()
    _own(a, g1, g2)          # owns both halves of the split: one player
    _own(b, g2)
    _own(c, g3)              # only on the untrusted member's list, which the igdb page still shows

    _recompute()

    assert _count(first) == _count(second) == 3
    # The untrusted concept's own card links its c/ page, which shows only its own list.
    assert _count(member) == 1


def test_lists_without_an_np_id_are_not_on_the_page_so_not_counted():
    concept = ConceptFactory()
    listed = GameFactory(concept=concept)
    unlisted = GameFactory(concept=concept, np_communication_id='')
    _own(ProfileFactory(), listed)
    _own(ProfileFactory(), unlisted)

    _recompute()

    assert _count(concept) == 1


def test_a_page_that_loses_its_players_resets_to_zero():
    concept = ConceptFactory()
    game = GameFactory(concept=concept)
    _own(ProfileFactory(), game)
    _recompute()
    assert _count(concept) == 1

    game.played_by.all().delete()
    _recompute()

    assert _count(concept) == 0


def test_page_players_is_floored_by_the_live_list_count():
    """Before the first nightly run a new page stores 0; it must read as its list's live count, not 0."""
    game = GameFactory(concept=ConceptFactory(page_player_count=0), played_count=40)
    assert game.page_players == 40

    game.concept.page_player_count = 55
    assert game.page_players == 55

    assert GameFactory(concept=None, played_count=7).page_players == 7


def test_the_card_and_the_game_page_show_the_same_number(client):
    """They disagreed (60 on the card, 229 on the page for one game): the card showed one list, the page
    summed every list."""
    concept = _igdb_page(778001, title='Agreeing Game')
    ps4 = GameFactory(concept=concept, title_platform=['PS4'], title_name='Agreeing Game')
    ps5 = GameFactory(concept=concept, title_platform=['PS5'], title_name='Agreeing Game')
    # Three numbers that must not be confused: 6 distinct players on the page, 4 on the biggest list
    # (what the card used to show), 7 summed across lists (what the page used to show).
    players = [ProfileFactory() for _ in range(6)]
    _own(players[0], ps4); _own(players[1], ps4)
    _own(players[2], ps4, ps5)                     # owns both versions: one player
    for p in players[3:]:
        _own(p, ps5)
    ps4.played_count, ps5.played_count = 3, 4     # the per-list denorms, as recalc_earn_rates writes them
    ps4.save(update_fields=['played_count']); ps5.save(update_fields=['played_count'])
    _recompute()

    card = client.get(reverse('games_list'), {'query': 'Agreeing'}).content.decode()
    page = client.get(reverse('game_page', kwargs={'igdb_id': 778001}))

    assert 'title="6 players own this game"' in card, 'the card shows one list (4), not the page'
    assert page.context['community_stats']['played_count'] == 6, 'the page summed the lists (7)'


def test_a_one_list_card_keeps_its_own_count(client):
    """Trophy Lists browse shows one card per LIST, so its count stays that list's own."""
    concept = _igdb_page(779001, title='Per List Game')
    ps4 = GameFactory(concept=concept, title_platform=['PS4'], title_name='Per List Game', played_count=4)
    GameFactory(concept=concept, title_platform=['PS5'], title_name='Per List Game', played_count=2)
    concept.page_player_count = 5
    concept.save(update_fields=['page_player_count'])

    content = client.get(reverse('trophy_lists'), {'query': 'Per List'}).content.decode()

    assert 'title="4 players own this game"' in content and 'title="2 players own this game"' in content
    assert 'title="5 players own this game"' not in content
    assert ps4.page_players == 5


def test_a_trusted_match_without_an_igdb_id_is_its_own_page():
    """No igdb id means no igdb page: its card links /games/c/<id>/ and the count is its own lists'."""
    concept = ConceptFactory()
    IGDBMatchFactory(concept=concept, igdb_id=None, status='accepted')
    game = GameFactory(concept=concept)
    _own(ProfileFactory(), game)
    _own(ProfileFactory(), game)

    _recompute()

    assert _count(concept) == 2


def test_a_rejected_member_still_counts_on_the_igdb_page_it_appears_on():
    """GamePageView shows every list whose match carries the id, rejected included, so the page counts it."""
    trusted = _igdb_page(780001)
    rejected = _igdb_page(780001, status='rejected', title='Rejected Member')
    _own(ProfileFactory(), GameFactory(concept=trusted))
    _own(ProfileFactory(), GameFactory(concept=rejected))

    _recompute()

    assert _count(trusted) == 2
    assert _count(rejected) == 1


def test_a_list_with_a_null_np_id_is_not_counted_either():
    concept = ConceptFactory()
    _own(ProfileFactory(), GameFactory(concept=concept))
    _own(ProfileFactory(), GameFactory(concept=concept, np_communication_id=None))

    _recompute()

    assert _count(concept) == 1


def _sorted_titles(client, url_name, *titles, **params):
    content = client.get(reverse(url_name), {'sort': 'played', **params}).content.decode()
    return sorted(titles, key=content.index)


def test_most_played_orders_game_cards_by_the_number_they_show(client):
    """A two-version game with 1,500 players (biggest list 1,000) showed 1,500 but sorted below a one-list
    game showing 1,200, because the sort read one list's count."""
    multi = _igdb_page(781001, title='Multi Version Game')
    GameFactory(concept=multi, title_platform=['PS4'], played_count=1000)
    GameFactory(concept=multi, title_platform=['PS5'], played_count=800)
    multi.page_player_count = 1500
    multi.save(update_fields=['page_player_count'])
    single = _igdb_page(781002, title='Single Version Game')
    GameFactory(concept=single, title_platform=['PS5'], played_count=1200)
    single.page_player_count = 1200
    single.save(update_fields=['page_player_count'])

    assert _sorted_titles(client, 'games_list', 'Multi Version Game', 'Single Version Game') == [
        'Multi Version Game', 'Single Version Game']


def test_most_played_on_trophy_lists_still_orders_by_the_list(client):
    """Each Trophy Lists card is ONE list showing its own count, so it sorts by that count."""
    page = _igdb_page(782001, title='List Order Game')
    GameFactory(concept=page, title_platform=['PS4'], title_name='List Order PS4', played_count=1000)
    GameFactory(concept=page, title_platform=['PS5'], title_name='List Order PS5', played_count=800)
    page.page_player_count = 1500
    page.save(update_fields=['page_player_count'])
    other = _igdb_page(782002, title='List Order Other')
    GameFactory(concept=other, title_platform=['PS5'], title_name='List Order Other', played_count=900)

    order = _sorted_titles(client, 'trophy_lists', 'List Order PS4', 'List Order PS5', 'List Order Other',
                           query='List Order')
    assert order == ['List Order PS4', 'List Order Other', 'List Order PS5']


def test_the_nightly_job_widens_its_statement_timeout():
    """Its player counts are full-table COUNT(DISTINCT) aggregates; the 60s default (15s from the web shell)
    would cancel them at prod scale."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    with CaptureQueriesContext(connection) as ctx:
        _recompute()

    assert any(q['sql'].startswith('SET statement_timeout = 600000') for q in ctx.captured_queries)
