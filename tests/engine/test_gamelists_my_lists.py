"""My Lists, and the create modal's form.

Built from scratch rather than ported: unlike browse and list detail, this page was never rebuilt in
2026-08 and was still a pre-rebuild DaisyUI shell.

Two things here are decisions rather than mechanics, and both get pinned. A list is PRIVATE when it
is born and publishing is a separate act -- so the create form has no visibility control at all, and
passing one must not work. And "Following" is the only place a follow means anything this release,
since there is no notification surface; the tab is the feature, not decoration on it.
"""
import pytest
from django.urls import reverse

from gamelists.models import GameList
from gamelists.services import game_list_service as svc
from tests.factories import ConceptFactory, GameFactory, ProfileFactory, UserFactory

pytestmark = pytest.mark.django_db

MY_LISTS = '/my-lists/'

#: The private chip's title, which is unique to the chip. Asserting on the bare word "Private"
#: matches the create modal's "Private to start" copy, and asserting a list NAME like "Mine" matches
#: the scope switcher's own chip label -- both of which made an early version of these tests pass or
#: fail for reasons that had nothing to do with the grid.
PRIVATE_CHIP = 'Only you can see this list'


def _grid(body):
    """Just the tile grid, so a substring assertion cannot be answered by the page chrome."""
    start = body.index('pp-gtile-grid')
    return body[start:body.index('</div>', body.rindex('pp-gtile', start))]


def _staff_hunter(client, psn='curator', premium=False):
    """Staff because the surface is gated while the branch is open; linked because a list belongs to
    a profile. Both are real requirements of the page, not test scaffolding."""
    user = UserFactory()
    user.role = 'admin'
    user.save()
    profile = ProfileFactory(user=user, is_linked=True, psn_username=psn)
    if premium:
        profile.user_is_premium = True
        profile.save(update_fields=['user_is_premium'])
    client.force_login(user)
    return profile


# ── the page ─────────────────────────────────────────────────────────────────────────────────────

def test_my_lists_shows_my_private_lists_too():
    """The whole point of "mine". Browse is the public catalogue; this is your shelf."""
    from django.test import Client

    client = Client()
    profile = _staff_hunter(client)
    svc.create_list(profile, name='Kept back')
    svc.create_list(profile, name='Shared', is_public=True)

    body = client.get(MY_LISTS).content.decode()

    assert 'Kept back' in body
    assert 'Shared' in body


def test_my_lists_does_not_show_anybody_elses(client):
    profile = _staff_hunter(client)
    stranger = ProfileFactory(is_linked=True, psn_username='stranger')
    svc.create_list(stranger, name='Not yours', is_public=True)
    svc.create_list(profile, name='Mine')

    grid = _grid(client.get(MY_LISTS).content.decode())

    assert 'Mine' in grid
    assert 'Not yours' not in grid


def test_a_private_list_is_marked_and_a_public_one_is_not(client):
    """Marking the PRIVATE state rather than the public one: a list is private by default, so the
    chip marks the exception and its absence is the signal that this one is out in the world."""
    profile = _staff_hunter(client)
    svc.create_list(profile, name='Hidden one')

    assert PRIVATE_CHIP in client.get(MY_LISTS).content.decode()

    svc.update_list(GameList.objects.get(name='Hidden one'), profile, is_public=True)
    assert PRIVATE_CHIP not in client.get(MY_LISTS).content.decode()


def test_the_public_browse_grid_never_marks_privacy(client):
    """Every tile there is public by definition, so a chip on all of them is noise."""
    profile = _staff_hunter(client)
    svc.create_list(profile, name='Out there', is_public=True)

    assert PRIVATE_CHIP not in client.get('/community/lists/').content.decode()


def test_the_cap_is_shown_rather_than_discovered_by_being_refused(client):
    profile = _staff_hunter(client)
    svc.create_list(profile, name='One')

    resp = client.get(MY_LISTS)

    assert resp.context['list_count'] == 1
    assert resp.context['list_cap'] == svc.max_lists_for(profile)
    assert resp.context['at_cap'] is False


def test_at_the_cap_the_create_button_is_disabled_not_hidden(client):
    """A button that vanishes reads as a bug; a disabled one with a reason reads as a rule."""
    profile = _staff_hunter(client)
    for n in range(svc.max_lists_for(profile)):
        svc.create_list(profile, name=f'List {n}')

    resp = client.get(MY_LISTS)
    body = resp.content.decode()

    assert resp.context['at_cap'] is True
    assert 'disabled' in body
    assert 'New list' in body, 'the button disappeared instead of explaining itself'


def test_the_page_is_query_flat_as_lists_are_added(client):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    profile = _staff_hunter(client, premium=True)

    def build(count, tag):
        for n in range(count):
            game_list = svc.create_list(profile, name=f'{tag}{n}')
            for _ in range(3):
                concept = ConceptFactory()
                GameFactory(concept=concept, title_platform='PS5')
                svc.add_concept(game_list, profile, concept)

    def measure():
        with CaptureQueriesContext(connection) as ctx:
            client.get(MY_LISTS)
        return len([q for q in ctx.captured_queries if 'gamelists_' in q['sql']])

    build(2, 'a')
    few = measure()
    build(6, 'b')
    many = measure()

    assert few == many, f'{few} queries for 2 lists, {many} for 8'


# ── the scope switcher, which is where a follow finally means something ──────────────────────────

def test_following_shows_lists_i_follow_and_not_my_own(client):
    profile = _staff_hunter(client)
    author = ProfileFactory(is_linked=True, psn_username='author')
    theirs = svc.create_list(author, name='Theirs', is_public=True)
    svc.create_list(profile, name='Mine')
    svc.set_follow(theirs, profile, following=True)

    grid = _grid(client.get(MY_LISTS, {'scope': 'following'}).content.decode())

    assert 'Theirs' in grid
    assert 'Mine' not in grid


def test_unpublishing_removes_a_list_from_everyone_elses_following_tab(client):
    """Otherwise a follow becomes a private window into somebody's library after they close it."""
    profile = _staff_hunter(client)
    author = ProfileFactory(is_linked=True, psn_username='author')
    theirs = svc.create_list(author, name='Was public', is_public=True)
    svc.set_follow(theirs, profile, following=True)

    svc.update_list(theirs, author, is_public=False)

    assert 'Was public' not in _grid(
        client.get(MY_LISTS, {'scope': 'following'}).content.decode())


def test_a_junk_scope_falls_back_to_mine(client):
    profile = _staff_hunter(client)
    svc.create_list(profile, name='Mine')

    resp = client.get(MY_LISTS, {'scope': 'nonsense'})

    assert resp.context['scope'] == 'mine'
    assert 'Mine' in _grid(resp.content.decode())


def test_the_empty_states_differ_by_scope(client):
    """"No lists yet" is an invitation; "not following any" is a different situation and saying the
    first one there would be wrong about what happened."""
    _staff_hunter(client)

    assert 'No lists yet' in client.get(MY_LISTS).content.decode()
    assert 'Not following any lists' in client.get(
        MY_LISTS, {'scope': 'following'}).content.decode()


# ── create ───────────────────────────────────────────────────────────────────────────────────────

def test_creating_a_list_makes_it_private(client):
    """The decision that makes the public/private state mean anything. Offering the toggle at
    creation would make it a checkbox somebody ticks while thinking about a name."""
    profile = _staff_hunter(client)

    client.post(reverse('list_create'), {'name': 'Fresh'})

    game_list = GameList.objects.get(owner=profile)
    assert game_list.name == 'Fresh'
    assert game_list.is_public is False


def test_the_create_form_offers_no_way_to_publish_and_ignores_one_if_posted(client):
    """Both halves: the form has no control, AND forging the field does nothing. A form-only
    guarantee is not a guarantee."""
    profile = _staff_hunter(client)

    body = client.get(MY_LISTS).content.decode()
    form = body[body.index('id="gl-create"'):]
    assert 'is_public' not in form, 'the create modal grew a visibility control'

    client.post(reverse('list_create'), {'name': 'Forged', 'is_public': 'true'})

    assert GameList.objects.get(owner=profile).is_public is False


def test_a_refused_create_says_why_and_writes_nothing(client):
    profile = _staff_hunter(client)
    for n in range(svc.max_lists_for(profile)):
        svc.create_list(profile, name=f'List {n}')

    resp = client.post(reverse('list_create'), {'name': 'One too many'}, follow=True)

    assert GameList.objects.owned_by(profile).count() == svc.max_lists_for(profile)
    assert 'limit' in ' '.join(str(m) for m in resp.context['messages'])


def test_a_blank_name_is_refused_by_the_endpoint_too(client):
    """The service is the rule; this proves the endpoint routes a refusal into a message rather than
    a 500 or a silent no-op."""
    profile = _staff_hunter(client)

    resp = client.post(reverse('list_create'), {'name': '   '}, follow=True)

    assert GameList.objects.owned_by(profile).count() == 0
    assert 'name' in ' '.join(str(m) for m in resp.context['messages']).lower()


def test_creating_is_a_post_only_endpoint(client):
    _staff_hunter(client)

    assert client.get(reverse('list_create')).status_code == 405


# ── the guards on the page itself ────────────────────────────────────────────────────────────────

def test_an_account_with_no_linked_profile_is_sent_to_link_psn(client):
    """A list belongs to a profile, so there is nothing to show -- and reading
    `request.user.profile` without this raises RelatedObjectDoesNotExist and 500s the page."""
    user = UserFactory()
    user.role = 'admin'
    user.save()
    client.force_login(user)

    resp = client.get(MY_LISTS)

    assert resp.status_code == 302
    assert 'link' in resp.url.lower()


def test_creating_without_a_linked_profile_does_not_500(client):
    user = UserFactory()
    user.role = 'admin'
    user.save()
    client.force_login(user)

    resp = client.post(reverse('list_create'), {'name': 'Nope'})

    assert resp.status_code == 302
    assert GameList.objects.count() == 0
