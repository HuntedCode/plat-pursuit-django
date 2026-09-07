"""The list's write endpoints.

These are thin on purpose: every rule lives in `game_list_service`, and these translate a refusal
into a status code. So what is tested here is the TRANSLATION and the gates -- that the service's
refusals actually reach the wire, that a private list cannot be probed through an id, and that a
refused call writes nothing.
"""
import pytest
from django.urls import reverse

from gamelists.models import GameList, GameListFollow, GameListItem, GameListLike
from gamelists.services import game_list_service as svc
from tests.factories import ConceptFactory, GameFactory, ProfileFactory, UserFactory
from users.models import UserRestriction

pytestmark = pytest.mark.django_db


def _staff(client, psn='hunter'):
    user = UserFactory()
    user.role = 'admin'
    user.save()
    profile = ProfileFactory(user=user, is_linked=True, psn_username=psn)
    client.force_login(user)
    return profile


def _concept(title='A Game'):
    concept = ConceptFactory(unified_title=title)
    GameFactory(concept=concept, title_platform='PS5')
    return concept


# ── like and follow ──────────────────────────────────────────────────────────────────────────────

def test_liking_and_unliking_a_public_list(client):
    author = ProfileFactory(is_linked=True, psn_username='author')
    game_list = svc.create_list(author, name='Likeable', is_public=True)
    _staff(client, psn='reader')

    resp = client.post(reverse('list_like', args=[game_list.id]), {'liked': 'true'})
    assert resp.status_code == 200
    assert resp.json() == {'liked': True, 'like_count': 1}

    resp = client.post(reverse('list_like', args=[game_list.id]), {'liked': 'false'})
    assert resp.json()['like_count'] == 0
    assert GameListLike.objects.count() == 0


def test_following_and_unfollowing(client):
    author = ProfileFactory(is_linked=True, psn_username='author')
    game_list = svc.create_list(author, name='Followable', is_public=True)
    _staff(client, psn='reader')

    assert client.post(reverse('list_follow', args=[game_list.id]),
                       {'following': 'true'}).json()['follower_count'] == 1
    assert client.post(reverse('list_follow', args=[game_list.id]),
                       {'following': 'false'}).json()['follower_count'] == 0
    assert GameListFollow.objects.count() == 0


def test_a_private_list_cannot_be_liked_through_its_id(client):
    """404, not 403 and not a service error -- otherwise the endpoint is an oracle that confirms a
    private list exists, and whose it is, from nothing but a number."""
    author = ProfileFactory(is_linked=True, psn_username='author')
    private = svc.create_list(author, name='Private')
    _staff(client, psn='stranger')

    assert client.post(reverse('list_like', args=[private.id]),
                       {'liked': 'true'}).status_code == 404
    assert GameListLike.objects.count() == 0


def test_you_cannot_like_your_own_list_through_the_endpoint(client):
    owner = _staff(client)
    game_list = svc.create_list(owner, name='Mine', is_public=True)

    resp = client.post(reverse('list_like', args=[game_list.id]), {'liked': 'true'})

    assert resp.status_code == 400
    assert GameListLike.objects.count() == 0


def test_a_restricted_hunter_cannot_like_through_the_endpoint(client):
    """The service gates it; this proves the gate survives the trip through HTTP."""
    author = ProfileFactory(is_linked=True, psn_username='author')
    game_list = svc.create_list(author, name='Ranked', is_public=True)
    reader = _staff(client, psn='reader')
    UserRestriction.objects.create(user=reader.user, profile=reader, scope='all_ugc',
                                   reason='spam', created_by_label='Admin')

    resp = client.post(reverse('list_like', args=[game_list.id]), {'liked': 'true'})

    assert resp.status_code == 400
    assert GameListLike.objects.count() == 0


# ── adding and removing ──────────────────────────────────────────────────────────────────────────

def test_adding_a_game_returns_the_new_count(client):
    owner = _staff(client)
    game_list = svc.create_list(owner, name='Backlog')
    concept = _concept('Added Game')

    resp = client.post(reverse('list_add_game', args=[game_list.id]),
                       {'concept_id': concept.pk})

    assert resp.status_code == 200
    assert resp.json()['game_count'] == 1
    assert resp.json()['title'] == 'Added Game'
    assert GameListItem.objects.filter(game_list=game_list, concept=concept).exists()


def test_only_the_owner_can_add(client):
    author = ProfileFactory(is_linked=True, psn_username='author')
    game_list = svc.create_list(author, name='Theirs', is_public=True)
    _staff(client, psn='stranger')

    resp = client.post(reverse('list_add_game', args=[game_list.id]),
                       {'concept_id': _concept().pk})

    assert resp.status_code == 400
    assert GameListItem.objects.count() == 0


def test_adding_the_same_game_twice_is_refused_and_writes_nothing(client):
    owner = _staff(client)
    game_list = svc.create_list(owner, name='Dupes')
    concept = _concept()
    client.post(reverse('list_add_game', args=[game_list.id]), {'concept_id': concept.pk})

    resp = client.post(reverse('list_add_game', args=[game_list.id]), {'concept_id': concept.pk})

    assert resp.status_code == 400
    assert GameListItem.objects.filter(game_list=game_list).count() == 1
    game_list.refresh_from_db()
    assert game_list.game_count == 1


def test_adding_a_game_that_does_not_exist_is_a_404(client):
    owner = _staff(client)
    game_list = svc.create_list(owner, name='Backlog')

    assert client.post(reverse('list_add_game', args=[game_list.id]),
                       {'concept_id': 999999}).status_code == 404


def test_removing_an_item_closes_the_gap(client):
    """The dense-position contract, through HTTP."""
    owner = _staff(client)
    game_list = svc.create_list(owner, name='Ordered')
    items = [svc.add_concept(game_list, owner, _concept(f'G{n}')) for n in range(3)]

    resp = client.post(reverse('list_remove_game', args=[game_list.id, items[0].id]))

    assert resp.json()['game_count'] == 2
    assert list(GameListItem.objects.filter(game_list=game_list)
                .order_by('position').values_list('position', flat=True)) == [0, 1]


def test_an_item_from_another_list_cannot_be_removed_through_yours(client):
    owner = _staff(client)
    mine = svc.create_list(owner, name='Mine')
    other = ProfileFactory(is_linked=True, psn_username='other')
    theirs = svc.create_list(other, name='Theirs', is_public=True)
    their_item = svc.add_concept(theirs, other, _concept())

    resp = client.post(reverse('list_remove_game', args=[mine.id, their_item.id]))

    assert resp.status_code == 404
    assert GameListItem.objects.filter(pk=their_item.pk).exists()


def test_a_restricted_hunter_cannot_add(client):
    owner = _staff(client)
    game_list = svc.create_list(owner, name='Backlog')
    UserRestriction.objects.create(user=owner.user, profile=owner, scope='all_ugc',
                                   reason='spam', created_by_label='Admin')

    resp = client.post(reverse('list_add_game', args=[game_list.id]),
                       {'concept_id': _concept().pk})

    assert resp.status_code == 400
    assert GameListItem.objects.count() == 0


# ── the typeahead ────────────────────────────────────────────────────────────────────────────────

def test_the_search_returns_concepts_not_trophy_lists(client):
    """The old list search matched `Game.title_name` -- the trophy-list name, which is unreliable
    for matching and returns one row per stack for a game somebody wants to add once."""
    owner = _staff(client)
    game_list = svc.create_list(owner, name='Backlog')
    concept = ConceptFactory(unified_title='Hollow Knight')
    for platform in ('PS4', 'PS5'):
        GameFactory(concept=concept, title_platform=platform, title_name='HK stack')

    results = client.get(reverse('list_game_search', args=[game_list.id]),
                         {'q': 'hollow'}).json()['results']

    assert len(results) == 1, 'a two-stack game appeared twice'
    assert results[0]['concept_id'] == concept.pk
    assert results[0]['title'] == 'Hollow Knight'


def test_the_search_marks_what_is_already_on_the_list(client):
    """Marked rather than filtered out: somebody searching for something already there should be
    told it is there, not left wondering why it does not appear."""
    owner = _staff(client)
    game_list = svc.create_list(owner, name='Backlog')
    concept = _concept('Already Here')
    svc.add_concept(game_list, owner, concept)

    results = client.get(reverse('list_game_search', args=[game_list.id]),
                         {'q': 'already'}).json()['results']

    assert len(results) == 1
    assert results[0]['already_added'] is True


def test_a_short_query_returns_nothing_rather_than_the_catalogue(client):
    owner = _staff(client)
    game_list = svc.create_list(owner, name='Backlog')
    _concept('A')

    assert client.get(reverse('list_game_search', args=[game_list.id]),
                      {'q': 'a'}).json()['results'] == []


def test_the_search_is_bounded(client):
    owner = _staff(client)
    game_list = svc.create_list(owner, name='Backlog')
    for n in range(20):
        _concept(f'Bounded Game {n:02d}')

    results = client.get(reverse('list_game_search', args=[game_list.id]),
                         {'q': 'bounded'}).json()['results']

    assert len(results) == 12


def test_the_search_does_not_fetch_the_igdb_blob(client):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    owner = _staff(client)
    game_list = svc.create_list(owner, name='Backlog')
    _concept('Blob Test')

    with CaptureQueriesContext(connection) as ctx:
        client.get(reverse('list_game_search', args=[game_list.id]), {'q': 'blob'})

    for sql in (q['sql'] for q in ctx.captured_queries if 'igdb' in q['sql'].lower()):
        assert 'raw_response' not in sql.lower()


# ── the gates ────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('name, args', [
    ('list_like', (1,)),
    ('list_follow', (1,)),
    ('list_add_game', (1,)),
    ('list_remove_game', (1, 1)),
])
def test_every_write_endpoint_refuses_an_ordinary_hunter(client, name, args):
    """The development gate, on the endpoints as well as the pages -- a routed write with no gate is
    how a system meant to be invisible starts accepting data.

    Tested with a LOGGED-IN, linked, non-staff hunter rather than an anonymous one. Anonymous proves
    nothing here: `LoginRequiredMixin` redirects them too, so removing `_DevelopmentGate` entirely
    left that version of this test green. Only a real hunter distinguishes "gated" from "merely
    requires an account".
    """
    assert client.post(reverse(name, args=args)).status_code == 302, 'anonymous is not refused'

    hunter = UserFactory()
    ProfileFactory(user=hunter, is_linked=True, psn_username='ordinary')
    client.force_login(hunter)

    resp = client.post(reverse(name, args=args))
    assert resp.status_code == 302, f'{name} is open to any signed-in hunter'
    assert resp.url == '/', 'a non-staff hunter should be sent home, not to login'
    assert GameList.objects.count() == 0


def test_the_write_endpoints_refuse_a_get(client):
    owner = _staff(client)
    game_list = svc.create_list(owner, name='Backlog')

    assert client.get(reverse('list_like', args=[game_list.id])).status_code == 405
    assert client.get(reverse('list_add_game', args=[game_list.id])).status_code == 405
