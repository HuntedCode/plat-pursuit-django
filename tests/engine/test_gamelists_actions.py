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
    GameFactory(concept=concept, title_platform=['PS5'])
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


@pytest.mark.parametrize('name, extra_args, payload', [
    ('list_like', (), {'liked': 'true'}),
    ('list_follow', (), {'following': 'true'}),
    ('list_add_game', (), {'concept_id': 1}),
    ('list_remove_game', (1,), {}),
])
def test_no_endpoint_confirms_a_private_list_exists(client, name, extra_args, payload):
    """404 from EVERY endpoint -- never 403, never a service error, and never a 500.

    This existed for `list_like` alone, and that omission is exactly why `list_follow` and
    `list_add_game` shipped passing `None` into the service: `get_list` returns None for an
    unreadable list, and neither view checked. Both raised AttributeError inside the service, so a
    private id answered 500 while a readable one answered 400 -- which is the very distinction the
    uniform 404 exists to erase.
    """
    author = ProfileFactory(is_linked=True, psn_username='author')
    private = svc.create_list(author, name='Private')
    _staff(client, psn='stranger')

    resp = client.post(reverse(name, args=(private.id, *extra_args)), payload)

    assert resp.status_code == 404, f'{name} answered {resp.status_code} for a private list'
    assert GameListLike.objects.count() == 0
    assert GameListFollow.objects.count() == 0
    assert GameListItem.objects.count() == 0


def test_no_endpoint_500s_on_a_list_that_never_existed(client):
    """Same guard, from the other side: an id nobody owns must answer the same way a private one
    does, or the difference between them is itself the oracle."""
    _staff(client)

    for name, args, payload in (
        ('list_like', (99999,), {'liked': 'true'}),
        ('list_follow', (99999,), {'following': 'true'}),
        ('list_add_game', (99999,), {'concept_id': 1}),
        ('list_remove_game', (99999, 1), {}),
    ):
        assert client.post(reverse(name, args=args), payload).status_code == 404, name


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
    # `[platform]`, not `platform`. This line passed a loop VARIABLE, so it survived the sweep that
    # fixed the 21 string literals -- and the fix for the crash then INSULATED it rather than
    # correcting it: `platform_priority_rank` iterates its argument, so the string 'PS4' is walked
    # character by character, every char misses, and it silently returns the unknown-platform
    # fallback. Both stacks then tied and the pk tiebreak picked the PS4 row, which is the opposite
    # of the rule. No raise, no failing test, just a quietly wrong cover.
    ps4 = GameFactory(concept=concept, title_platform=['PS4'], title_name='HK stack',
                      title_image='https://img.test/ps4.jpg')
    ps5 = GameFactory(concept=concept, title_platform=['PS5'], title_name='HK stack',
                      title_image='https://img.test/ps5.jpg')

    results = client.get(reverse('list_game_search', args=[game_list.id]),
                         {'q': 'hollow'}).json()['results']

    assert len(results) == 1, 'a two-stack game appeared twice'
    assert results[0]['concept_id'] == concept.pk
    assert results[0]['title'] == 'Hollow Knight'
    # This is the ONLY test that drives the search through `cover_games_for` with a multi-stack
    # concept -- the exact case the platform ordering exists to resolve -- and it previously asserted
    # nothing about WHICH stack won, so it could not tell the right answer from the wrong one.
    # PS5 outranks PS4, and it must win regardless of which row was created first.
    assert results[0]['cover'] == ps5.display_image_url
    assert results[0]['cover'] != ps4.display_image_url


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


@pytest.mark.parametrize('short', ['', 'a', 'ab'])
def test_a_short_query_returns_nothing_rather_than_the_catalogue(client, short):
    """Three characters, not two: pg_trgm extracts no trigrams from a two-character pattern, so a
    2-char query is a guaranteed full pass over the catalogue however the index question lands."""
    owner = _staff(client)
    game_list = svc.create_list(owner, name='Backlog')
    _concept('Abc')

    assert client.get(reverse('list_game_search', args=[game_list.id]),
                      {'q': short}).json()['results'] == []


def test_an_enormous_query_is_refused_rather_than_becoming_a_like_pattern(client):
    """An unbounded `q` is an unbounded LIKE pattern. `SiteSuggestView` refuses these and this
    copied its query shape without its protections."""
    owner = _staff(client)
    game_list = svc.create_list(owner, name='Backlog')

    resp = client.get(reverse('list_game_search', args=[game_list.id]), {'q': 'x' * 5000})

    assert resp.status_code == 400


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
        client.get(reverse('list_game_search', args=[game_list.id]), {'q': 'blobtest'})

    joined = [q['sql'] for q in ctx.captured_queries if 'igdb' in q['sql'].lower()]
    # The guard its sibling in test_gamelists_detail.py has and this dropped: an empty generator
    # passes the loop below, so without this the test is green when the query never runs at all.
    assert joined, 'the cover chain is not being joined at all'
    for sql in joined:
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


# -- what the endpoint audit found had no test ---------------------------------------------------

@pytest.mark.parametrize('bad', ['abc', '99999999999999999999', '-1', ''])
def test_a_malformed_concept_id_is_refused_rather_than_raising(client, bad):
    """`Concept.objects.filter(pk='abc')` raises ValueError and a 20-digit id raises DataError --
    both 500s reachable from a form field. This file already carried `_count_filter`, written after
    a superscript two took the browse page down, and none of that discipline had reached here."""
    owner = _staff(client)
    game_list = svc.create_list(owner, name='Backlog')

    resp = client.post(reverse('list_add_game', args=[game_list.id]), {'concept_id': bad})

    assert resp.status_code == 404, f'{bad!r} produced {resp.status_code}'
    assert GameListItem.objects.count() == 0


def test_an_overflowing_list_id_answers_404_rather_than_erroring(client):
    """Django's `<int:...>` converter is `[0-9]+` with no length bound, so a 20-digit id reaches the
    ORM. An audit expected that to raise `DataError` and 500; it does not -- Django 5.2 returns an
    empty queryset for an out-of-range pk, verified rather than assumed. So there is no guard here
    to test, and this pins the BEHAVIOUR instead: if a future Django stops absorbing it, this fails
    and tells us to add one."""
    _staff(client)

    huge = '99999999999999999999'
    assert client.post(f'/community/lists/{huge}/like/', {'liked': 'true'}).status_code == 404
    assert client.get(f'/community/lists/{huge}/').status_code == 404


def test_the_write_endpoints_enforce_csrf():
    """No test in this file would have noticed `@csrf_exempt` being added: the default client has
    CSRF checks OFF. The house already has the strict-client pattern in test_mod_center."""
    from django.test import Client

    strict = Client(enforce_csrf_checks=True)
    owner = _staff(strict)
    game_list = svc.create_list(owner, name='Mine', is_public=True)

    resp = strict.post(reverse('list_add_game', args=[game_list.id]),
                       {'concept_id': _concept().pk})

    assert resp.status_code == 403, 'the write endpoints accept a POST with no CSRF token'
    assert GameListItem.objects.count() == 0


def test_every_write_endpoint_is_rate_limited():
    """The endpoints these replace all carried limits (120/m add and remove, 60/m like and follow),
    and the replacements carried none. Asserted on the view rather than by bursting: the decorator
    is the contract, and a burst test would couple this file to the cache backend."""
    import inspect

    from gamelists import views

    for name in ('ToggleLikeView', 'ToggleFollowView', 'AddConceptView', 'RemoveItemView',
                 'ListGameSearchView'):
        source = inspect.getsource(getattr(views, name))
        assert 'ratelimit' in source, f'{name} is unthrottled'


def test_the_search_is_cached_so_the_catalogue_scan_is_not_per_keystroke(client):
    """The cache is one of the three protections `SiteSuggestView` has and this had copied none of.
    It is keyed on the QUERY, not the list, because the catalogue half of the answer is the same for
    everybody and is the expensive half."""
    from django.core.cache import cache
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    cache.clear()
    owner = _staff(client)
    game_list = svc.create_list(owner, name='Backlog')
    _concept('Cached Game')

    url = reverse('list_game_search', args=[game_list.id])
    with CaptureQueriesContext(connection) as first:
        client.get(url, {'q': 'cached'})
    with CaptureQueriesContext(connection) as second:
        client.get(url, {'q': 'cached'})

    def catalogue_queries(ctx):
        return len([q for q in ctx.captured_queries if 'trophies_concept' in q['sql']])

    assert catalogue_queries(first) >= 1
    assert catalogue_queries(second) == 0, 'the catalogue is scanned again on an identical query'
    cache.clear()


def test_the_cache_does_not_leak_one_hunters_list_into_anothers_results(client):
    """`already_added` is per-list and must be applied AFTER the cache, or the second hunter to
    search a term sees the first one's list state."""
    from django.core.cache import cache
    from django.test import Client

    cache.clear()
    concept = _concept('Shared Game')

    owner_a = _staff(client, psn='huntera')
    list_a = svc.create_list(owner_a, name='A')
    svc.add_concept(list_a, owner_a, concept)
    results_a = client.get(reverse('list_game_search', args=[list_a.id]),
                           {'q': 'shared'}).json()['results']
    assert results_a[0]['already_added'] is True

    other = Client()
    owner_b = _staff(other, psn='hunterb')
    list_b = svc.create_list(owner_b, name='B')
    results_b = other.get(reverse('list_game_search', args=[list_b.id]),
                          {'q': 'shared'}).json()['results']

    assert results_b[0]['already_added'] is False, "the cache leaked another hunter's list state"
    cache.clear()



# ── rename, publish, reorder ─────────────────────────────────────────────────────────────────────

def test_the_owner_can_rename_and_redescribe(client):
    owner = _staff(client)
    game_list = svc.create_list(owner, name='Old name', description='Old words')

    resp = client.post(reverse('list_update', args=[game_list.id]),
                       {'name': 'New name', 'description': 'New words'})

    assert resp.status_code == 200
    game_list.refresh_from_db()
    assert game_list.name == 'New name'
    assert game_list.description == 'New words'
    # The STORED values come back, not the submitted ones -- the client re-renders from these.
    assert resp.json()['name'] == 'New name'


def test_an_edit_returns_what_was_stored_not_what_was_sent(client):
    """`_check_name` trims and sanitizes, so the two are not always the same string. A client that
    re-rendered its own input would show a name the database does not hold."""
    owner = _staff(client)
    game_list = svc.create_list(owner, name='Fine')

    resp = client.post(reverse('list_update', args=[game_list.id]),
                       {'name': '   Padded out   '})

    game_list.refresh_from_db()
    assert resp.json()['name'] == game_list.name
    assert resp.json()['name'] == 'Padded out'


def test_clearing_the_description_is_a_real_edit(client):
    """Absent means leave alone; present-and-empty means set to empty. `.get()` cannot tell those
    apart, which is why the view tests membership instead."""
    owner = _staff(client)
    game_list = svc.create_list(owner, name='A list', description='Something')

    client.post(reverse('list_update', args=[game_list.id]), {'description': ''})

    game_list.refresh_from_db()
    assert game_list.description == ''


def test_a_field_that_is_not_sent_is_left_alone(client):
    owner = _staff(client)
    game_list = svc.create_list(owner, name='Keep me', description='Keep this too')

    client.post(reverse('list_update', args=[game_list.id]), {'name': 'Renamed'})

    game_list.refresh_from_db()
    assert game_list.name == 'Renamed'
    assert game_list.description == 'Keep this too', 'an untouched field was overwritten'


def test_publishing_is_the_same_endpoint(client):
    owner = _staff(client)
    game_list = svc.create_list(owner, name='Draft', is_public=False)

    resp = client.post(reverse('list_update', args=[game_list.id]), {'is_public': 'true'})

    assert resp.status_code == 200
    assert resp.json()['is_public'] is True
    game_list.refresh_from_db()
    assert game_list.is_public is True


def test_a_visitor_cannot_rename_or_publish_someone_elses_list(client):
    """The list is PUBLIC, so `readable_by` resolves it -- ownership is the service's job, and this
    asserts against the database rather than trusting the status code."""
    author = ProfileFactory(is_linked=True, psn_username='author')
    game_list = svc.create_list(author, name='Theirs', is_public=True)
    _staff(client, psn='intruder')

    renamed = client.post(reverse('list_update', args=[game_list.id]), {'name': 'Mine now'})
    hidden = client.post(reverse('list_update', args=[game_list.id]), {'is_public': 'false'})

    assert renamed.status_code == 400
    assert hidden.status_code == 400
    game_list.refresh_from_db()
    assert game_list.name == 'Theirs'
    assert game_list.is_public is True


def test_an_empty_update_is_refused_rather_than_silently_touching_the_row(client):
    owner = _staff(client)
    game_list = svc.create_list(owner, name='Untouched')

    assert client.post(reverse('list_update', args=[game_list.id]), {}).status_code == 400


def test_reorder_sets_the_order_and_keeps_positions_dense(client):
    """Dense positions are load-bearing well beyond this page: `attach_cover_games` bounds the tile
    mosaic on `position__lt=4`, so a gap silently renders a three-cover mosaic on a four-game list."""
    owner = _staff(client)
    game_list = svc.create_list(owner, name='Order me')
    items = []
    for n in range(4):
        concept = ConceptFactory(unified_title=f'Game {n}')
        items.append(svc.add_concept(game_list, owner, concept))

    reversed_ids = [i.id for i in reversed(items)]
    resp = client.post(reverse('list_reorder', args=[game_list.id]),
                       {'item_ids[]': reversed_ids})

    assert resp.status_code == 200
    stored = list(game_list.items.order_by('position').values_list('id', 'position'))
    assert [i for i, _ in stored] == reversed_ids
    assert [p for _, p in stored] == [0, 1, 2, 3], 'positions are no longer dense'


def test_reorder_refuses_a_partial_order_rather_than_dropping_entries(client):
    """A subset means the client and server disagree about what is on the list. Applying it would
    silently drop whatever was not sent."""
    owner = _staff(client)
    game_list = svc.create_list(owner, name='Order me')
    items = [svc.add_concept(game_list, owner, ConceptFactory(unified_title=f'G{n}'))
             for n in range(3)]
    before = list(game_list.items.order_by('position').values_list('id', flat=True))

    resp = client.post(reverse('list_reorder', args=[game_list.id]),
                       {'item_ids[]': [items[0].id, items[1].id]})

    assert resp.status_code == 400
    assert list(game_list.items.order_by('position').values_list('id', flat=True)) == before


def test_a_visitor_cannot_reorder_someone_elses_list(client):
    author = ProfileFactory(is_linked=True, psn_username='author')
    game_list = svc.create_list(author, name='Theirs', is_public=True)
    items = [svc.add_concept(game_list, author, ConceptFactory(unified_title=f'G{n}'))
             for n in range(3)]
    before = list(game_list.items.order_by('position').values_list('id', flat=True))
    _staff(client, psn='intruder')

    resp = client.post(reverse('list_reorder', args=[game_list.id]),
                       {'item_ids[]': list(reversed(before))})

    assert resp.status_code == 400
    assert list(game_list.items.order_by('position').values_list('id', flat=True)) == before


def test_an_empty_reorder_is_refused(client):
    owner = _staff(client)
    game_list = svc.create_list(owner, name='Order me')
    svc.add_concept(game_list, owner, ConceptFactory(unified_title='Only one'))

    assert client.post(reverse('list_reorder', args=[game_list.id]), {}).status_code == 400
