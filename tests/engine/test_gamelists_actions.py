"""The list's write endpoints.

These are thin on purpose: every rule lives in `game_list_service`, and these translate a refusal
into a status code. So what is tested here is the TRANSLATION and the gates -- that the service's
refusals actually reach the wire, that a private list cannot be probed through an id, and that a
refused call writes nothing.
"""
import re

import pytest
from django.urls import reverse

from django.db import connection
from django.test.utils import CaptureQueriesContext

from gamelists.models import (FREE_MAX_LISTS, NAME_MAX_LENGTH, LIST_TYPE_COLLECTION, LIST_TYPE_RANKED, GameList,
                              GameListFollow, GameListItem, GameListLike, GameListSection)
from pathlib import Path

from gamelists.services import game_list_service as svc
from tests.factories import ConceptFactory, GameFactory, ProfileFactory, UserFactory
from users.models import UserRestriction

pytestmark = pytest.mark.django_db


def _decommented(source):
    """Strip comments before asserting on code.

    A comment is a claim; only code is evidence -- and this project has shipped several assertions
    that passed off prose naming the very thing that had been removed."""
    source = re.sub(r'/\*.*?\*/', '', source, flags=re.S)
    return re.sub(r'^\s*//.*$', '', source, flags=re.M)


def _decommented_css(source):
    """The same, for CSS, which has only the block form.

    Separate from `_decommented` so a caller cannot accidentally strip `//` from a stylesheet, where
    it is not a comment at all -- a URL contains one."""
    return re.sub(r'/\*.*?\*/', '', source, flags=re.S)


def _read(relative):
    return (Path(__file__).resolve().parents[2] / relative).read_text(encoding='utf-8')


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


# ── quick-add: the entry points on the shared card and the game pages ────────────────────────────

def test_the_picker_answers_where_this_game_can_go(client):
    """One request, every list the hunter owns, with the three facts a row needs: whether the game is
    already on it, which ITEM that is (so removing reuses the per-item route), and whether it is
    full."""
    owner = _member(client)
    concept = _concept('Findable')

    holds_it = svc.create_list(owner, name='Has it')
    item = svc.add_concept(holds_it, owner, concept)
    empty = svc.create_list(owner, name='Room here')

    resp = client.get(reverse('lists_for_concept', args=[concept.id]))

    assert resp.status_code == 200
    rows = {row['name']: row for row in resp.json()['lists']}
    assert rows['Has it']['has_concept'] is True
    # THE ROUTE, and no bare `item_id` beside it. That field was returned under a paragraph arguing
    # it was what let removal reuse the per-item route -- and nothing read it, because `remove_url`
    # had been added for exactly that job. A response field with no reader is a second way to say one
    # thing, and the one nobody maintains.
    assert rows['Has it']['remove_url'] == reverse('list_remove_game', args=[holds_it.id, item.id])
    assert 'item_id' not in rows['Has it'], 'the redundant id came back'
    assert rows['Room here']['has_concept'] is False
    assert rows['Room here']['remove_url'] is None
    assert rows['Room here']['add_url'] == reverse('list_add_game', args=[empty.id])


def test_the_picker_never_shows_another_hunters_lists(client):
    """`owned_by`, not `readable_by`. A picker offering somebody else's PUBLIC list would be an add
    that the service then refuses -- and it would put their private library in front of a stranger."""
    author = _member(client, psn='author')
    theirs = svc.create_list(author, name='Theirs', is_public=True)
    concept = _concept('Shared')
    svc.add_concept(theirs, author, concept)

    client.logout()
    _member(client, psn='stranger')

    names = [row['name'] for row in
             client.get(reverse('lists_for_concept', args=[concept.id])).json()['lists']]
    assert names == [], "another hunter's list was offered as a destination"


def test_the_picker_reports_both_caps(client, monkeypatch):
    """A full list renders DISABLED rather than absent -- "no room" and "not a list" are different
    answers, and hiding the first is a lie. And `can_create` is the other cap: offering "New list" to
    somebody already holding three is the remedy-that-refuses defect all over again."""
    from gamelists.services import game_list_service as service
    from gamelists.models import FREE_MAX_LISTS

    owner = _staff(client, psn='free')          # free tier: three lists
    concept = _concept('Wanted')

    monkeypatch.setattr(service, 'MAX_ITEMS_PER_LIST', 1)
    full = svc.create_list(owner, name='Full')
    svc.add_concept(full, owner, _concept('Filler'))

    body = client.get(reverse('lists_for_concept', args=[concept.id])).json()
    assert body['lists'][0]['is_full'] is True
    assert body['can_create'] is True, 'one list of three is not the cap'

    for n in range(FREE_MAX_LISTS - 1):
        svc.create_list(owner, name=f'Spare {n}')
    body = client.get(reverse('lists_for_concept', args=[concept.id])).json()
    assert body['can_create'] is False, '"New list" was offered to somebody already at the cap'


def test_the_picker_costs_one_membership_query_however_many_lists(client):
    """The N+1 a picker invites is a membership check per row. A hunter holds up to 25 lists, so this
    is the same bounded-`IN` shape the adder's typeahead uses."""
    owner = _member(client)
    concept = _concept('Wanted')
    for n in range(8):
        game_list = svc.create_list(owner, name=f'List {n}')
        if n % 2 == 0:
            svc.add_concept(game_list, owner, concept)

    url = reverse('lists_for_concept', args=[concept.id])
    client.get(url)                                   # warm
    with CaptureQueriesContext(connection) as ctx:
        resp = client.get(url)

    assert len(resp.json()['lists']) == 8
    member_reads = [q for q in ctx.captured_queries if 'gamelists_gamelistitem' in q['sql']]
    assert len(member_reads) == 1, f'one read per list: {len(member_reads)}'


def test_new_list_with_a_game_is_one_act(client):
    """Two requests would leave an empty list named after a game it does not contain when the second
    fails -- and the hunter, having spent one of three slots, is worse off than before they pressed
    anything."""
    owner = _member(client)
    concept = _concept('Seed')

    resp = client.post(reverse('list_create_with_concept', args=[concept.id]),
                       {'name': 'Fresh start'})

    assert resp.status_code == 200
    body = resp.json()
    # LOOKED UP, not read from the response. The endpoint returns only the name: an `id` and a `url`
    # were there and read by nothing, which is a response promising a navigation the design
    # deliberately does not make.
    assert body == {'name': 'Fresh start'}
    created = GameList.objects.get(owner=owner, name='Fresh start')
    assert list(created.items.values_list('concept_id', flat=True)) == [concept.id]
    # PRIVATE, like every list. Publishing stays a separate deliberate act, and one made in passing
    # from a browse grid is the last to make public by default.
    assert created.is_public is False


def test_a_refused_create_leaves_nothing_behind(client):
    """The list-count cap, which `create_list` refuses before it builds anything."""
    owner = _staff(client, psn='free')
    concept = _concept('Seed')
    for n in range(FREE_MAX_LISTS):
        svc.create_list(owner, name=f'Existing {n}')

    before = GameList.objects.filter(owner=owner).count()
    resp = client.post(reverse('list_create_with_concept', args=[concept.id]), {'name': 'One more'})

    assert resp.status_code == 400
    assert 'error' in resp.json()
    assert GameList.objects.filter(owner=owner).count() == before, 'a refused create left a list'


def test_a_failure_AFTER_the_list_exists_rolls_it_back(client, monkeypatch):
    """THE CASE THE TEST ABOVE CANNOT REACH, and the one the transaction is actually for.

    A cap refusal happens inside `create_list`, so nothing is built and nothing needs rolling back --
    which meant the atomicity was never exercised: removing `transaction.atomic()` left that test
    green. Mutation testing caught it. The failure that matters is the SECOND call failing once the
    first has already written a row: without the rollback the hunter is left an empty list named
    after a game it does not contain, having spent one of three slots on it.

    Forced with a monkeypatch because there is no natural input that passes `create_list` and then
    fails `add_concept` -- which is exactly why this needs stating rather than hoping."""
    from gamelists.services import game_list_service as service

    owner = _member(client)
    concept = _concept('Seed')
    before = GameList.objects.filter(owner=owner).count()

    def refuse(*args, **kwargs):
        raise service.ListError('nope')

    monkeypatch.setattr(service, 'add_concept', refuse)

    resp = client.post(reverse('list_create_with_concept', args=[concept.id]), {'name': 'Doomed'})

    assert resp.status_code == 400
    assert GameList.objects.filter(owner=owner).count() == before, \
        'the list survived a failure in the call that was meant to fill it'
    assert not GameList.objects.filter(owner=owner, name='Doomed').exists()


def test_adding_a_game_returns_the_route_to_undo_it(client):
    """The popover flips a row from "add" to "remove" the moment this returns, and it used to derive
    that path by rewriting the add URL -- hand-assembling a route, which is what breaks silently the
    day one moves. The server owns URL shapes, so it says one.

    ASSERTED ON `list_add_game`, not on the picker. The picker returns a `remove_url` too, and an
    assertion there passes with this one deleted -- which is how this survived its first mutation."""
    owner = _member(client)
    game_list = svc.create_list(owner, name='Mine')
    concept = _concept('Addable')

    resp = client.post(reverse('list_add_game', args=[game_list.id]), {'concept_id': concept.id})

    assert resp.status_code == 200
    body = resp.json()
    assert body['remove_url'] == reverse('list_remove_game', args=[game_list.id, body['item_id']])
    # ...and it actually works, rather than merely looking right.
    assert client.post(body['remove_url']).status_code == 200
    assert game_list.items.count() == 0


def test_the_quick_add_endpoints_refuse_an_unlinked_hunter(client):
    """`_LinkedProfileRequired`, like every other write here. Both of these are reachable from a
    browse grid, which is the surface an unlinked account sees most of."""
    user = UserFactory()
    ProfileFactory(user=user, is_linked=False, psn_username='unlinked')
    client.force_login(user)
    concept = _concept('Wanted')

    for url in (reverse('lists_for_concept', args=[concept.id]),
                reverse('list_create_with_concept', args=[concept.id])):
        resp = client.post(url, {'name': 'x'}) if 'new-with-game' in url else client.get(url)
        assert resp.status_code in (302, 400, 403), f'{url} answered {resp.status_code}'


def test_the_browse_grid_renders_the_trigger_for_a_linked_hunter(client):
    """The template test above reads the partial; this proves the partial is REACHED, with a real
    request, on the surface that matters most. Browse Games is the site's main catalogue and the
    page most of this feature's traffic will come from."""
    concept = _concept('Findable Quest')
    _member(client, psn='hunter')

    body = client.get(reverse('games_list')).content.decode()

    assert 'data-quick-add' in body, 'the browse grid rendered no way onto a list'
    assert f'data-concept-id="{concept.id}"' in body
    assert 'pp-gcard-wrap' in body


def test_a_visitor_sees_no_trigger_and_no_wrapper(client):
    """Signed-out traffic is the bulk of a browse grid's audience -- it is what the SEO work brings
    in -- and every other action on the site is hidden from it. The WRAPPER goes too: five other
    grids should keep exactly the DOM they had."""
    _concept('Findable Quest')

    body = client.get(reverse('games_list')).content.decode()

    assert 'data-quick-add' not in body
    # `class="pp-gcard-wrap"`, not the bare name: the page's inline scroller config now mentions the
    # wrapper in a JS comment, which SHIPS -- so the loose form matched prose and failed over correct
    # markup. The same shipped-comment trap this project has hit several times.
    assert 'class="pp-gcard-wrap"' not in body, 'a visitor pays for a wrapper that wraps nothing'


def test_an_unlinked_hunter_sees_no_trigger(client):
    """The endpoints behind it carry `_LinkedProfileRequired`, so the page and the endpoint have to
    agree about who can act -- the rule this feature states every time it renders a control."""
    _concept('Findable Quest')
    user = UserFactory()
    ProfileFactory(user=user, is_linked=False, psn_username='unlinked')
    client.force_login(user)

    assert 'data-quick-add' not in client.get(reverse('games_list')).content.decode()


def test_the_grid_costs_no_extra_queries_for_the_trigger(client):
    """THE WHALE RULE, on the site's largest catalogue. The trigger deliberately carries no
    membership state -- whether this game is already on one of your lists -- because computing that
    per concept on a 24-card grid is a per-user query on a page this size. The popover reads
    membership for ONE game, when it opens."""
    for n in range(6):
        _concept(f'Bulk {n:03d}')
    owner = _member(client, psn='hunter')
    svc.add_concept(svc.create_list(owner, name='Mine'), owner, _concept('On a list'))

    url = reverse('games_list')
    client.get(url)                                   # warm
    with CaptureQueriesContext(connection) as ctx:
        client.get(url)

    # No read of the list tables at all: the grid does not know, and does not ask.
    list_reads = [q for q in ctx.captured_queries
                  if 'gamelists_gamelist' in q['sql']]
    assert list_reads == [], f'the browse grid now queries the list tables: {len(list_reads)}'


def test_the_concept_page_offers_the_add_action():
    """The concept page is the one a list ACTUALLY holds -- `GameListItem.concept` -- so it is the
    most honest place on the site to offer this.

    ASSERTED AGAINST THE TEMPLATE, NOT A RENDER, and that is a real weakness rather than a choice.
    `/games/c/<concept_id>/` 302s to its own URL under the test client -- a loop that predates this
    branch and has nothing to do with lists -- so a request-level test either reads a redirect body
    or spins. The sibling page IS render-tested (`test_the_trophy_list_page_offers_the_add_action`
    below) and its markup is the same shape, which is the only reason this is acceptable: if the
    button here silently stopped rendering for a reason the source cannot show, this would not
    notice. Worth fixing when somebody works out what that redirect is.
    """
    page = _read('templates/trophies/game_page.html')

    assert 'data-quick-add' in page, 'the concept page offers no way onto a list'
    assert 'data-concept-id="{{ concept.id }}"' in page
    assert 'Add to list' in page
    # The controller is loaded here too; a trigger with nothing listening is a dead button.
    assert "js/quick-add.js" in page

    # Same gate as everywhere else: the endpoints behind it are login- and link-gated, so the page
    # and the endpoint have to agree about who can act.
    assert 'concept and user.is_authenticated and user.profile.is_linked' in page


def test_the_trophy_list_page_offers_the_add_action(client):
    """The other detail page. It files the CONCEPT, not the trophy list, which is why it is gated on
    `game.concept_id`: a list whose games were reassigned has no concept to add."""
    concept = _concept('Findable Quest')
    game = concept.games.first()
    _member(client, psn='hunter')

    body = client.get(reverse('game_detail', args=[game.np_communication_id])).content.decode()

    assert 'data-quick-add' in body
    assert f'data-concept-id="{concept.id}"' in body
    assert 'js/quick-add.js' in body


def test_neither_detail_page_offers_it_to_a_visitor(client):
    """Same rule as the card: the endpoints behind it are login- and link-gated, so the page and the
    endpoint have to agree about who can act."""
    concept = _concept('Findable Quest')
    game = concept.games.first()

    for url in (concept.game_page_url, reverse('game_detail', args=[game.np_communication_id])):
        assert 'data-quick-add' not in client.get(url).content.decode(), url


def test_the_concept_pages_switcher_stays_put_without_the_button(client):
    """The tabs row became `justify-between` to seat the action on the left. With one child that
    renders identically to the `justify-end` it replaced -- the empty span is what holds that true
    for a viewer who gets no button, rather than letting the switcher drift to the centre."""
    page = _read('templates/trophies/game_page.html')

    row = page[page.index('id="gp-tabs-row"'):]
    row = row[:row.index('</div>', row.index('pp-switch'))]
    assert '{% else %}<span></span>{% endif %}' in row, \
        'the switcher centres itself for a signed-out reader'


# ── the audit round ──────────────────────────────────────────────────────────────────────────────

def test_infinite_scroll_appends_the_cell_not_the_bare_card(client):
    """THE WORST DEFECT IN THIS FEATURE, and it was invisible on page one.

    `InfiniteScroller` clones `cardSelector` nodes out of the fetched HTML. The quick-add button is a
    SIBLING of the card inside `.pp-gcard-wrap` -- it has to be, because the card is an `<a>` and a
    `<button>` in a link is invalid HTML -- so cloning the card alone dropped the wrapper and the
    button with it. Cards 1-30 had a way onto a list and every card after the first scroll did not,
    with nothing on screen to show the difference.

    `cellSelector` is the fix and it is opt-in, so no other scroller changes behaviour."""
    utils = _read('static/js/utils.js')

    assert 'config.cellSelector' in utils, 'the scroller cannot append anything but a bare card'
    clone = utils[utils.index('const newCards = doc.querySelectorAll(cardSelector)'):]
    clone = clone[:clone.index('page++')]
    assert "card.closest(config.cellSelector)" in clone, 'it still clones the card, not the cell'
    # ...and falls back, so a caller that passes nothing is untouched.
    assert '|| card' in clone

    # EVERY GRID THAT RENDERS THE BUTTON PASSES IT. Three of the four are JS, one is inline in a
    # template; missing one would reproduce the bug on that page alone, which is exactly the kind of
    # partial fix that survives review.
    for rel in ('templates/trophies/game_list.html', 'static/js/recently-added.js',
                'static/js/tag-detail.js', 'static/js/trophy-lists.js'):
        source = _read(rel)
        assert "cellSelector: '.pp-gcard-wrap'" in source, rel


def test_the_legacy_button_styling_is_gone(client):
    """A `.pp-gcard__add` block from the 2019 quick-add outlived its markup and collided with the
    rebuilt button, which reuses the class. Its `transform: scale(.82)` still applied -- the new sheet
    never sets `transform` -- while the two rules that cancelled it could not, because they matched
    the button as a DESCENDANT of `.pp-gcard` and it is now a sibling. The button drew at 82% with a
    36px hit area on every hover-capable device, under a comment claiming a 44px floor."""
    legacy = _read('static/css/components/game-card.css')

    # DECOMMENTED. The note left in that file explaining the removal naturally quotes the
    # declaration it removed, so a bare search matched prose -- the same trap this suite has now hit
    # often enough to be a habit rather than an accident.
    legacy_code = re.sub(r'/\*.*?\*/', '', legacy, flags=re.S)
    assert '.pp-gcard__add {' not in legacy_code, 'the legacy block is back, and it wins on nothing'
    assert 'scale(.82)' not in legacy_code
    # The one place it lives now.
    assert '.pp-gcard__add {' in _read('static/css/components/quick-add.css')


def test_the_popover_shows_what_the_server_actually_said(client):
    """`PP.API` throws an Error whose `.response` is the raw fetch Response, NOT a parsed body -- so
    `err.response.error` was always undefined and every refusal was replaced by a generic fallback.
    A hunter tapping a list at its 200-game cap was told "That could not be saved" instead of the
    sentence `add_concept` runs an extra query to get right."""
    js = _decommented(_read('static/js/quick-add.js'))

    assert 'function failureMessage(' in js
    assert 'err.response.json()' in js, 'the body is still never awaited'
    assert 'err.response.error' not in js, 'the property that is always undefined is back'
    # BOTH failure paths use it. Counted from the CALL sites only: the definition line contains the
    # same substring, so a bare count of three would have been satisfied by one caller plus the
    # function itself.
    assert js.count('failureMessage(err).then(') == 2


def test_the_popover_survives_the_gestures_a_phone_makes(client):
    """Two closes that fired on the most ordinary mobile actions. Android raises `resize` when the
    virtual keyboard opens -- and the popover focuses its "New list" field on open for a hunter with
    no lists yet, so it could vanish on the frame it appeared. And the inner row list chained its
    scroll to the document, whose `scroll` listener shut the panel mid-flick."""
    js = _decommented(_read('static/js/quick-add.js'))

    resize = js[js.index("window.addEventListener('resize'"):]
    resize = resize[:resize.index('});') + 3]
    assert 'place(openTrigger)' in resize, 'a resize still closes rather than repositions'

    css = _read('static/css/components/quick-add.css')
    assert 'overscroll-behavior: contain' in css, 'the inner scroll still chains to the document'


def test_the_popover_lets_go_of_the_page_it_was_anchored_to(client):
    """Browse Games swaps its grid on every filter change. The panel went on floating over the new
    results, anchored to a button no longer in the document -- and a row click still posted, filing a
    game the hunter could no longer see."""
    js = _decommented(_read('static/js/quick-add.js'))

    assert "htmx:afterSwap" in js, 'a filter change orphans the popover'
    assert 'openTrigger.isConnected' in js


def test_focus_comes_back_when_it_was_inside(client):
    """Every close path but Escape passed `restoreFocus = false`, so a keyboard user who scrolled or
    clicked away had the focused row deleted out from under them and focus reset to <body> -- the next
    Tab restarting at the top of the document. Whether focus needs restoring is a fact about the DOM,
    not a decision for the caller."""
    js = _decommented(_read('static/js/quick-add.js'))

    fn = js[js.index('function close('):js.index('function rowHtml(')]
    assert 'pop.contains(document.activeElement)' in fn, 'the caller still decides'
    # ...and read BEFORE the content is thrown away, or the answer is always false.
    assert fn.index('document.activeElement') < fn.index("pop.innerHTML = ''")


def test_the_trigger_does_not_swallow_the_click_other_menus_listen_for(client):
    """`stopPropagation` was left over from when the button lived inside the card's `<a>`. Stopping
    the click one node below `document` is where the site's other outside-click closers listen, so
    opening this left the nav search, the sub-nav menu and Browse Games' own discipline popovers
    hanging open behind it."""
    js = _decommented(_read('static/js/quick-add.js'))

    handler = js[js.index("var trigger = e.target.closest && e.target.closest('[data-quick-add]')"):]
    handler = handler[:handler.index('var row =')]
    assert 'stopPropagation' not in handler
    assert 'preventDefault' not in handler, 'a type=button outside a form has nothing to prevent'


def test_the_full_row_is_reachable_and_announced(client):
    """As a role-less `<span>` it was neither: a keyboard user never reached it, and a screen reader
    read "Backlog Full" as loose text indistinguishable from the heading -- which defeats the reason
    for rendering a full list rather than hiding it."""
    js = _decommented(_read('static/js/quick-add.js'))

    assert '<button type="button" class="qa-pop__row qa-pop__row--full" aria-disabled="true">' in js
    # Focusable means clickable, so the refusal moved into the handler.
    assert "row.getAttribute('aria-disabled') === 'true'" in js


def test_the_client_hardcodes_neither_a_route_nor_a_ceiling(client):
    """Three literals in a feature whose own endpoint comment argues that hand-assembling a path is
    what breaks silently the day one moves -- plus `maxlength="60"`, which `NAME_MAX_LENGTH`'s comment
    records having been written three times before somebody showed the number to a hunter."""
    js = _decommented(_read('static/js/quick-add.js'))

    assert '/community/lists/for-game/' not in js
    assert '/community/lists/new-with-game/' not in js
    assert 'href="/support/"' not in js
    assert 'maxlength="60"' not in js
    assert 'data.name_max_length' in js

    # ...and the server supplies all four.
    owner = _member(client)
    concept = _concept('Wanted')
    body = client.get(reverse('lists_for_concept', args=[concept.id])).json()
    assert body['name_max_length'] == NAME_MAX_LENGTH
    assert body['support_url'] == reverse('support_hub')

    card = _read('templates/trophies/partials/game_list/game_cards.html')
    assert 'data-lists-url' in card and 'data-create-url' in card


def test_creating_from_the_popover_records_the_event(client):
    """`CreateListView` wires `game_list_create` under a docstring explaining the event sat declared
    and unreachable from 2019. This is the same act by another door, and on current shape the busier
    one -- four grids and two detail pages against one modal."""
    views = _decommented(_read('gamelists/views.py'))

    block = views[views.index('class CreateListWithConceptView'):]
    block = block[:block.index('class ListGameSearchView')]
    assert "track_site_event('game_list_create'" in block, \
        'the busier creation path does not record the event'


def test_the_add_button_leaves_the_cover_art_on_touch(client):
    """Owner's call: a plus sitting permanently on every cover is noise on a catalogue you scan BY
    the cover -- the same argument the card's template makes about the scrim it removed. With no
    hover to reveal it, a touch device cannot have the desktop treatment, so the button moves into
    the text strip rather than being softened on the image."""
    css = _read('static/css/components/quick-add.css')

    touch = css[css.index('@media (hover: none) {'):]
    touch = touch[:touch.index('\n}', touch.index('.pp-gcard__add:active')) + 2]

    # OFF the cover, and anchored TO it. `bottom: 32px` was the first attempt -- derived from the
    # strip's padding, facts line and gap -- and it put the button in the card's bottom-right corner,
    # because `.pp-gcard__facts` becomes a two-row grid in one variant and anything measured up from
    # the bottom inherits every row below it. The cover's `aspect-ratio: 3 / 4` is the fixed thing, and
    # a PERCENTAGE MARGIN is the one place CSS converts a width into a height.
    assert 'margin-top: 133.333%;' in touch, 'it is no longer measured from the cover'
    assert 'bottom: 32px;' not in touch, 'the guess measured up from the bottom is back'
    # ...and the title makes room, or a two-line name runs underneath it.
    assert '.pp-gcard-wrap .pp-gcard__title { padding-right: 36px; }' in touch


def test_an_invisible_add_button_is_never_tappable(client):
    """THE BUG THE OWNER HIT, stated as the rule that prevents it.

    `opacity: 0` hides a control and keeps every one of its hit targets, and this button carries a
    44px `::before` for the touch floor. So an unrevealed button sat invisibly over each card's title
    and swallowed taps meant for the card: nothing on screen, nothing responding, which on a phone
    reads as the page freezing.

    The invariant is that the two move together. Every rule that sets one sets the other, so there is
    no state in which the button is invisible and live."""
    css = _read('static/css/components/quick-add.css')

    block = css[css.index('.pp-gcard__add {'):css.index('.qa-pop {')]

    # Wherever the button is hidden it is also inert...
    assert 'opacity: 0;' in block and 'pointer-events: none;' in block
    # ...and wherever it is shown it is live. Three places show it: hover/focus, touch, and while its
    # own popover is open.
    assert block.count('pointer-events: auto;') == 3, \
        'a rule shows the button without making it tappable, or hides it without making it inert'
    for shown in ('.pp-gcard-wrap:hover .pp-gcard__add,\n.pp-gcard__add:focus-visible '
                  '{ opacity: 1; pointer-events: auto; }',):
        assert shown in block

    # ON TOUCH IT IS SIMPLY THERE. No reveal state to get stuck in -- see the test below.
    #
    # SLICED TO THE ONE RULE, not to "everything after the media query opens". The looser slice ran
    # past the query's end and matched the `[aria-expanded]` rule's `opacity: 1`, so setting the touch
    # rule to `opacity: 0` -- losing the button on every phone, the reported bug -- left this passing.
    touch = block[block.index('@media (hover: none) {'):]
    touch_rule = touch[touch.index('.pp-gcard__add {'):touch.index('}')]
    assert 'opacity: 1;' in touch_rule, 'the button is hidden on touch, where nothing can reveal it'
    assert 'pointer-events: auto;' in touch_rule


def test_the_button_is_never_shown_by_the_reveal_class(client):
    """`.is-revealed` may HIDE this button and must never SHOW it, and the asymmetry is the whole
    lesson of three attempts at this arrival.

    Keying the button's appearance on `.is-revealed` is the fix that looks obviously right and is
    wrong: the class lands on frame 2 for every card in the batch, while what actually holds a card
    invisible through its stagger delay is the WAAPI animation's `fill: 'backwards'`. So a show-rule
    lights the button ~950ms before its card arrives -- the bug, with a test around it. (It also broke
    the button outright twice on the way here: a re-hide at (0,4,0) beating hover at (0,3,0), then
    invisible-but-tappable, which reads on a phone as the page freezing.)

    The arrival is solved in `staggerReveal` instead, by animating the CELL so the button is inside the
    animation. What survives in CSS is a hide, for the one frame before that animation exists."""
    css = _read('static/css/components/quick-add.css')

    assert '.pp-gcard.is-revealed ~ .pp-gcard__add' not in css, \
        'the show-coupling is back; it has broken this button twice and cannot fix it'
    assert '.pp-reveal .pp-gcard-wrap .pp-gcard__add' not in css


def test_the_long_press_route_was_not_taken(client):
    """Recorded because it was considered and rejected, and the reasons outlive the decision: a
    long-press would seize the browser's own long-press on a link across the main catalogue, it
    cannot be discovered, and it leaves a screen-reader user on a phone with no way in at all.

    The button being a real, permanent control in the strip IS the alternative."""
    js = _decommented(_read('static/js/quick-add.js'))

    assert 'touchstart' not in js and 'contextmenu' not in js, \
        'a long-press handler appeared without the discoverability and AT answers'

    # The control a screen reader reaches is a button with a name, on every grid card.
    card = _read('templates/trophies/partials/game_list/game_cards.html')
    assert '<button type="button" class="pp-gcard__add" data-quick-add' in card
    assert 'aria-label="Add ' in card


def test_holding_a_card_cannot_start_a_native_drag(client):
    """A link wrapping an image is draggable by default, so holding the mouse on a card begins a
    native drag of the link -- and while one is in flight the browser suppresses page clicks AND
    keyboard shortcuts. The page reads as frozen: the card stuck in `:active`, taps landing nowhere,
    Ctrl+R swallowed, and only the browser's own refresh button still working.

    Not caused by quick-add -- it is the default for every link-and-image on the web -- but it is what
    a long-press on a card does, and a page that looks frozen is worth more than a drag nobody
    performs on purpose.

    BOTH MECHANISMS, because neither covers every engine: the CSS property is WebKit's, and the
    attribute is what the others read. And the image needs its own, because images drag independently
    of the link around them."""
    css = _read('static/css/components/game-card.css')
    assert '.pp-gcard, .pp-gcard__art { -webkit-user-drag: none; }' in css

    card = _read('templates/trophies/partials/game_list/game_cards.html')
    assert 'class="pp-gcard" data-gcard draggable="false"' in card, 'the link is draggable again'
    assert '<img draggable="false"' in card, 'the cover art is draggable independently of its link'


def test_the_touch_callout_is_left_alone(client):
    """Deliberate, and the opposite of the arrange mode on list detail. On a real phone a long-press
    raises a dismissible sheet rather than locking anything, and that sheet is how somebody opens a
    game in a new tab from a catalogue. Suppressing it would take a browser capability away from the
    site's main browsing surface to stop a gesture that already does no harm there."""
    # DECOMMENTED. The note above the rule explains why the property is NOT used, so it names it --
    # and a bare search matched that prose. This is the sixteenth time this session an assertion of
    # mine has matched a comment instead of code; decommenting first is the habit, not the exception.
    css = _decommented_css(_read('static/css/components/game-card.css'))
    assert '-webkit-touch-callout' not in css, \
        'the catalogue lost open-in-new-tab to suppress a gesture that is harmless on a phone'


def test_an_appended_cell_still_reaches_the_reveal(client):
    """THE SECOND-ORDER BUG FROM THE cellSelector FIX, and the reason a shared-file change wants its
    own audit: the damage does not show up in the feature that caused it.

    `InfiniteScroller` now appends the `.pp-gcard-wrap` CELL so the button travels with its card. All
    four callers forward those appended nodes straight into `staggerReveal.observe`, which tested
    `nd.matches('.pp-gcard')` and silently dropped anything that was not itself a card. So page two
    onward was never observed, never got `.is-revealed`, and stayed at the `opacity: 0` the hide class
    holds it at -- thirty correctly-sized, completely blank cells per page, with the quick-add buttons
    (siblings, so not covered by the hide rule) floating over the voids.

    Invisible to a signed-out visitor, who never gets a wrapper. Invisible under reduced motion, where
    the reveal never arms. Which is to say: invisible in most casual testing."""
    utils = _decommented(_read('static/js/utils.js'))

    # SCOPED TO `staggerReveal`. `utils.js` has TWO `observe: function (nodes)` -- the other belongs
    # to `cardReveal`, which Career and job detail use for `.rp-row` and which filters nothing. The
    # unscoped slice found that one and failed over correct code in the function it was not testing.
    reveal = utils[utils.index('function staggerReveal('):]
    fn = reveal[reveal.index('observe: function (nodes) {'):reveal.index('disconnect: function ()')]
    # It must accept a node that CONTAINS a card, not only one that IS a card.
    assert 'nd.querySelector(sel)' in fn, 'an appended cell is dropped and its card never reveals'
    # ...and still take the card itself, which is what every other caller hands it.
    assert 'nd.matches(sel) ? nd' in fn

    # The pairing that makes this necessary: the scroller appends cells for exactly these grids.
    scroller = _decommented(_read('static/js/utils.js'))
    assert 'config.cellSelector' in scroller


def test_the_reveal_animates_the_cell_so_the_button_rides_with_its_card():
    """The button is the card's SIBLING, so it does not inherit the grid's staggered reveal.

    On touch it is visible at rest, so that left a row of grey plus signs hanging in empty space for up
    to a second while the cards faded up beneath them -- and touch also makes it tappable, so a tap in
    that window opened a picker for a card not yet on screen.

    THE OBVIOUS FIX IS THE WRONG ONE. A sibling rule keyed on `.is-revealed` (the shape already
    shipping for `.gl-item__remove`) looks like it covers this and does not: `.is-revealed` lands on
    frame 2 for every card in the batch, while what actually holds a card invisible through its stagger
    delay is the WAAPI animation's `fill: 'backwards'` -- which a selector cannot see. That rule would
    have lit the button ~950ms early and looked fixed.

    Only something INSIDE the animation is hidden by the animation, so the cell is what animates."""
    utils = _decommented(_read('static/js/utils.js'))

    stagger = utils[utils.index('function staggerReveal('):utils.index('function arriveOnScroll(')]
    assert "var target = (o.cellSelector && el.closest(o.cellSelector)) || el;" in stagger, \
        'the reveal plays on the card, so a control beside it is outside the animation'
    assert 'o.reveal(target, delay);' in stagger

    # ...and every grid that renders a cell asks for it. A caller that forgets is a grid whose button
    # hangs still over an arriving card -- the bug itself, on one page.
    for path in ('templates/trophies/game_list.html', 'static/js/tag-detail.js',
                 'static/js/recently-added.js', 'static/js/trophy-lists.js'):
        src = _decommented(_read(path))
        # The config line only -- the callback below it is full of `});` and would end the slice early.
        block = src[src.index('staggerReveal({'):][:200]
        assert "cellSelector: '.pp-gcard-wrap'" in block, f'{path} reveals the bare card'


def test_the_button_is_hidden_and_inert_until_its_card_reveals():
    """The one frame before the animation exists, and an appended cell still waiting on the observer.

    Hidden is not enough on its own: `opacity: 0` keeps every hit target, and this button carries a
    44px `::before`. The pair is the rule -- the same pairing the base block documents."""
    css = _decommented_css(_read('static/css/components/quick-add.css'))

    rule = css[css.index('.pp-reveal .pp-gcard:not(.is-revealed) ~ .pp-gcard__add'):]
    rule = rule[:rule.index('}') + 1]
    assert 'opacity: 0' in rule
    assert 'pointer-events: none' in rule, 'invisible but tappable is the page-freeze bug'

    # Guarded by the same media query as the card's own hide rule: they are halves of one behaviour,
    # and a reduced-motion viewer never gets `.pp-reveal` at all.
    before = css[:css.index('.pp-reveal .pp-gcard:not(.is-revealed) ~ .pp-gcard__add')]
    assert before.rstrip().endswith('@media (prefers-reduced-motion: no-preference) {')


def test_the_popover_survives_the_ios_keyboard_raising():
    """A hunter with NO lists gets the New list field focused on open. On iOS that raises the keyboard,
    which SCROLLS the document to lift the field clear of it -- and the scroll handler closed on any
    scroll, so the panel vanished on the frame it appeared. Every time, for exactly the first-run
    hunter the empty-state copy is written for.

    The `resize` handler one block down was already written for this same keyboard; the reasoning was
    never carried across to `scroll`."""
    js = _decommented(_read('static/js/quick-add.js'))

    handler = js[js.index('function onScrollSettled()'):]
    handler = handler[:handler.index("window.addEventListener('resize'")]

    # THE TRIGGER'S VISIBILITY IS THE TEST. Not the scroll (which the keyboard causes by itself), and
    # NOT whether the focus is ours -- `open()` focuses the panel on every open, mouse included, so
    # that test is true for every loaded popover and scroll-to-close stops existing altogether.
    assert 'getBoundingClientRect()' in handler
    assert 'r.bottom <= 0 || r.top >= vh' in handler, \
        'the panel follows its card off the screen instead of closing'
    assert 'place(openTrigger)' in handler

    # The branch `resize` has always had and this one was missing: an anchor that no longer exists
    # cannot be followed, and the panel must not be left floating against a detached node.
    assert 'if (!openTrigger || !openTrigger.isConnected) { close(false); return; }' in handler

    # `place()` forces two reflows, so a raw scroll listener calling it is a per-event reflow storm.
    assert 'requestAnimationFrame(onScrollSettled)' in js


def test_an_arriving_cell_is_inert_until_its_animation_finishes():
    """THE HALF `.is-revealed` CANNOT DO, and the correction to the first attempt at this.

    OPACITY DOES NOT AFFECT HIT-TESTING. Animating the cell hid the button -- and left it catching
    every tap, because the class that releases it lands one frame into a stagger the animation holds
    for up to ~950ms more. So the control was invisible and live over a card that was not on screen,
    which is the freeze, reached by a different road.

    Only the animation knows when the cell is really there, so the mark is lifted by the animation."""
    utils = _decommented(_read('static/js/utils.js'))
    stagger = utils[utils.index('function staggerReveal('):utils.index('function arriveOnScroll(')]

    assert "target.classList.add('pp-arriving')" in stagger
    # Lifted by the animation finishing, NOT by a timer and NOT by `.is-revealed`.
    assert 'a.finished' in stagger and "removeProperty" not in stagger
    assert stagger.count("target.classList.remove('pp-arriving')") == 3, \
        'an ending that does not hand the cell back leaves the grid permanently dead'

    # A cancelled animation rejects; a cancelled arrival must still release the cell.
    finish = stagger[stagger.index('Promise.all(running.map'):]
    assert '.catch(' in finish[:400], 'a cancelled reveal leaves the cell inert forever'

    # And the mark has to mean something.
    motion = _decommented_css(_read('static/css/components/motion.css'))
    assert '.pp-arriving { pointer-events: none; }' in motion


def test_the_list_detail_controls_ride_their_tile_and_do_not_stick_on():
    """Two bugs in one pair of rules, both from releasing a control with `.is-revealed`.

    It lands a frame into the reveal, so the remove "x" and the drag grip came back long before their
    tiles did. And because the release SET opacity at (0,4,0), it outranked the (0,2,0) hover rule for
    the rest of the page's life: every tile on desktop wore a permanent "x" instead of showing one on
    hover -- a rule meant to last 500ms quietly disabling the control's whole resting design."""
    css = _decommented_css(_read('static/css/components/gamelists.css'))

    for control in ('.gl-item__remove', '.gl-item__grab'):
        assert f'.pp-reveal .gl-item .pp-gcard.is-revealed ~ {control}' not in css, \
            f'{control} is released by a class that outranks its own hover rule forever'
        rule = css[css.index(f'.pp-reveal .gl-item .pp-gcard:not(.is-revealed) ~ {control}'):]
        rule = rule[:rule.index('}') + 1]
        assert 'opacity: 0' in rule and 'pointer-events: none' in rule

    # The arrival itself belongs to the engine, which animates the cell these controls live in.
    js = _decommented(_read('static/js/list-detail.js'))
    block = js[js.index('staggerReveal({'):][:200]
    assert "cellSelector: '.gl-item'" in block


def test_no_stylesheet_shows_a_control_on_is_revealed():
    """The guard that was scoped to one file while a second file did the forbidden thing.

    `.is-revealed` may HIDE a card's sibling control and must never SHOW one: it lands on frame 2 for
    the whole batch, while the WAAPI `backwards` fill is what actually holds a cell down. Every such
    rule is both ~950ms early AND, being specific enough to win, permanent."""
    root = Path(__file__).resolve().parents[2] / 'static' / 'css'
    offenders = []
    for path in root.rglob('*.css'):
        if path.name == 'output.css':
            continue        # generated; it can only contain what the sources above already hold
        for line in _decommented_css(path.read_text(encoding='utf-8')).splitlines():
            if '.is-revealed ~' in line and 'opacity: 1' in line:
                offenders.append(f'{path.name}: {line.strip()}')
    assert not offenders, 'a control is shown by .is-revealed: ' + '; '.join(offenders)
