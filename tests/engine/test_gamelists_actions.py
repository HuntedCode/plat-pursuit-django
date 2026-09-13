"""The list's write endpoints.

These are thin on purpose: every rule lives in `game_list_service`, and these translate a refusal
into a status code. So what is tested here is the TRANSLATION and the gates -- that the service's
refusals actually reach the wire, that a private list cannot be probed through an id, and that a
refused call writes nothing.
"""
import pytest
from django.urls import reverse

from gamelists.models import (FREE_MAX_LISTS, LIST_TYPE_COLLECTION, LIST_TYPE_RANKED, GameList,
                              GameListFollow, GameListItem, GameListLike, GameListSection)
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
    # Added after an audit found the list incomplete AGAIN -- and `list_update` is the PUBLISH
    # endpoint, so it is the one that most needed to be here. The docstring below already explains
    # that this exact omission is how two endpoints shipped 500ing on a private id.
    ('list_update', (), {'name': 'probe'}),
    ('list_reorder', (), {'item_ids[]': [1]}),
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
def test_every_write_endpoint_needs_an_account_and_a_readable_list(client, name, args):
    """INVERTED 2026-09. This pinned the development gate: a signed-in hunter got a 302 like everyone
    who was not staff. With the gate gone, what is left is the permission stack that was always
    underneath it, and both halves still matter.

    ANONYMOUS gets a redirect, from `LoginRequiredMixin`. A SIGNED-IN hunter gets past that and hits
    a uniform JSON 404.

    WHAT THIS DOES AND DOES NOT COVER, because the first version of this docstring claimed its
    sibling's work: list 1 does not exist, so what is exercised here is the MISSING-ROW path. Drop
    the permission filter entirely -- `readable_by()` to `visible()` -- and this still passes. The
    private-list half is `test_no_endpoint_confirms_a_private_list_exists` above, which creates a
    real one; the point of this test is that a nonexistent id is answered IDENTICALLY, so the two
    cases cannot be told apart.
    """
    assert client.post(reverse(name, args=args)).status_code == 302, 'anonymous is not refused'

    hunter = UserFactory()
    ProfileFactory(user=hunter, is_linked=True, psn_username='ordinary')
    client.force_login(hunter)

    resp = client.post(reverse(name, args=args))
    assert resp.status_code == 404, f'{name} does not answer a 404 for an unreadable list'
    # A UNIFORM 404 with no detail. `Content-Type` rules out the site's HTML `handler404`, which
    # would be a different code path answering the same status.
    assert resp['Content-Type'] == 'application/json', f'{name} answered with a page, not JSON'

    # IDENTICAL to the answer for somebody else's PRIVATE list, which is the property that makes the
    # id useless as an oracle. This asserted `b'Should not exist' not in resp.content` -- a string
    # this test never posts (it was copied from the create-endpoint test, which does) and which
    # therefore no change to the views could have produced.
    author = ProfileFactory(is_linked=True, psn_username='someone_else')
    private = svc.create_list(author, name='Theirs')
    private_resp = client.post(reverse(name, args=(private.id, *args[1:])))
    assert private_resp.content == resp.content, (
        f'{name} answers a private list differently from a missing one')
    # The pre-existing "a refused POST wrote nothing" check, allowing for the one list this test now
    # creates on purpose. Asserting the SET rather than a count, so it still fails if a write slips
    # through rather than merely counting to the wrong number.
    assert list(GameList.objects.values_list('id', flat=True)) == [private.id]


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
        # `ratelimit(` -- `inspect.getsource` includes the docstring and every comment, and these
        # classes are heavily commented, so the bare word was satisfiable by prose with the
        # decorator deleted.
        assert 'ratelimit(' in source, f'{name} is unthrottled'


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


def test_a_hunter_at_the_cap_can_delete_their_way_out(client):
    """THE CAP PROMISED A REMEDY THE PRODUCT DID NOT SHIP. `delete_list` was written and tested with
    no view, no URL and no button, and the refusal at the limit reads "Delete one to make room, or
    become a member for more."

    Invisible while only staff could create a list -- staff do not hit a three-list ceiling. Every
    free hunter hits it at list four, permanently. This walks the whole path the message describes.
    """
    owner = _staff(client, psn='capped')
    owner.user_is_premium = False
    owner.save(update_fields=['user_is_premium'])

    made = [svc.create_list(owner, name=f'List {n}') for n in range(FREE_MAX_LISTS)]
    with pytest.raises(svc.ListError):
        svc.create_list(owner, name='One too many')

    resp = client.post(reverse('list_delete', args=[made[0].id]))
    assert resp.status_code == 200
    assert resp.json()['redirect'] == '/my-lists/', 'the client is not told where to go'

    made[0].refresh_from_db()
    assert made[0].is_deleted, 'the list was not deleted'
    # ...and the slot is genuinely free, which is the whole claim the message makes.
    svc.create_list(owner, name='Room at last')


def test_deleting_is_idempotent_and_owner_only_over_http(client):
    """Soft delete, so a double-press is not an error -- somebody double-clicking has not made a
    mistake worth an error message. And a stranger gets the same uniform 404 every other endpoint
    answers, rather than a 403 that would confirm the list exists."""
    author = ProfileFactory(is_linked=True, psn_username='author')
    game_list = svc.create_list(author, name='Theirs', is_public=True)

    # 400, NOT 404, and the distinction is the design rather than an inconsistency: this list is
    # PUBLIC, so a stranger can legitimately see it and `readable_by` returns it -- the refusal comes
    # from the service's ownership check. The uniform 404 is for lists you cannot see at all, where
    # any other answer would confirm the list exists. `test_no_endpoint_confirms_a_private_list_exists`
    # covers that half.
    _staff(client, psn='stranger')
    assert client.post(reverse('list_delete', args=[game_list.id])).status_code == 400
    game_list.refresh_from_db()
    assert not game_list.is_deleted, 'a stranger deleted a list that was not theirs'

    client.logout()
    client.force_login(author.user)
    assert client.post(reverse('list_delete', args=[game_list.id])).status_code == 200

    # A SECOND PRESS IS A 404, not a second 200, and that is right even though `delete_list` is
    # idempotent: the view resolves through `readable_by()` FIRST, and `.visible()` excludes
    # soft-deleted rows -- so a deleted list is indistinguishable from one that never existed, which
    # is the same rule every other endpoint follows. The service's idempotence is therefore
    # unreachable over HTTP, which is fine: the client navigates away on success, and the shell and
    # the admin are where a second call could come from.
    assert client.post(reverse('list_delete', args=[game_list.id])).status_code == 404


def test_switching_type_twice_in_one_session_works(client):
    """THE SECOND SWITCH SILENTLY DID NOTHING, and the endpoint was never the problem.

    The client measures "did the type change" against a `data-list-type` attribute on the identity
    block, and the in-place refresh that replaced the page reload re-renders only the grid, the sort
    control and the position bar -- not that block. So after one successful switch the attribute still
    named the OLD type: switching back computed no change, sent no `list_type`, and with the name and
    description also untouched the request was skipped altogether and the editor just closed.

    The server half is what this pins: every response carries the STORED type, so the client has
    something authoritative to re-anchor to. Asserted across two consecutive writes, because one
    write could not have caught it.
    """
    owner = _staff(client)
    game_list = svc.create_list(owner, name='Switcher')
    assert game_list.list_type == LIST_TYPE_COLLECTION

    first = client.post(reverse('list_update', args=[game_list.id]),
                        {'list_type': LIST_TYPE_RANKED})
    assert first.status_code == 200
    assert first.json()['list_type'] == LIST_TYPE_RANKED, \
        'the client re-anchors from this; without it the next switch is a no-op'

    second = client.post(reverse('list_update', args=[game_list.id]),
                         {'list_type': LIST_TYPE_COLLECTION})
    assert second.status_code == 200
    assert second.json()['list_type'] == LIST_TYPE_COLLECTION

    game_list.refresh_from_db()
    assert game_list.list_type == LIST_TYPE_COLLECTION, 'the switch back did not land'


@pytest.mark.parametrize('endpoint', ['list_update', 'list_create'])
def test_a_bogus_type_over_http_is_a_refusal_not_a_500(client, endpoint):
    """The service refuses it, which is tested -- but nothing checked what that refusal looks like on
    the wire. `CreateListView` passes `request.POST.get('list_type')` straight in, so a hand-posted
    value reaches the service unvalidated and the only question is whether the view translates the
    `ListError` or lets it become a 500."""
    owner = _staff(client)

    if endpoint == 'list_create':
        resp = client.post(reverse('list_create'), {'name': 'Bogus', 'list_type': 'tier'})
        # A form post, so a refusal is a redirect carrying a message rather than a JSON 400.
        assert resp.status_code == 302
        assert not GameList.objects.filter(name='Bogus').exists()
        return

    game_list = svc.create_list(owner, name='Real')
    resp = client.post(reverse('list_update', args=[game_list.id]), {'list_type': 'tier'})

    assert resp.status_code == 400, 'an unknown type must be refused, not raised'
    game_list.refresh_from_db()
    assert game_list.list_type == LIST_TYPE_COLLECTION


def test_the_database_refuses_a_type_the_service_never_saw(client):
    """`choices` is not a constraint, and the service is not the only writer that will ever exist --
    the planned importer and any data migration write around it, which is exactly why the blank-name
    rule is a `CheckConstraint` rather than a service check alone.

    A stored `list_type='tier'` renders as a Collection AND leaves the detail page's radio group with
    nothing checked, so the owner has no UI path back.
    """
    from django.db import IntegrityError, transaction

    owner = _staff(client)
    game_list = svc.create_list(owner, name='Guarded')

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            GameList.objects.filter(pk=game_list.pk).update(list_type='tier')


def test_a_type_switch_alone_is_a_complete_request(client):
    """The client sends ONLY what changed, so a type switch arrives with no name and no description.
    That has to be a valid edit rather than "Nothing to change"."""
    owner = _staff(client)
    game_list = svc.create_list(owner, name='Keep me', description='Keep these words')

    resp = client.post(reverse('list_update', args=[game_list.id]),
                       {'list_type': LIST_TYPE_RANKED})

    assert resp.status_code == 200
    game_list.refresh_from_db()
    assert game_list.list_type == LIST_TYPE_RANKED
    assert game_list.name == 'Keep me', 'an omitted field must be left alone, not cleared'
    assert game_list.description == 'Keep these words'


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


# ── the restriction gate on publishing ───────────────────────────────────────────────────────────

def test_a_restricted_hunter_cannot_publish(client):
    """The moderation bypass this shipped with, pinned in both directions.

    The gate only fired when `name` or `description` was passed, and did not care which way
    `is_public` moved -- so a POST carrying nothing but `is_public=true` reached the write with no
    restriction check at all. The bypass was: write lists privately, get restricted for something
    else, publish the lot. A moderator's only remaining lever was deletion.

    Confirmed end to end before fixing: rename answered 400 while publish answered 200 and flipped
    the row.
    """
    owner = _staff(client, psn='restricted')
    game_list = svc.create_list(owner, name='Written before the ban', is_public=False)
    UserRestriction.objects.create(user=owner.user, profile=owner, scope='all_ugc',
                                   reason='spam', created_by_label='Admin')

    resp = client.post(reverse('list_update', args=[game_list.id]), {'is_public': 'true'})

    assert resp.status_code == 400
    game_list.refresh_from_db()
    assert game_list.is_public is False, 'a restricted hunter published their list'


def test_a_restricted_hunter_can_still_take_their_own_list_down(client):
    """The other half, and the reason the gate is not simply on the whole function. Un-publishing is
    a hunter removing their OWN content, which restriction exists to encourage. Gating everything
    trapped a restricted hunter's list in public."""
    owner = _staff(client, psn='restricted')
    game_list = svc.create_list(owner, name='Already out there', is_public=True)
    UserRestriction.objects.create(user=owner.user, profile=owner, scope='all_ugc',
                                   reason='spam', created_by_label='Admin')

    resp = client.post(reverse('list_update', args=[game_list.id]), {'is_public': 'false'})

    assert resp.status_code == 200
    game_list.refresh_from_db()
    assert game_list.is_public is False
    # And deleting stays available too.
    assert svc.delete_list(game_list, owner).is_deleted is True


@pytest.mark.parametrize('sent, expected', [
    ('true', True), ('True', True), ('1', True), ('on', True), ('yes', True),
    ('false', False), ('0', False), ('', False), ('garbage', False),
])
def test_visibility_parsing_accepts_what_clients_actually_send(client, sent, expected):
    """`== 'true'` read 'True', '1', 'on' and 'yes' as FALSE, so anything but the exact lowercase
    literal silently took a published list down and answered 200. Verified: posting `is_public=on`,
    which is what a plain HTML checkbox sends, un-published a live list.

    Fail-closed is the safe direction for a privacy control, but safe is not the same as correct --
    destroying somebody's publication without telling them is its own harm.
    """
    owner = _staff(client)
    game_list = svc.create_list(owner, name='Toggle me', is_public=False)

    resp = client.post(reverse('list_update', args=[game_list.id]), {'is_public': sent})

    assert resp.status_code == 200
    game_list.refresh_from_db()
    assert game_list.is_public is expected, f'is_public={sent!r} stored {game_list.is_public}'


def test_creating_a_list_is_rate_limited_like_every_other_write(client):
    """The cap bounds the steady state, not the RATE. `delete_list` is a soft delete that frees a
    slot immediately, so create-delete-create was an unthrottled INSERT loop that also ran the
    fixpoint sanitiser and the banned-word scan on every pass."""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / 'gamelists' / 'views.py').read_text(
        encoding='utf-8')
    create_block = source[source.index('class CreateListView'):
                          source.index('class GameListDetailView')]
    assert 'ratelimit(' in create_block, 'CreateListView is the one write with no rate limit'

    # Every other write carries one too -- asserted together so a new endpoint added without a limit
    # fails here rather than being noticed by an audit two rounds later.
    for view in ('UpdateListView', 'ReorderItemsView', 'ToggleLikeView', 'ToggleFollowView',
                 'AddConceptView', 'RemoveItemView', 'ListGameSearchView'):
        start = source.index(f'class {view}')
        nxt = source.find('\nclass ', start + 1)
        block = source[start:nxt if nxt != -1 else len(source)]
        assert 'ratelimit(' in block, f'{view} has no rate limit'


# ── sections, over the wire ──────────────────────────────────────────────────────────────────────

def _member(client, psn='member'):
    profile = _staff(client, psn=psn)
    profile.user_is_premium = True
    profile.save(update_fields=['user_is_premium'])
    return profile


def test_the_section_endpoints_enforce_the_membership_gate(client):
    """The gate lives in the service; these check the refusal reaches the wire as a 400 rather than a
    500 or a silent success -- and that CREATE and RENAME are the two it covers."""
    owner = _staff(client, psn='free')
    owner.user_is_premium = False
    owner.save(update_fields=['user_is_premium'])
    game_list = svc.create_list(owner, name='Mine')

    resp = client.post(reverse('list_section_create', args=[game_list.id]), {'name': 'Playing'})
    assert resp.status_code == 400
    assert 'member' in resp.json()['error'].lower()
    assert GameListSection.objects.count() == 0


def test_a_member_can_create_rename_reorder_and_delete_over_the_wire(client):
    owner = _member(client)
    game_list = svc.create_list(owner, name='Backlog')

    first = client.post(reverse('list_section_create', args=[game_list.id]), {'name': 'Playing'})
    second = client.post(reverse('list_section_create', args=[game_list.id]), {'name': 'Finished'})
    assert first.status_code == 200 and second.status_code == 200
    a, b = first.json()['id'], second.json()['id']

    renamed = client.post(reverse('list_section_rename', args=[game_list.id, a]),
                          {'name': '  Now playing  '})
    # The STORED name: `_check_section_name` trims, so echoing the submitted value would show one the
    # database does not have.
    assert renamed.json()['name'] == 'Now playing'

    assert client.post(reverse('list_sections_reorder', args=[game_list.id]),
                       {'section_ids[]': [b, a]}).status_code == 200
    assert list(game_list.sections.values_list('id', flat=True)) == [b, a]

    assert client.post(reverse('list_section_delete', args=[game_list.id, a])).status_code == 200
    assert GameListSection.objects.filter(game_list=game_list).count() == 1


def test_a_section_on_another_list_is_a_404_not_a_403(client):
    """The section lookup is scoped to the LIST, so "exists on somebody else's list" and "does not
    exist" answer identically. Looking it up by id alone would make the difference an oracle on the
    id space most easily walked -- sections are few per list."""
    author = _member(client, psn='author')
    theirs = svc.create_list(author, name='Theirs')
    their_section = svc.create_section(theirs, author, name='Elsewhere')

    client.logout()
    stranger = _member(client, psn='stranger')
    mine = svc.create_list(stranger, name='Mine')

    for url in (reverse('list_section_rename', args=[mine.id, their_section.id]),
                reverse('list_section_delete', args=[mine.id, their_section.id])):
        assert client.post(url, {'name': 'Hijacked'}).status_code == 404, url

    their_section.refresh_from_db()
    assert their_section.name == 'Elsewhere'


def test_a_cross_section_drop_is_one_atomic_write(client):
    """THE REASON THIS RIDES `list_reorder`. Dragging a card between sections changes where it sits
    AND which section it belongs to. Sent as two requests, either can fail alone -- and the
    interesting failure is the quiet one: a card in the right place under the wrong header, which
    looks correct until the page is reloaded."""
    owner = _member(client)
    game_list = svc.create_list(owner, name='Backlog', list_type=LIST_TYPE_RANKED)
    items = [svc.add_concept(game_list, owner, _concept(f'G{n}')) for n in range(3)]
    playing = svc.create_section(game_list, owner, name='Playing')
    finished = svc.create_section(game_list, owner, name='Finished')
    for item in items:
        svc.assign_item(game_list, owner, item, playing)

    # Drag the last card to the front AND into the other section, in one drop.
    resp = client.post(reverse('list_reorder', args=[game_list.id]), {
        'item_ids[]': [items[2].id, items[0].id, items[1].id],
        'moved_item': items[2].id,
        'section': finished.id,
    })

    assert resp.status_code == 200
    items[2].refresh_from_db()
    assert items[2].position == 0, 'the order did not apply'
    assert items[2].section_id == finished.id, 'the assignment did not apply'


def test_dropping_a_card_out_of_every_section_is_a_real_destination(client):
    """An EMPTY `section` means the loose bucket, not a missing value -- so the two are told apart by
    whether `moved_item` was sent, rather than by `section` being falsy. Getting that backwards makes
    un-filing a card impossible by drag."""
    owner = _member(client)
    game_list = svc.create_list(owner, name='Backlog')
    item = svc.add_concept(game_list, owner, _concept('Only'))
    section = svc.create_section(game_list, owner, name='Playing')
    svc.assign_item(game_list, owner, item, section)

    resp = client.post(reverse('list_reorder', args=[game_list.id]), {
        'item_ids[]': [item.id],
        'moved_item': item.id,
        'section': '',
    })

    assert resp.status_code == 200
    item.refresh_from_db()
    assert item.section_id is None, 'the card could not be dragged out of its section'


def test_a_drop_into_another_lists_section_is_refused_and_writes_nothing(client):
    """Without this a card is filed under a section id from a list the caller may not be able to see:
    it renders nowhere, and the refusal (or its absence) confirms that section exists."""
    author = _member(client, psn='author')
    theirs = svc.create_list(author, name='Theirs')
    their_section = svc.create_section(theirs, author, name='Elsewhere')

    client.logout()
    owner = _member(client, psn='owner')
    mine = svc.create_list(owner, name='Mine', list_type=LIST_TYPE_RANKED)
    items = [svc.add_concept(mine, owner, _concept(f'M{n}')) for n in range(2)]

    resp = client.post(reverse('list_reorder', args=[mine.id]), {
        'item_ids[]': [items[1].id, items[0].id],
        'moved_item': items[1].id,
        'section': their_section.id,
    })

    assert resp.status_code == 400
    items[1].refresh_from_db()
    assert items[1].section_id is None
    # THE ORDER IS UNTOUCHED TOO, which is the point of doing the assignment first: a refusal must
    # not leave the list half-reordered.
    assert items[1].position == 1, 'a refused move still reordered the list'


def test_the_numbering_toggle_rides_the_editor_save(client):
    """It is a property of the list exactly as `list_type` is, so it saves with the rest rather than
    having an endpoint of its own -- two writers for one field is how they drift."""
    owner = _member(client)
    game_list = svc.create_list(owner, name='Backlog', list_type=LIST_TYPE_RANKED)

    resp = client.post(reverse('list_update', args=[game_list.id]), {'restart_numbering': 'on'})

    assert resp.status_code == 200
    assert resp.json()['restart_numbering'] is True
    game_list.refresh_from_db()
    assert game_list.sections_restart_numbering is True

    # `safe_bool`, not `== 'true'`: 'on' is what a plain HTML checkbox sends, and the bare comparison
    # reads it as False -- the exact bug `is_public` shipped and this inherits the fix for.
    client.post(reverse('list_update', args=[game_list.id]), {'restart_numbering': 'false'})
    game_list.refresh_from_db()
    assert game_list.sections_restart_numbering is False


def test_a_drag_that_only_files_does_not_rewrite_the_authors_order(client):
    """THE REASON `list_item_assign` EXISTS BESIDE `list_reorder`.

    Under any sort but the real sequence the position beneath the cursor is an artefact of the sort,
    so posting it would overwrite the author's ranking with the shape of a view. This endpoint reports
    the filing and nothing else."""
    owner = _member(client)
    game_list = svc.create_list(owner, name='Shelf')
    items = [svc.add_concept(game_list, owner, _concept(f'S{n}')) for n in range(3)]
    section = svc.create_section(game_list, owner, name='Playing')

    before = list(GameListItem.objects.filter(game_list=game_list)
                  .order_by('position').values_list('id', flat=True))

    resp = client.post(reverse('list_item_assign', args=[game_list.id, items[2].id]),
                       {'section': section.id})

    assert resp.status_code == 200
    items[2].refresh_from_db()
    assert items[2].section_id == section.id
    after = list(GameListItem.objects.filter(game_list=game_list)
                 .order_by('position').values_list('id', flat=True))
    assert after == before, 'filing a game silently reordered the list'


def test_assigning_to_an_empty_section_un_files_a_game(client):
    owner = _member(client)
    game_list = svc.create_list(owner, name='Shelf')
    item = svc.add_concept(game_list, owner, _concept('Only'))
    section = svc.create_section(game_list, owner, name='Playing')
    svc.assign_item(game_list, owner, item, section)

    resp = client.post(reverse('list_item_assign', args=[game_list.id, item.id]), {'section': ''})

    assert resp.status_code == 200
    item.refresh_from_db()
    assert item.section_id is None


def test_assigning_to_a_junk_section_is_refused_rather_than_a_500(client):
    """`filter(pk='abc')` raises ValueError, so without `safe_int` a junk value is a server error on
    a route any logged-in hunter can post to."""
    owner = _member(client)
    game_list = svc.create_list(owner, name='Shelf')
    item = svc.add_concept(game_list, owner, _concept('Only'))

    for junk in ('abc', '99999999'):
        resp = client.post(reverse('list_item_assign', args=[game_list.id, item.id]),
                           {'section': junk})
        assert resp.status_code == 400, junk
    item.refresh_from_db()
    assert item.section_id is None


def test_a_non_owner_gets_the_same_answer_for_every_item_id(client):
    """THE ORACLE, and the first version of this test did not build one.

    `readable_by` lets anybody reach this endpoint for a PUBLIC list. Resolving the item first meant
    an id that belongs to that list answered 400 "That is not your list" while an id that belongs
    elsewhere answered 404 "no such entry" -- so the pair reports, for any id, whether it sits on the
    list being probed. Item ids are not in the public DOM (`data-item-id` renders only under
    `can_arrange`), so that is new information, and it maps the global id space onto public lists.

    Comparing a foreign item against a missing one is what makes the two implementations disagree:
    both 404 when the item is simply absent, so the earlier test passed either way.
    """
    author = _member(client, psn='author')
    public = svc.create_list(author, name='Theirs', is_public=True)
    on_their_list = svc.add_concept(public, author, _concept('Theirs'))

    client.logout()
    _member(client, psn='stranger')

    on_it = client.post(reverse('list_item_assign', args=[public.id, on_their_list.id]),
                        {'section': ''})
    not_on_it = client.post(reverse('list_item_assign', args=[public.id, 99999999]),
                            {'section': ''})

    assert on_it.status_code == not_on_it.status_code, \
        'the status tells a stranger whether that item id is on this list'
    assert on_it.json()['error'] == not_on_it.json()['error'], \
        'the message tells a stranger whether that item id is on this list'

    on_their_list.refresh_from_db()
    assert on_their_list.section_id is None, 'a refused call wrote something'


def test_assigning_someone_elses_item_is_refused(client):
    """The private case, which the outer `readable_by` 404 already covers -- kept because it is the
    shape somebody actually attacks, and because it pins that the LIST-level refusal comes first."""
    author = _member(client, psn='author')
    theirs = svc.create_list(author, name='Theirs')
    their_item = svc.add_concept(theirs, author, _concept('Theirs'))

    client.logout()
    owner = _member(client, psn='owner')
    mine = svc.create_list(owner, name='Mine')

    # Their PRIVATE list: a uniform 404 that never confirms it exists.
    assert client.post(reverse('list_item_assign', args=[theirs.id, their_item.id]),
                       {'section': ''}).status_code == 404
    # And their item against MY list, which does not hold it.
    assert client.post(reverse('list_item_assign', args=[mine.id, their_item.id]),
                       {'section': ''}).status_code == 404

    their_item.refresh_from_db()
    assert their_item.section_id is None


def test_removing_an_item_gives_a_non_owner_one_answer_too(client):
    """The same id oracle `AssignItemView` was fixed for, one route away and left behind.

    `readable_by` lets any signed-in hunter POST this for somebody else's PUBLIC list. Resolving the
    item before the ownership check answered 400 "That is not your list" for an id that sits on that
    list and 404 for one that does not -- reporting, for any id, whether it belongs to the list being
    probed. `data-item-id` renders only for owners, so those ids are otherwise undisclosed."""
    author = _member(client, psn='author')
    public = svc.create_list(author, name='Theirs', is_public=True)
    on_their_list = svc.add_concept(public, author, _concept('Theirs'))

    client.logout()
    _member(client, psn='stranger')

    on_it = client.post(reverse('list_remove_game', args=[public.id, on_their_list.id]))
    not_on_it = client.post(reverse('list_remove_game', args=[public.id, 99999999]))

    assert on_it.status_code == not_on_it.status_code, \
        'the status tells a stranger whether that item id is on this list'
    assert on_it.json()['error'] == not_on_it.json()['error']

    assert GameListItem.objects.filter(pk=on_their_list.pk).exists(), 'a refused call deleted a row'
