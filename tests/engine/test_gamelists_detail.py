"""One list, on its own page.

The surface where the owner will edit in place. This commit is the READ half plus the affordances;
the writes (rename, add, remove, reorder, publish) follow.

Two rules carry most of the weight here. A private list must 404 rather than 403 for everyone but
its owner -- a 403 confirms the list exists and whose it is, from nothing but an id. And the page
must not scale with the list: a 200-game list should cost what a 5-game one does.
"""
import re
from pathlib import Path

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from gamelists.models import LIST_TYPE_RANKED, GameListSection
from gamelists.services import game_list_service as svc
from tests.factories import ConceptFactory, GameFactory, ProfileFactory, UserFactory

pytestmark = pytest.mark.django_db


def _staff(client, psn='owner', premium=False):
    user = UserFactory()
    user.role = 'admin'
    user.save()
    profile = ProfileFactory(user=user, is_linked=True, psn_username=psn)
    if premium:
        profile.user_is_premium = True
        profile.save(update_fields=['user_is_premium'])
    client.force_login(user)
    return profile


def _list(owner, games=0, *, name='A list', public=True):
    game_list = svc.create_list(owner, name=name, is_public=public)
    for n in range(games):
        concept = ConceptFactory(unified_title=f'Game {n:03d}')
        GameFactory(concept=concept, title_platform=['PS5'])
        svc.add_concept(game_list, owner, concept)
    return game_list


def _url(game_list):
    return f'/community/lists/{game_list.id}/'


def _read(relative):
    return (Path(__file__).resolve().parents[2] / relative).read_text(encoding='utf-8')


def _decommented(source):
    """Strip comments before asserting a call exists.

    Written after several assertions on this branch were satisfied by prose that merely NAMED the
    thing -- a comment explaining `wireTablist`, a docstring mentioning `hx-swap="outerHTML"`. A
    comment is a claim; only code is evidence.

    Line comments are cut only where `//` opens the line (after indentation). A mid-line rule would
    also slice `'http://www.w3.org/2000/svg'` in half and quietly change what is being searched.
    """
    source = re.sub(r'/\*.*?\*/', '', source, flags=re.S)
    return re.sub(r'^\s*//.*$', '', source, flags=re.M)


# ── who can read it ──────────────────────────────────────────────────────────────────────────────

def test_a_public_list_renders_for_a_visitor(client):
    owner = ProfileFactory(is_linked=True, psn_username='author')
    game_list = _list(owner, 2, name='Out in the world')
    _staff(client, psn='reader')

    resp = client.get(_url(game_list))

    assert resp.status_code == 200
    assert 'Out in the world' in resp.content.decode()


def test_a_private_list_is_404_for_everyone_but_its_owner(client):
    """404, not 403. A 403 confirms the list exists and who owns it, from an id alone -- the same
    oracle the like and follow endpoints refuse to be."""
    author = ProfileFactory(is_linked=True, psn_username='author')
    private = _list(author, 1, name='Kept back', public=False)
    _staff(client, psn='stranger')

    assert client.get(_url(private)).status_code == 404


def test_the_owner_can_read_their_own_private_list(client):
    owner = _staff(client)
    private = _list(owner, 1, name='Mine alone', public=False)

    resp = client.get(_url(private))

    assert resp.status_code == 200
    assert 'Mine alone' in resp.content.decode()
    assert resp.context['is_owner'] is True


def test_a_soft_deleted_list_is_gone_even_for_its_owner(client):
    owner = _staff(client)
    game_list = _list(owner, 1)
    svc.delete_list(game_list, owner)

    assert client.get(_url(game_list)).status_code == 404


# ── the affordances ──────────────────────────────────────────────────────────────────────────────

def test_a_visitor_gets_the_social_actions_and_the_owner_does_not(client):
    """Nobody gets both, because there is no state in which both apply -- you cannot like your own
    list (the service refuses it) and you have no use for following it.

    BOTH halves. The first version only ever loaded the page as the reader, so the half after the
    "and" was unasserted and a `can_act` that was unconditionally true would have passed.
    """
    from django.test import Client

    author = ProfileFactory(is_linked=True, psn_username='author')
    game_list = _list(author, 1, public=False)
    svc.update_list(game_list, author, is_public=True)

    _staff(client, psn='reader')
    reader_body = client.get(_url(game_list)).content.decode()
    assert 'data-gl-like' in reader_body
    assert 'data-gl-follow' in reader_body
    assert 'data-gl-publish' not in reader_body

    owner_client = Client()
    owner_client.force_login(author.user)
    author.user.role = 'admin'
    author.user.save()
    owner_body = owner_client.get(_url(game_list)).content.decode()
    assert 'data-gl-like' not in owner_body, 'the owner is offered a like the service would refuse'
    assert 'data-gl-follow' not in owner_body


def test_the_owner_gets_a_publish_affordance_only_while_it_is_private(client):
    """Publishing is the deliberate second act. Once done there is nothing to offer, so the page says
    what state it is in rather than showing a button that would do nothing.

    BOTH states are now rendered and `hidden` picks which shows, so that publishing completes on the
    page it started on instead of needing a reload. The assertion therefore moved from presence to
    visibility -- the intent is unchanged, and a bug that showed both at once still fails here.
    """
    owner = _staff(client)
    private = _list(owner, 1, name='Draft', public=False)

    body = client.get(_url(private)).content.decode()
    assert 'data-gl-publish' in body
    assert 'Only you can see this so far' in body
    assert 'data-gl-private-state hidden' not in body, 'the publish control is hidden while private'
    assert 'data-gl-public-state hidden' in body

    svc.update_list(private, owner, is_public=True)
    body = client.get(_url(private)).content.decode()
    assert 'data-gl-private-state hidden' in body, 'publish is still offered on a published list'
    assert 'data-gl-public-state hidden' not in body
    assert 'Published' in body
    # Unpublishing is offered, which the service always allowed and the UI never could.
    assert 'data-gl-unpublish' in body


def test_an_anonymous_reader_gets_no_action_buttons(client):
    """They cannot act, so offering the buttons would be a promise the endpoints refuse."""
    author = ProfileFactory(is_linked=True, psn_username='author')
    game_list = _list(author, 1)
    staff = UserFactory()
    staff.role = 'admin'
    staff.save()
    client.force_login(staff)          # staff, but no linked profile -> no viewer

    body = client.get(_url(game_list)).content.decode()

    assert 'data-gl-like' not in body
    assert 'data-gl-follow' not in body


def test_the_like_state_reflects_the_viewer(client):
    author = ProfileFactory(is_linked=True, psn_username='author')
    game_list = _list(author, 1)
    reader = _staff(client, psn='reader')

    # Sliced to the LIKE button: the follow button carries `aria-pressed` too and reads "false" for
    # a fresh viewer, so the unsliced form passed with the like button hardcoded to "true".
    fresh = client.get(_url(game_list)).content.decode()
    like_btn = fresh[fresh.index('data-gl-like'):]
    assert 'aria-pressed="false"' in like_btn[:like_btn.index('</button>')]

    svc.set_like(game_list, reader, liked=True)
    body = client.get(_url(game_list)).content.decode()
    assert 'aria-pressed="true"' in body
    assert 'Liked' in body


def test_the_follow_button_says_follow(client):
    """Named for where the feature is going, not for what it does today.

    The notification surface is not built yet, so right now a follow only surfaces under
    My Lists > Following -- but a social verb is a word people LEARN, and renaming one after they
    have learned it costs more than the gap. Jeffrey's call, and the right one.

    The honesty lives in the surrounding copy instead: this asserts that nothing beside the button
    promises an alert that cannot yet arrive, so when notifications land there is a feature to add
    and nothing to walk back.
    """
    author = ProfileFactory(is_linked=True, psn_username='author')
    game_list = _list(author, 1)
    _staff(client, psn='reader')

    body = client.get(_url(game_list)).content.decode()
    button = body[body.index('data-gl-follow'):]
    button = button[:button.index('</button>')]

    # `'Following' CONTAINS 'Follow'`, so `assert 'Follow' in button` passed against a button
    # hardcoded to the followed state -- and the sibling test only checks the followed case, so
    # between them the whole round-trip was untested. The fresh state has to be asserted as what it
    # is NOT, plus the pressed state, which is what the Like test three functions up already does.
    assert 'Follow' in button
    assert 'Following' not in button, 'a fresh viewer is shown as already following'
    assert 'aria-pressed="false"' in button, 'the follow button does not report its state'
    assert 'Save' not in button

    # No claim of alerts anywhere on the page while there is nowhere for one to arrive.
    #
    # SCANNED AS VISIBLE TEXT, not as raw HTML. This is about what the page SAYS to a reader, and
    # against the raw markup it also matched `role="alert"` on the report dialog's error line --
    # an ARIA role, not a promise, and exactly the kind of false positive that gets a correct
    # attribute deleted to make a test green. Scripts and styles come out first so a library's
    # internals cannot trip it either.
    text = re.sub(r'<(script|style)\b.*?</\1>', ' ', body, flags=re.S | re.I)
    text = re.sub(r'<[^>]+>', ' ', text).lower()
    for promise in ('notify', 'notified', 'alert', "we'll let you know", 'get updates'):
        assert promise not in text, f'the page promises {promise!r} with no surface for it'


def test_a_followed_list_reads_as_following(client):
    author = ProfileFactory(is_linked=True, psn_username='author')
    game_list = _list(author, 1)
    reader = _staff(client, psn='reader')
    svc.set_follow(game_list, reader, following=True)

    body = client.get(_url(game_list)).content.decode()
    button = body[body.index('data-gl-follow'):]
    button = button[:button.index('</button>')]

    assert 'Following' in button
    assert 'aria-pressed="true"' in button


# ── the games ────────────────────────────────────────────────────────────────────────────────────

def test_the_page_does_not_scale_with_the_list(client):
    """The version this replaces resolved the cover chain per item and reached 82 queries for a
    40-game list. The concept re-key could reintroduce that shape, since a cover needs a Game and an
    item now holds a Concept."""
    owner = _staff(client)
    short = _list(owner, 3, name='Short')
    long_one = _list(owner, 30, name='Long')

    def cost(game_list):
        with CaptureQueriesContext(connection) as ctx:
            resp = client.get(_url(game_list))
        assert resp.status_code == 200, 'the page did not render; the count proves nothing'
        # `trophies_igdbmatch` and `trophies_concept` are in the filter deliberately: the tile
        # walks `concept.game_page_url`, so leaving them out hides the exact N+1 this test exists
        # to catch -- which is what happened until the raw_response guard caught it instead.
        watched = ('gamelists_', 'trophies_game"', 'trophies_concept', 'trophies_igdbmatch')
        return len([q for q in ctx.captured_queries
                    if any(table in q['sql'] for table in watched)])

    assert cost(long_one) == cost(short), 'the cover chain is resolving per item again'


def test_the_cover_join_does_not_drag_the_igdb_blob_along(client):
    owner = _staff(client)
    game_list = _list(owner, 3)

    with CaptureQueriesContext(connection) as ctx:
        client.get(_url(game_list))

    joined = [q['sql'] for q in ctx.captured_queries if 'igdb' in q['sql'].lower()]
    assert joined, 'the cover chain is not being joined at all'
    for sql in joined:
        assert 'raw_response' not in sql.lower()


def test_entries_link_to_the_game_not_to_a_trophy_list(client):
    """Entries are concept-keyed, which is the whole point -- so they link to the concept page."""
    owner = _staff(client)
    concept = ConceptFactory(unified_title='Linked Game')
    GameFactory(concept=concept, title_platform=['PS5'])
    game_list = _list(owner)
    svc.add_concept(game_list, owner, concept)

    body = client.get(_url(game_list)).content.decode()

    assert 'Linked Game' in body
    # A METHOD, not a property -- Django templates call it for you, a test has to.
    concept.refresh_from_db()
    assert concept.game_page_url() in body


@pytest.mark.parametrize('sort, expected', [
    ('name', ['alpha', 'Mike', 'Zulu']),
    ('name_desc', ['Zulu', 'Mike', 'alpha']),
    ('added', ['Mike', 'alpha', 'Zulu']),
    ('oldest', ['Zulu', 'alpha', 'Mike']),
])
def test_every_offered_sort_actually_sorts(client, sort, expected):
    """Order, not an echo of the parameter.

    The titles are deliberately NOT in alphabetical insertion order. They were First/Second/Third,
    whose alphabetical order IS their insertion order -- so the `name` case expected exactly what
    insertion order produces, and a view that ignored `sort=name` entirely passed it.

    `alpha` is lowercase on purpose, though NOT for the reason first written here. That docstring
    claimed a raw column sort would file it after Zulu and only `Lower()` puts it first. False on
    this database: `lc_collate` is `en_US.utf8`, whose collation already ignores case for ordering,
    so raw and `lower()` agree. Swapping `Lower()` for a plain column sort was mutation-tested and
    passed, which is what exposed the claim.

    So this asserts the ORDER, which is what a reader cares about, and does not pretend to pin the
    `Lower()` call -- nothing observable on this database can, and a test that claims otherwise is
    the vacuous kind. The mixed case is kept because it is realistic, not because it discriminates.
    """
    owner = _staff(client)
    game_list = _list(owner)
    for title in ('Zulu', 'alpha', 'Mike'):
        concept = ConceptFactory(unified_title=title)
        GameFactory(concept=concept, title_platform=['PS5'])
        svc.add_concept(game_list, owner, concept)

    resp = client.get(_url(game_list), {'sort': sort})

    assert [i.concept.unified_title for i in resp.context['items']] == expected


def test_a_collection_offers_no_curated_order(client):
    """A Collection is UNORDERED by design, so it has no "List order" sort and no drag. An ordered
    list is a different TYPE (Ranked); see docs/design/game-list-types.md.

    `position` itself stays and stays dense -- it is insertion order here, and `attach_cover_games`
    bounds the tile mosaic on `position__lt=4`, so a gap would render a three-cover mosaic on a
    four-game list.

    TWO OF THESE ASSERTIONS WERE VACUOUS once Ranked shipped, and were rewritten rather than left
    passing. They named `'position'` as the sort key and `data-gl-drag` as the attribute; the
    implementation calls them `rank` and `data-gl-reorder`, so both checked for strings that appear
    nowhere in the codebase and would have passed against a Collection rendering full drag handles.
    Guessing an identifier the code does not use is the easiest way to write a test that cannot fail.
    """
    owner = _staff(client)
    game_list = _list(owner, 3)

    resp = client.get(_url(game_list))
    body = resp.content.decode()

    assert resp.context['sort'] == 'name', 'a shelf should default to being findable, i.e. A-Z'
    assert 'rank' not in dict(resp.context['sort_choices'])
    assert resp.context['is_ranked'] is False
    assert resp.context['can_reorder'] is False
    assert 'List order' not in body
    assert 'data-gl-reorder' not in body
    assert 'data-gl-grab' not in body

    # The field is still dense, which is what the mosaic depends on.
    assert list(game_list.items.order_by('position').values_list('position', flat=True)) == [0, 1, 2]


def _ranked(owner, games=0, **kwargs):
    game_list = _list(owner, games, **kwargs)
    svc.update_list(game_list, owner, list_type=LIST_TYPE_RANKED)
    game_list.refresh_from_db()
    return game_list


def test_a_ranked_list_leads_with_the_order_its_author_chose(client):
    owner = _staff(client)
    game_list = _ranked(owner, 3)

    resp = client.get(_url(game_list))

    assert resp.context['is_ranked'] is True
    assert resp.context['sort'] == 'rank', 'a ranked list should open on its real sequence'
    assert 'rank' in dict(resp.context['sort_choices'])
    assert 'List order' in resp.content.decode()


def test_a_ranked_list_renders_one_based_numerals_in_position_order(client):
    """`position` is 0-indexed; a reader counts from 1. Off-by-one here is silent and wrong on every
    row at once."""
    owner = _staff(client)
    game_list = _ranked(owner)
    for title in ('Zulu', 'Alpha', 'Mike'):
        concept = ConceptFactory(unified_title=title)
        GameFactory(concept=concept, title_platform=['PS5'])
        svc.add_concept(game_list, owner, concept)

    body = client.get(_url(game_list)).content.decode()

    ranks = re.findall(r'<span class="gl-rank"[^>]*>(\d+)</span>', body)
    assert ranks == ['1', '2', '3'], 'numerals should start at 1 and run down the list'
    # ...against the ORDER, or the numerals could be right while the games are shuffled.
    assert [i.concept.unified_title for i in client.get(_url(game_list)).context['items']] \
        == ['Zulu', 'Alpha', 'Mike']


def test_the_numeral_survives_a_different_sort_but_the_handles_do_not(client):
    """A rank is a fact about the ENTRY, so it still reads "#3" when the page is sorted A-Z. Dragging
    is a fact about the VIEW: rearranging a sorted page would post an order that means nothing, so
    the handles go while the numerals stay."""
    owner = _staff(client)
    game_list = _ranked(owner)
    for title in ('Zulu', 'Alpha'):
        concept = ConceptFactory(unified_title=title)
        GameFactory(concept=concept, title_platform=['PS5'])
        svc.add_concept(game_list, owner, concept)

    resp = client.get(_url(game_list), {'sort': 'name'})
    body = resp.content.decode()

    assert resp.context['can_reorder'] is False
    assert 'data-gl-grab' not in body
    # Alpha is second in the list and first alphabetically, so its numeral must still read 2.
    assert re.findall(r'<span class="gl-rank"[^>]*>(\d+)</span>', body) == ['2', '1']


def test_only_the_owner_gets_handles(client):
    owner = _staff(client, psn='owner')
    game_list = _ranked(owner, 2)
    client.logout()
    _staff(client, psn='somebodyelse')

    resp = client.get(_url(game_list))
    body = resp.content.decode()

    assert resp.context['is_ranked'] is True, 'a visitor should still see it AS a ranked list'
    assert 'gl-rank' in body, '...numerals included'
    assert resp.context['can_reorder'] is False
    assert 'data-gl-grab' not in body
    assert 'data-gl-reorder' not in body


def test_the_handles_and_the_endpoint_are_there_when_they_can_work(client):
    """The positive case, so every refusal above is a real narrowing rather than a feature that never
    renders at all."""
    owner = _staff(client)
    game_list = _ranked(owner, 3)

    resp = client.get(_url(game_list))
    body = resp.content.decode()

    assert resp.context['can_reorder'] is True
    assert body.count('data-gl-grab') == 3, 'one grip per row'
    assert f'data-reorder-url="/community/lists/{game_list.id}/reorder/"' in body
    assert body.count('data-item-id=') == 3, 'the drag payload needs an id per row'


def test_the_rank_sort_is_reachable_explicitly(client):
    """A hunter who sorts A-Z to find something needs a way back to the order they built.

    THIS TEST USED TO CLAIM MORE THAN IT CHECKED. It made a request at `?sort=name` first and called
    itself a round trip -- but sort is read from `request.GET` and stored nowhere, so the first GET
    could be deleted with no effect. The real round-trip failure was on the CLIENT (the drag wiring
    read htmx's pre-settle attributes and died on exactly this navigation), and this test's framing
    implied coverage of it. That is pinned below instead, where it lives.
    """
    owner = _staff(client)
    game_list = _ranked(owner, 2)

    resp = client.get(_url(game_list), {'sort': 'rank'})

    assert resp.context['sort'] == 'rank'
    assert resp.context['can_reorder'] is True


def test_the_drag_is_wired_after_settle_not_after_swap(client):
    """The sort swap replaces `#gl-items`, and htmx stabilises attributes on id'd elements: for ~20ms
    after `htmx:afterSwap` the new grid still wears the OLD one's attributes.

    So wiring on swap breaks both directions. A-Z -> List order reads `data-gl-reorder` as absent and
    returns early, leaving every grip inert until a reload. List order -> A-Z reads it as present,
    wires a grid that must not be draggable, and marks it in the WeakSet so the settle pass skips it.

    Asserted against the source because no server test can see it, and because this file's own header
    already documents the same mechanism for `pp-reveal`.
    """
    js = _decommented(_read('static/js/list-detail.js'))

    assert "addEventListener('htmx:afterSettle'" in js, 'the drag must re-wire on settle'
    # And NOT from the swap handler, which is what made the second direction worse than the first.
    # The named function moved (`wireReorder` -> `syncPositioning`) when reordering became a mode,
    # and this assertion kept passing against a name that no longer existed anywhere in the file --
    # the same way a test goes quietly vacuous when the thing it guards is renamed around it.
    swap_handler = js[js.index('function onAfterSwap('):js.index('function onAfterSettle(')]
    for wirer in ('syncPositioning()', 'attachDrag(', 'enterPositioning()'):
        assert wirer not in swap_handler, \
            f'{wirer} on swap reads stale attributes and poisons the wiring guard'

    # ...and the settle handler must actually DO the wiring. Asserting only that a listener for the
    # event name exists left `onAfterSettle` free to be emptied to `return;` -- which kills the mode
    # across every sort swap and every chrome refresh -- with this test still green.
    settle = js[js.index('function onAfterSettle('):js.index('function wirePositioning(')]
    assert 'syncPositioning()' in settle and 'wirePositioning()' in settle, \
        'the settle handler is registered but does no wiring'


def test_the_edit_form_sends_only_what_changed(client):
    """`update_list` runs the restriction gate whenever `name` or `description` is present, so a form
    that always posts both makes every type switch a gated write -- and the service's documented
    "a restricted hunter can still switch type" becomes unreachable through the only UI that can send
    the field."""
    js = _decommented(_read('static/js/list-detail.js'))

    # The ACTUAL comparisons, not a fuzzy "is there an `if` somewhere above". The first draft of this
    # test looked for `'if (' in` the preceding 200 characters, which almost any code satisfies -- it
    # would have passed against the unconditional version it exists to catch.
    assert "nameField.value.trim() !== previousName" in js, \
        'the name is posted without comparing it to what the server rendered'
    assert "descField.value.trim() !== previousDesc" in js, \
        'the description is posted without comparing it to what the server rendered'
    # And the type switch must be able to travel ALONE, which is the whole point.
    assert "if (typeChanged) { body.append('list_type'" in js


def test_clicking_a_card_picks_it_up_so_the_arrow_keys_have_a_target(client):
    """THE ARROW KEYS DID NOT WORK, and the hint said they did.

    They were bound to the GRID and only fired while a grip had focus -- which meant tabbing to a
    26px control nobody had a reason to suspect. So the instruction described a key that, as far as
    anyone could tell, did nothing.

    Clicking a card now picks it up, and the keys follow the picked card. That also gives the click a
    job: suppressing the card's navigation was necessary once the whole card became the drag surface,
    but it left a click meaning nothing, and a card that visibly ignores you reads as broken.
    """
    js = _decommented(_read('static/js/list-detail.js'))

    click = js[js.index('function onCardClick('):js.index('function togglePicked(')]
    assert 'e.preventDefault()' in click, 'the card must not navigate while arranging'
    assert 'togglePicked(row)' in click, 'a click must pick the card up'

    # On the DOCUMENT, or it only fires while something inside the grid has focus -- which is the
    # bug. And bound/unbound with the mode, or it outlives it.
    attach = js[js.index('function attachDrag('):js.index('function detachDrag(')]
    assert "document.addEventListener('keydown', onPositionKey)" in attach
    detach = js[js.index('function detachDrag('):js.index('function onCardClick(')]
    assert "document.removeEventListener('keydown', onPositionKey)" in detach
    assert 'dropPicked(true)' in detach, 'a pick-up must not survive the mode'

    # The same click drops it, and Escape does too: never make somebody hunt for the way out of a
    # selection they made by accident.
    toggle = js[js.index('function togglePicked('):js.index('function dropPicked(')]
    assert 'if (pickedRow === row) { dropPicked(); return; }' in toggle

    key = js[js.index('function onPositionKey('):js.index('function itemIdsIn(')]
    assert "e.key === 'Escape'" in key


def test_the_arrow_keys_stay_out_of_the_editor_text_fields(client):
    """The identity editor is OPEN whenever this mode is -- that is how you reach it -- so a document
    listener would move a card every time somebody moved the caret through the list's name."""
    js = _decommented(_read('static/js/list-detail.js'))

    key = js[js.index('function onPositionKey('):js.index('function itemIdsIn(')]
    assert 'isTyping(e.target)' in key

    typing = js[js.index('function isTyping('):js.index('function onPositionKey(')]
    for field in ("'INPUT'", "'TEXTAREA'", "'SELECT'", 'isContentEditable'):
        assert field in typing, f'{field} is not treated as typing'


def test_a_picked_card_is_unmistakable_and_the_bar_shouts(client):
    """Two visual jobs. The picked card has to stand out among up to 200 cards that are ALL already
    lifted, so it is a ring rather than more elevation -- a second, louder shadow would read as "more
    of the same" rather than "this one".

    And the bar is the only thing on the page that says which mode you are in. Its first active state
    was a 14% tint the owner could look straight past.
    """
    css = _read('static/css/components/gamelists.css')

    picked = css[css.index('.gl-item.is-picked .pp-gcard {'):]
    # 3px, NOT the 2px that `.pp-gcard:focus-visible` already draws -- identical rings meant a
    # keyboard user tabbing the grid saw the "held" mark on every card they passed.
    assert 'outline: 3px solid var(--pp-primary)' in picked[:500]
    # `outline`, not `border`: a border changes the box and shifts the grid as cards are picked up.
    assert 'border-width' not in picked[:400]

    on = css[css.index('.gl-positions.is-on {'):]
    assert 'border-color: var(--pp-primary);' in on[:300], 'the active bar needs a full-weight border'
    assert 'color-mix(in oklab, var(--pp-primary) 22%' in on[:400], 'the active wash is still timid'


def test_the_grip_is_operable_from_a_keyboard(client):
    """It is a real button in the tab order announced as "Reorder <game>". SortableJS runs
    `forceFallback`, which is pointer-only, so without an explicit handler the control promises an
    action it cannot perform -- on a long list, once per row."""
    js = _decommented(_read('static/js/list-detail.js'))

    # The MAPPING, not the mere presence of the key names. Swapping the two lists inverts every
    # keyboard move on the page and passed every test in the suite.
    assert "GRAB_EARLIER = ['ArrowUp', 'ArrowLeft']" in js
    assert "GRAB_LATER = ['ArrowDown', 'ArrowRight']" in js
    # BOUND, not merely defined. An earlier version asserted the function existed and that its body
    # called `saveOrder` -- both still true with the `addEventListener` line deleted, so the mutation
    # that unbinds it entirely walked straight through.
    assert "document.addEventListener('keydown', onPositionKey)" in js, \
        'the key handler is defined but never attached'
    # The GRIP path still works alongside the picked-card one, and a focused grip WINS, because it is
    # the element the hunter is actually touching.
    handler = js[js.index('function onPositionKey('):js.index('function itemIdsIn(')]
    assert "closest('[data-gl-grab]')" in handler, 'the grip no longer drives the keys'
    # It must actually SAVE, not merely move the node in the DOM.
    assert 'saveOrder(' in handler, 'a keyboard move that never persists is worse than none'


def test_reordering_is_a_mode_you_have_to_enter(client):
    """Handles on by default made dragging something you could do by ACCIDENT. The capability
    (`data-gl-reorder`) and the intent (the mode) are now separate: the server still says where
    reordering is possible, and the hunter says when."""
    owner = _staff(client)
    game_list = _ranked(owner, 3)

    body = client.get(_url(game_list)).content.decode()

    # The toggle is offered...
    assert 'data-gl-positions-toggle' in body
    assert 'Edit list positions' in body
    # ...next to the grid it acts on, not buried in the edit panel above it. Asserted by position,
    # because "it is on the page somewhere" is what let it ship somewhere nobody could find it.
    assert body.index('data-gl-positions') > body.index('data-gl-identity-edit'), \
        'the bar belongs below the editor, beside the list'
    assert body.index('data-gl-positions') < body.index('id="gl-items-panel"'), \
        'the bar belongs directly above the grid'
    # The save model is stated once the mode is on; the JS owns that copy because it swaps with state.
    js = _decommented(_read('static/js/list-detail.js'))
    assert 'Moves save as you make them.' in js, 'the save model has to be stated, not discovered'
    # ...and the grips are rendered but inert until the mode is on, which is CSS, not markup.
    css = _read('static/css/components/gamelists.css')
    assert '#gl-items-panel:not([data-positioning]) .gl-item__grab { display: none; }' in css
    # `display: none` and not `opacity: 0` -- an invisible button is still a tab stop that announces
    # itself, which is the bug this is avoiding rather than a detail of how it looks.
    #
    # Written as a slice, both anchors landed on the SAME LINE of the stylesheet and the end anchor
    # was searched from 0, so this examined a 40-character fragment of one selector and could not
    # fail under any edit. The whole-file negative below says the same thing and can.
    assert '#gl-items-panel:not([data-positioning]) .gl-item__grab { opacity: 0' not in css


def test_the_mode_is_not_offered_where_reordering_is_impossible(client):
    """The toggle lives in the page header, which the sort swap does not re-render, so it must not be
    rendered for a list that cannot be reordered at all."""
    owner = _staff(client)

    plain = _list(owner, 2)
    assert 'data-gl-positions-toggle' not in client.get(_url(plain)).content.decode()

    ranked = _ranked(owner, 2)
    sorted_away = client.get(_url(ranked), {'sort': 'name'}).content.decode()
    assert 'data-gl-positions-toggle' not in sorted_away


def test_the_mode_follows_the_grid_across_swaps_and_ends_with_the_editor(client):
    """Three ways the mode could outlive its own preconditions, all pinned at the source because none
    is reachable from a server test: a sort that removes the capability, a swap that replaces the grid
    the drag manager is bound to, and closing the panel the mode was entered from."""
    js = _decommented(_read('static/js/list-detail.js'))

    sync = js[js.index('function syncPositioning() {'):js.index('function syncPositionsVisibility() {')]
    assert 'attachDrag(grids)' in sync, 'a replaced grid must be re-attached while the mode is on'
    assert 'syncPositionsVisibility()' in sync, 'the bar must follow the grid below it'

    close_body = js[js.index('function close() {'):js.index('function reset() {')]
    assert 'editorOpen = false' in close_body and 'syncPositionsVisibility()' in close_body, \
        'closing the editor must end the mode it started'


def test_the_whole_card_drags_and_does_not_navigate_while_arranging(client):
    """The grip was a 26px target on a 166px card, and people reach for the thing itself.

    Dropping `handleSelector` makes the card the drag surface, which is only safe because the card's
    navigation is suppressed for the duration -- a click the browser did not classify as a drag would
    otherwise leave the page in the middle of rearranging it. Both halves, or this is a regression:
    the risk the grip was avoiding is real.
    """
    js = _decommented(_read('static/js/list-detail.js'))

    attach = js[js.index('function attachDrag('):js.index('function detachDrag(')]
    assert 'handleSelector' not in attach, 'the drag is still confined to the grip'
    assert "addEventListener('click', onCardClick)" in attach

    # Bounded to `onCardClick` ALONE. Ending at `syncPositioning` swept in `togglePicked` and
    # `dropPicked` too, so moving the mode guard out of the click handler and into one of those --
    # which breaks it, leaving every card unclickable outside the mode -- kept this green.
    guard = js[js.index('function onCardClick('):js.index('function togglePicked(')]
    assert 'if (!positioning' in guard, \
        'a click guard that outlives the mode makes the list unclickable'
    # ...and a post-drop synthetic click must not toggle the pick. Sortable eats that click on every
    # platform except Chrome for Android, where it skips registering the listener entirely.
    assert 'justDragged' in guard, 'a drop will re-pick the card it just dropped on Chrome Android'
    assert 'e.preventDefault()' in guard
    # NOT stopPropagation: the grip's own click, and any control added to a card later, still has to
    # reach its handler.
    assert 'stopPropagation' not in guard

    # The grip survives, because it is the keyboard path and the per-card signal.
    assert 'data-gl-grab' in client.get(_url(_ranked(_staff(client), 2))).content.decode()


def test_touch_needs_a_deliberate_hold_before_a_card_moves(client):
    """A finger resting on a card is how you begin a SCROLL. Without a hold, dragging and scrolling
    are the same gesture and the grid picks up a card every time somebody tries to scroll past it."""
    js = _decommented(_read('static/js/list-detail.js'))

    attach = js[js.index('function attachDrag('):js.index('function detachDrag(')]
    # The VALUE, not just the key. `DragReorderManager` does `if (this.delay)`, so `delay: 0`
    # silently disables the hold, `delayOnTouchOnly` AND `touchStartThreshold` in one edit -- and a
    # membership check on `'delay:'` passes through all three.
    assert re.search(r'delay:\s*[1-9]\d{2}', attach), 'the touch hold is zero or missing'
    assert 'delayOnTouchOnly: true' in attach, 'a mouse drag must stay immediate'

    # The manager has to honour it, and has to pair it with a movement budget -- without one, a
    # finger that drifts during the hold still arms the drag and the scroll is lost anyway.
    utils = _decommented(_read('static/js/utils.js'))
    assert 'sortableConfig.delay = this.delay' in utils
    assert 'sortableConfig.delayOnTouchOnly' in utils
    assert 'sortableConfig.touchStartThreshold' in utils


def test_arranging_quiets_the_card_hover_and_shows_the_cards_are_loose(client):
    """Hover lifts the art, glows the border and recolours the title -- an invitation to click, which
    is wrong while dragging, and it fires on every card the pointer crosses mid-drag.

    The counterpart says "these are loose" about every card at once, which is what lets the gesture
    go unexplained. It was a wobble and is now DEPTH: the grid recesses into a tray and the cards
    lift off it. Same message, held rather than repeated, and identical for a reader with reduced
    motion turned on.
    """
    css = _read('static/css/components/gamelists.css')

    # BOUNDED at both ends. Slicing to end-of-file swept in every later rule in the stylesheet,
    # including an unrelated publish animation, so an assertion about this section was really an
    # assertion about the rest of the file.
    # The BANNER, not the first mention. `POSITION-EDITING MODE` also appears ~150 lines earlier in
    # the grip block's comment, so this slice silently covered the grips and the whole position bar
    # -- the exact over-broad slice the note below claims to have fixed.
    mode_start = css.index('\u2550\u2550 POSITION-EDITING MODE')
    mode = css[mode_start:css.index('/* SortableJS states.', mode_start)]
    for suppressed in ('.pp-gcard:hover .pp-gcard__art { transform: none; }',
                       '.pp-gcard:active { transform: none; }'):
        assert suppressed in mode, f'hover is still live while arranging: {suppressed}'

    # THE TELL IS DEPTH, NOT MOTION. This asserted a continuous wobble until the owner cut it: a grid
    # of up to 200 cards moving forever is a lot to impose to convey one bit of state, and it keeps
    # asking for attention long after it has been understood.
    assert 'glLoose' not in css, 'the wobble was removed; nothing should reintroduce it'
    # Against the CODE, not the prose. The first version of this line matched the comment that
    # explains why there is no animation -- a comment is a claim, and here it made a true assertion
    # fail. The same slip in the other direction is how a guard passes while checking nothing.
    assert 'animation:' not in _decommented(mode), 'the mode signal must not be an animation'

    # The grid becomes a tray and the cards lift off it. BOTH halves: the recess is what the cards
    # read as loose ON, and the elevation is what makes them read as pick-up-able rather than merely
    # selected -- an accent border on its own says "selected", which is a different idea.
    # `[data-gl-arrange]` and NOT `#gl-items`: that id is rendered only by a FLAT list, so scoping
    # the tray to it left every SECTIONED list with cards lifting off nothing at all -- half a
    # two-part signal, on exactly the lists sections introduced.
    assert '#gl-items-panel[data-positioning] [data-gl-arrange] {' in mode
    assert '#gl-items {' not in mode, 'the tray still keys on the flat-list id'
    assert 'inset 0 1px 3px' in mode, 'the tray has no recess, so nothing is raised relative to it'
    assert '#gl-items-panel[data-positioning] .gl-item .pp-gcard {' in mode
    lifted = mode[mode.index('#gl-items-panel[data-positioning] .gl-item .pp-gcard {'):]
    assert 'box-shadow' in lifted[:400], 'the cards carry no elevation, only a border'

    # It arrives as a transition, so there is one moment of change and no loop -- and the END state is
    # identical under reduced motion, or the signal would be a feature only some readers receive.
    assert 'transition: background 0.2s ease, box-shadow 0.2s ease' in mode
    # `padding` must NOT be in that list: it is a layout property, and easing it over a grid of up to
    # 200 cards reflows the document every frame for 200ms.
    assert 'padding 0.2s' not in mode, 'the tray animates a layout property across the whole grid'
    # INSIDE the media query. A bare `#gl-items { transition: none; }` would kill the transition for
    # everyone -- the opposite of the intent -- and the membership test alone could not tell.
    reduced_start = mode.index('@media (prefers-reduced-motion: reduce)')
    reduced = mode[reduced_start:mode.index('\n}', reduced_start)]
    assert '[data-gl-arrange] { transition: none; }' in reduced


def test_saving_a_type_change_refreshes_in_place_instead_of_reloading(client):
    """A type switch changes three server-rendered things -- the cards, which sorts exist, and whether
    the position bar exists at all -- and the last two live OUTSIDE the swapped panel. That is why it
    used to reload the whole page, which lost the hunter's place and made them re-open the editor to
    reach the positions they had just switched the list over to use.

    One request carries all three now: the grid as the main swap, the other two out-of-band.
    """
    js = _decommented(_read('static/js/list-detail.js'))

    # Scoped to the SAVE path. A blanket search was wrong: `onAfterSwap` reloads on purpose when the
    # sort toolbar crosses the exists/does-not-exist boundary, which is a different, rarer case.
    save_handler = js[js.index("form.addEventListener('submit'"):js.index('function wireVisibility(')]
    assert 'window.location.reload()' not in save_handler, 'a save must not reload the page'
    assert 'refreshAfterTypeChange()' in save_handler
    # Code landmarks at BOTH ends, searched FORWARD. Comments are gone (`_decommented`) and several
    # function names in this file are defined above the one being sliced, so a bare `.index()` for
    # either end silently returns the wrong offset or raises.
    refresh_start = js.index('function refreshAfterTypeChange() {')
    refresh = js[refresh_start:js.index('var TOGGLES', refresh_start)]
    assert "'?chrome=1'" in refresh, 'the refresh must ask for the chrome'
    assert "target: '#gl-items-panel'" in refresh
    # No `sort` carried across: the new type has its own default, and a freshly-ranked list that
    # opened on A-Z would hide the very ordering the switch was made to use.
    assert 'sort' not in refresh.split('htmx.ajax')[1].split(')')[0]
    assert 'replaceState' in refresh, 'the address bar must not keep a sort that no longer applies'


def test_the_editor_re_anchors_the_stored_type_after_a_save(client):
    """The client-side half of the two-switches bug (see `test_switching_type_twice_in_one_session`).

    `typeChanged` is measured against `data-list-type` on the identity block, and the in-place refresh
    does not re-render that block -- so it has to be updated from the response, or the SECOND switch
    of a session computes "nothing changed" and is dropped before it is ever sent.

    Re-anchored from the RESPONSE, not from the radio: what matters is what the server stored.
    """
    js = _decommented(_read('static/js/list-detail.js'))

    handler_start = js.index("form.addEventListener('submit'")
    handler = js[handler_start:js.index('function wireVisibility(', handler_start)]
    assert 'root.dataset.listType = data.list_type' in handler, \
        'nothing re-anchors the stored type, so a second switch is silently dropped'

    # And the attribute it re-anchors is really the one the page renders.
    owner = _staff(client)
    ranked = _ranked(owner, 2)
    assert f'data-list-type="{ranked.list_type}"' in client.get(_url(ranked)).content.decode()


def test_the_type_change_refresh_carries_the_chrome_the_swap_cannot_reach(client):
    """The out-of-band half, asserted against the rendered response rather than the template source.

    Both fragments must be addressed to ids that EXIST on the page, or htmx drops them silently: the
    sort `<select>` (not its form -- replacing the form would unbind the auto-submit that
    `browse-filters.js` attaches to it) and a position-bar SLOT that is present even when the bar is
    not, since going Collection -> Ranked has no bar to match against.
    """
    owner = _staff(client)
    ranked = _ranked(owner, 3)

    page = client.get(_url(ranked)).content.decode()
    assert 'id="gl-sort-select"' in page
    assert 'id="gl-positions-slot"' in page

    fragment = client.get(_url(ranked), {'chrome': '1'},
                          HTTP_X_REQUESTED_WITH='XMLHttpRequest').content.decode()
    # PER TAG, not "the attribute appears somewhere". A single `hx-swap-oob in fragment` passed with
    # the flag stripped from the select, because the positions slot still carried one -- one
    # assertion standing in for two things, which is how half a feature ships green.
    for tag_id in ('gl-sort-select', 'gl-positions-slot'):
        opening = fragment[fragment.index(f'id="{tag_id}"'):]
        opening = opening[:opening.index('>')]
        assert 'hx-swap-oob="true"' in opening, f'{tag_id} is sent without its out-of-band flag'
    # The FORM must not travel with it.
    assert 'data-browse-form' not in fragment, \
        'replacing the form would unbind the sort auto-submit until the next full load'

    # And a Collection sends an EMPTY slot, which is how the bar disappears on save.
    plain = _list(owner, 3)
    collection_fragment = client.get(_url(plain), {'chrome': '1'},
                                     HTTP_X_REQUESTED_WITH='XMLHttpRequest').content.decode()
    assert 'id="gl-positions-slot"' in collection_fragment
    assert 'data-gl-positions-toggle' not in collection_fragment


def test_the_ordinary_refresh_does_not_resend_the_chrome(client):
    """Add, remove and sort all re-render the grid and change none of it. Re-sending the `<select>`
    on a sort swap would replace the control the hunter just used, mid-interaction."""
    owner = _staff(client)
    ranked = _ranked(owner, 3)

    fragment = client.get(_url(ranked), HTTP_X_REQUESTED_WITH='XMLHttpRequest').content.decode()

    assert 'hx-swap-oob' not in fragment
    assert 'id="gl-sort-select"' not in fragment


def test_the_bar_starts_hidden_and_is_never_offered_to_a_visitor(client):
    """Hidden at rest because it is revealed by opening the editor -- and absent entirely for anyone
    who could not act on it, rather than merely hidden from them."""
    owner = _staff(client, psn='owner')
    ranked = _ranked(owner, 3)

    body = client.get(_url(ranked)).content.decode()
    bar = body[body.index('data-gl-positions'):]
    assert bar[:120].find('hidden') != -1, 'the bar must start hidden; the editor reveals it'

    client.logout()
    _staff(client, psn='visitor')
    assert 'data-gl-positions' not in client.get(_url(ranked)).content.decode()


def test_the_save_indicator_reports_both_outcomes(client):
    """"Saving" with no terminal state is worse than silence: it never tells you whether the move
    landed. Both ends are written, and the failure path is distinguishable."""
    js = _decommented(_read('static/js/list-detail.js'))

    # Bounded by CODE landmarks. A comment cannot be an anchor here: `_decommented` has already
    # removed every one of them, so slicing to a comment raises rather than matching.
    save = js[js.index('function saveOrder('):js.index('function renumber(')]
    assert "setPositionsStatus('Saving…')" in save
    assert "setPositionsStatus('Saved')" in save
    assert "setPositionsStatus('Not saved')" in save

    # The visible pill is kept OUT of the accessibility tree, because the spoken half goes through
    # `announce()` once per action -- a live region here would narrate "Saving" then "Saved" on top
    # of every move.
    # The bar lives in its own partial now, because a type change swaps it out-of-band.
    bar = _read('templates/gamelists/partials/detail_positions.html')
    status_tag = bar[bar.index('data-gl-positions-status'):]
    assert 'aria-hidden="true"' in status_tag[:200]


def test_cancelling_the_editor_restores_the_type_too(client):
    """Pick Ranked, cancel, reopen to fix a typo, save -- and the abandoned radio was still checked,
    so a rename silently carried the type change with it and reloaded the page underneath them.

    `reset()` restores every other field from the server-rendered DOM; this one was reading whatever
    was left over from the cancelled edit.
    """
    js = _decommented(_read('static/js/list-detail.js'))

    # Sliced FORWARD from `reset()`, because the end-marker also appears inside `close()` higher up
    # -- searching from 0 found that one, produced an empty slice, and made this test fail for a
    # reason that had nothing to do with what it checks. A mutation run reported it as "killed" on
    # that basis, which is a false pass hiding inside a false failure.
    #
    # The marker used to be `var opener = root.querySelector(`, which stopped existing when the
    # three opener lookups were routed through one document-scoped `editOpener()` helper -- the
    # button had moved out of `root` and was silently never bound. Borrowing a neighbouring line as
    # a boundary is fragile exactly like this; kept because the alternative is brace-matching, but
    # now anchored on the line this test is actually adjacent to.
    start = js.index('function reset() {')
    reset_body = js[start:js.index('var opener = editOpener();', start)]
    assert "form.querySelector('[name=\"list_type\"][value=\"'" in reset_body, \
        'reset() leaves an abandoned type selection checked'
    assert 'current.checked = true' in reset_body

    # And the value it restores TO has to be on the page, or reset() silently checks nothing.
    owner = _staff(client)
    game_list = _ranked(owner, 1)
    body = client.get(_url(game_list)).content.decode()
    assert f'data-list-type="{game_list.list_type}"' in body


def test_a_ranked_entry_announces_its_rank(client):
    """The visible numeral is `aria-hidden`, and an `aria-label` on the link REPLACES the name
    computed from its descendants -- so a rank placed in a hidden span inside the card was
    unreachable, on the one list type whose whole content is the ordering."""
    owner = _staff(client)
    game_list = _ranked(owner)
    for title in ('Zulu', 'Alpha'):
        concept = ConceptFactory(unified_title=title)
        GameFactory(concept=concept, title_platform=['PS5'])
        svc.add_concept(game_list, owner, concept)

    body = client.get(_url(game_list)).content.decode()

    assert 'aria-label="Number 1: Zulu"' in body
    assert 'aria-label="Number 2: Alpha"' in body
    # A Collection says nothing about numbers, because it has none.
    plain = _list(owner, 1)
    assert 'Number 1:' not in client.get(_url(plain)).content.decode()


def test_a_collection_cannot_reach_the_rank_sort_by_url(client):
    """`?sort=rank` on an unordered list must fall back rather than answering 200 with insertion
    order dressed up as a curated sequence."""
    owner = _staff(client)
    game_list = _list(owner, 2)

    resp = client.get(_url(game_list), {'sort': 'rank'})

    assert resp.context['sort'] == 'name'
    assert resp.context['can_reorder'] is False


def test_a_junk_sort_falls_back_to_the_default(client):
    """An unrecognised `?sort=` must land somewhere deterministic rather than dropping to no
    ordering, which would let the same list render differently on two loads."""
    owner = _staff(client)
    game_list = _list(owner, 2)

    resp = client.get(_url(game_list), {'sort': 'nonsense'})

    assert resp.context['sort'] == 'name'
    # The retired value degrades the same way, so an old bookmark still lands on a real page.
    assert client.get(_url(game_list), {'sort': 'position'}).context['sort'] == 'name'


# ── the sort swap ────────────────────────────────────────────────────────────────────────────────

def test_sorting_swaps_the_items_and_nothing_else(client):
    owner = _staff(client)
    game_list = _list(owner, 2)

    body = client.get(_url(game_list), {'sort': 'name'},
                      HTTP_HX_REQUEST='true').content.decode()

    # `id="gl-items"` exactly: the bare string `gl-items` is a substring of the full page's
    # `id="gl-items-panel"` wrapper, so it was answered by the page this test exists to exclude.
    assert 'id="gl-items"' in body
    assert '<!doctype html' not in body.lower(), 'the sort swap returned the whole page'
    assert '<nav' not in body.lower()


def test_the_swap_targets_a_stable_wrapper(client):
    """`innerHTML` into a wrapper the swap never destroys -- the pattern that works here. The
    outerHTML variant duplicated the page on My Lists."""
    from pathlib import Path

    tpl = (Path(__file__).resolve().parents[2]
           / 'templates' / 'gamelists' / 'detail.html').read_text(encoding='utf-8')

    assert 'hx-target="#gl-items-panel"' in tpl
    assert 'hx-swap="innerHTML"' in tpl
    assert 'id="gl-items-panel"' in tpl


def test_the_swapped_items_carry_the_reveal_class_and_something_reveals_them(client):
    """Baking `pp-reveal` with no observer is what left the My Lists panel blank -- and then did
    exactly the same thing HERE, because the server half of the pattern was copied to this page and
    the client half was not. Sorting a list rendered every tile at `opacity: 0`.

    The earlier version of this test passed throughout that bug. It asserted
    `'PlatPursuit.staggerReveal(' in gamelists.js` -- true, because that file reveals MY LISTS' grid,
    `#my-lists-grid`. Nothing tied the observer to THIS page's grid or to the script THIS page loads,
    so the assertion was answered by a different page's code.

    So bind all three together: the script the template actually loads, that script observing this
    page's grid id, and the grid carrying that id.
    """
    owner = _staff(client)
    game_list = _list(owner, 2)

    swapped = client.get(_url(game_list), HTTP_HX_REQUEST='true').content.decode()
    full = client.get(_url(game_list)).content.decode()

    assert 'pp-reveal' in swapped
    assert 'pp-reveal' not in full
    # The id the observer has to find, asserted against the rendered page rather than assumed.
    assert 'id="gl-items"' in swapped

    template = _read('templates/gamelists/detail.html')
    loaded = re.findall(r"js/([a-z0-9-]+\.js)", template)
    assert loaded, 'the detail template loads no JS at all'

    # Whichever script this page loads, ONE of them must reveal this page's grid.
    sources = {name: _read(f'static/js/{name}') for name in loaded}
    revealing = [
        name for name, src in sources.items()
        if 'staggerReveal(' in _decommented(src) and "'gl-items'" in _decommented(src)
    ]
    assert revealing, (
        f'no script loaded by detail.html reveals #gl-items; loaded={sorted(sources)}'
    )


# ── indexing ─────────────────────────────────────────────────────────────────────────────────────

def test_a_private_list_is_never_indexable(client):
    owner = _staff(client)
    private = _list(owner, 1, public=False)

    assert 'noindex' in client.get(_url(private)).content.decode()


def test_a_sorted_public_list_is_not_indexed_as_a_duplicate(client):
    author = ProfileFactory(is_linked=True, psn_username='author')
    game_list = _list(author, 2)
    _staff(client, psn='reader')

    assert 'noindex' in client.get(_url(game_list), {'sort': 'name'}).content.decode()


def test_a_plain_public_list_is_indexable():
    """The negative control the two noindex tests lacked. Both asserted `noindex in body`, so
    hardcoding the block to `noindex, nofollow` passed both and silently de-indexed every public
    list on the site."""
    from django.test import Client

    author = ProfileFactory(is_linked=True, psn_username='author')
    game_list = _list(author, 1)

    client = Client()
    _staff(client, psn='reader')

    assert 'noindex' not in client.get(_url(game_list)).content.decode()


def test_an_unlinked_viewer_is_not_offered_actions_the_endpoints_would_refuse(client):
    """The page computed `can_act` from "has a profile" while the endpoints require `is_linked` --
    so an unlinked viewer saw both buttons and their JSON fetch got an HTML redirect back."""
    author = ProfileFactory(is_linked=True, psn_username='author')
    game_list = _list(author, 1)

    user = UserFactory()
    user.role = 'admin'
    user.save()
    ProfileFactory(user=user, is_linked=False, psn_username='unlinked')
    client.force_login(user)

    body = client.get(_url(game_list)).content.decode()

    assert 'data-gl-like' not in body
    assert 'data-gl-follow' not in body


def test_the_owner_gets_the_adder_and_a_visitor_never_does(client):
    """The adder is an OWNER control, not a social one. `can_act` gates likes and follows; a visitor
    passing that check must still not be handed a way to edit somebody else's list."""
    author = ProfileFactory(is_linked=True, psn_username='author')
    game_list = _list(author, 1)

    _staff(client, psn='reader')
    visitor = client.get(_url(game_list)).content.decode()
    assert 'data-gl-adder' not in visitor
    # `gl-adder__input` used to be asserted here and became VACUOUS when the field moved onto the
    # shared `.pp-bgal__search`: the class stopped existing anywhere, so `not in` was trivially true.
    # These name things the page really renders, so removing the guard would fail them.
    assert 'data-gl-adder-input' not in visitor
    assert '/search/' not in visitor
    # The control it must NOT be confused with: the visitor does still get the social acts.
    assert 'data-gl-like' in visitor

    # AN ORDINARY OWNER, not a staff one. This granted `role = 'admin'` because every gamelists
    # surface used to sit behind `_DevelopmentGate` and an un-elevated request redirected -- which
    # would have made every `not in` assertion below pass against an empty body. The gate is gone,
    # and leaving the elevation in actively weakened the test: it asserts the OWNER's controls
    # render, so running it as an admin would let a regression that showed them only to staff pass.
    client.force_login(author.user)
    owner_resp = client.get(_url(game_list))
    # The status check is what the elevation was really protecting against; it stays.
    assert owner_resp.status_code == 200
    owner_body = owner_resp.content.decode()
    assert 'data-gl-adder' in owner_body
    assert f'/community/lists/{game_list.id}/search/' in owner_body
    assert f'/community/lists/{game_list.id}/add/' in owner_body


def test_every_entry_carries_its_own_remove_endpoint(client):
    """The URL is rendered per row rather than assembled in JS from a base path. Asserted against
    the real item ids, so a route rename fails here instead of silently 404ing in a browser --
    which nearly happened: the route is `list_remove_game`, not the `list_remove_item` its view
    class name suggests."""
    owner = _staff(client)
    game_list = _list(owner, 3)

    body = client.get(_url(game_list)).content.decode()

    item_ids = list(game_list.items.values_list('pk', flat=True))
    assert len(item_ids) == 3
    for item_id in item_ids:
        assert f'/community/lists/{game_list.id}/items/{item_id}/remove/' in body
    assert body.count('data-gl-remove') == 3


def test_a_visitor_gets_no_remove_controls(client):
    author = ProfileFactory(is_linked=True, psn_username='author')
    game_list = _list(author, 2)
    _staff(client, psn='reader')

    body = client.get(_url(game_list)).content.decode()

    assert 'data-gl-remove' not in body
    assert '/remove/' not in body
    # The games themselves are still there -- this is not an empty page passing by accident.
    # `data-gcard`, since the detail games moved onto the shared game card. This is the POSITIVE
    # CONTROL for the two `not in` assertions above -- without it they pass on an empty page -- and
    # it earned its keep by failing the moment the hook changed.
    assert body.count('data-gcard') == 2


def test_the_remove_control_sits_beside_the_tile_and_not_inside_it(client):
    """A <button> nested in an <a> is invalid HTML and swallows the link's own activation, so the
    control lives in a sibling wrapper. Pinned because the fix is invisible in a screenshot."""
    owner = _staff(client)
    game_list = _list(owner, 1)

    body = client.get(_url(game_list)).content.decode()

    assert 'class="gl-item"' in body

    # Scoped to the item block. The first version searched from `body.index('<a href=')`, which finds
    # a NAV link in the chrome hundreds of lines earlier, so the comparison was true no matter where
    # the button sat -- mutation-checked, and it caught nothing. Chrome answering a page assertion is
    # the recurring failure on this branch, so the search starts inside the thing under test.
    region = body[body.index('class="gl-item"'):]
    anchor_close = region.index('</a>')
    remove_at = region.index('data-gl-remove')
    assert anchor_close < remove_at, 'the remove button is inside the tile anchor'


def test_the_social_buttons_carry_their_own_endpoints(client):
    """Same rule as remove: the server owns URL shapes. Before this the buttons carried only
    `data-list-id`, which forces the client to know the path layout."""
    author = ProfileFactory(is_linked=True, psn_username='author')
    game_list = _list(author, 1)
    _staff(client, psn='reader')

    body = client.get(_url(game_list)).content.decode()

    assert f'data-url="/community/lists/{game_list.id}/like/"' in body
    assert f'data-url="/community/lists/{game_list.id}/follow/"' in body


def test_the_count_the_writes_update_is_addressable(client):
    """add/remove return `game_count` and the header has to be able to receive it."""
    owner = _staff(client)
    game_list = _list(owner, 2)

    assert 'data-game-count' in client.get(_url(game_list)).content.decode()


# -- the JS/endpoint contract ---------------------------------------------------------------------

def test_the_list_writes_post_form_data_not_json(client):
    """Django populates `request.POST` for form and multipart bodies and leaves it EMPTY for
    `application/json`. Every one of these endpoints reads `request.POST`, so posting JSON would
    send a body the view cannot see -- `liked` would read as absent, i.e. false, on every press,
    with no error anywhere. `API.post` serializes JSON; `API.postFormData` is the matching half.

    This is a silent-failure class, which is why it is pinned rather than left to a browser pass.
    """
    js = _decommented(_read('static/js/list-detail.js'))

    assert 'API.postFormData(' in js
    assert 'API.post(' not in js, 'API.post sends JSON, which request.POST cannot read'


def test_the_write_endpoints_read_form_encoded_bodies(client):
    """The server half of the contract above, exercised for real rather than asserted from source:
    a form-encoded POST must actually flip the state."""
    author = ProfileFactory(is_linked=True, psn_username='author')
    game_list = _list(author, 1)
    reader = _staff(client, psn='reader')

    resp = client.post(f'/community/lists/{game_list.id}/like/', {'liked': 'true'})

    assert resp.status_code == 200
    assert resp.json()['liked'] is True
    assert game_list.likes.filter(profile=reader).exists()


def test_the_adder_uses_the_shared_search_chrome(client):
    """`data-search-wrap` + `.pp-search-spin` + `.pp-search-clear` is the site's search field, driven
    by `wireSearchField`. The first cut hand-rolled a parallel spinner and clear button. Both halves
    are asserted: the markup contract, and the helper that drives it."""
    owner = _staff(client)
    game_list = _list(owner, 1)

    body = client.get(_url(game_list)).content.decode()
    # SCOPED to the adder. `data-search-clear` is on the navbar's own search on every page, so the
    # third of these three "shared chrome" assertions was answered by the site chrome -- deleting
    # the adder's clear button left it green.
    # The class LIST, which gained `.gl-adder` when the adder was raised to the toolbar's focal
    # point. Anchoring on the bare shared class matched nothing once a second class sat beside it.
    adder = body[body.index('<div class="pp-bgal__search gl-adder"'):body.index('data-gl-adder-results')]
    assert 'data-search-wrap' in adder
    assert 'pp-search-spin' in adder
    assert 'data-search-clear' in adder

    # THE HELPER MOVED, THE CONTRACT DID NOT. The adder's behaviour now lives in `PP.GameAdder`
    # (shared with the prompts adder), so this page delegates to it and `GameAdder` is what calls
    # `wireSearchField`. Both halves are still asserted -- delegation here, the shared chrome there --
    # because asserting only the first would pass over a GameAdder that hand-rolled its own spinner.
    js = _decommented(_read('static/js/list-detail.js'))
    assert 'GameAdder(' in js, 'the adder no longer goes through the shared component'
    assert 'wireSearchField(' not in js, 'the adder is driving the search chrome itself again'

    utils = _decommented(_read('static/js/utils.js'))
    adder_src = utils[utils.index('function GameAdder('):utils.index('window.PlatPursuit.GameAdder')]
    assert 'wireSearchField(' in adder_src


def test_the_detail_page_does_not_load_the_my_lists_script(client):
    """`gamelists.js` is the My Lists page (a create dialog and a scope switcher). This page loaded
    all of it and used none of it."""
    template = _read('templates/gamelists/detail.html')

    assert 'js/list-detail.js' in template
    assert 'js/gamelists.js' not in template


def test_the_adder_min_query_matches_the_endpoint(client):
    """Two copies of a threshold drift. If the endpoint's floor rises, the client must not keep
    firing requests below it that can only ever return nothing.

    Points at `game_search` since the catalogue half of the adder was extracted there for the prompts
    adder to share. That is the single source now: the view no longer carries its own copy, which is
    the whole reason this assertion still has something to be worth pinning.
    """
    from gamelists.services import game_search

    # `minQuery:` since the adder became a shared component configured per caller. The floor is still
    # declared by THIS page -- which is the point: `GameAdder` has a default, and a page that quietly
    # took a different one from the endpoint is exactly the drift being guarded against.
    js = _decommented(_read('static/js/list-detail.js'))
    match = re.search(r'minQuery:\s*(\d+)', js)

    assert match, 'the adder no longer declares a minQuery'
    assert int(match.group(1)) == game_search.MIN_QUERY


# -- guards against the audit's findings ----------------------------------------------------------

def test_no_gl_class_is_used_without_a_rule():
    """An orphaned class name is invisible until somebody looks at the page.

    `.gl-adder__field` was on the search wrapper with ZERO rules anywhere -- source or built. It had
    had a rule, and the refactor onto the shared search chrome deleted it and left the class behind.
    The consequence was not cosmetic: `[data-search-wrap]` supplies `position: relative`, but with no
    `display: block` the wrapper stayed an INLINE span, so the absolutely-positioned icon, spinner
    and clear button anchored to its ~19px line box instead of the 38px input.

    Checked against the BUILT stylesheet, because that is what the browser loads and this project has
    been bitten before by markup that disagreed with the compiled CSS.
    """
    import glob

    root = Path(__file__).resolve().parents[2]
    built = (root / 'static' / 'css' / 'output.css').read_text(encoding='utf-8')

    used = set()
    for path in glob.glob(str(root / 'templates' / 'gamelists' / '**' / '*.html'), recursive=True):
        for attr in re.findall(r'class="([^"]+)"', Path(path).read_text(encoding='utf-8')):
            # TEMPLATE TAGS OUT FIRST. A conditional class is written butted straight against the
            # name before it -- `class="gl-x{% if flag %} gl-x--icon{% endif %}"` -- so splitting
            # the raw attribute on whitespace yields the token `gl-x{%`, which matches no rule and
            # reported a false orphan for markup that was completely fine. Replacing tags with a
            # space recovers the real names. `{{ ... }}` goes too: a class built from a variable
            # cannot be checked against the stylesheet either way.
            attr = re.sub(r'\{%.*?%\}|\{\{.*?\}\}', ' ', attr)
            used.update(c for c in attr.split() if c.startswith('gl-'))

    assert used, 'found no gl-* classes at all -- the scan is broken, not the CSS'
    orphaned = sorted(name for name in used if f'.{name}' not in built)
    assert not orphaned, f'gl-* classes with no rule in the built CSS: {orphaned}'


def test_the_writes_refuse_a_redirected_html_page(client):
    """`fetch` follows redirects, so an expired session arrives as 200 text/html and `API.request`
    hands back a STRING. Read as success it printed the literal toast "Added undefined." and flipped
    the row to a state the server never reached. Django's test client does not follow redirects
    unless asked, so no server test can see this -- it is pinned at the client instead."""
    js = _decommented(_read('static/js/list-detail.js'))

    assert 'function postJson(' in js
    # THE INVARIANT, not a headcount. This asserted `postJson(` appeared exactly 6 times, which meant
    # every new write on this page failed a test about redirect handling and got "fixed" by bumping a
    # number -- the reorder write did exactly that. Worse, the count cannot tell a new GUARDED write
    # from a new UNGUARDED one; both move it by one.
    #
    # What actually matters is that the raw helper is called in exactly one place, inside `postJson`.
    # That is what makes bypassing impossible, it needs no maintenance as writes are added, and it
    # fails for the case the count was blind to.
    assert 'API.postFormData(' in js, 'postJson should still be built on the shared helper'
    assert js.count('API.postFormData(') == 1, 'a write is bypassing the redirect guard'
    # A floor, so the guard cannot pass by there being no writes left to guard.
    assert js.count('postJson(') >= 6, 'writes seem to have disappeared rather than been guarded'


def test_owner_actions_have_somewhere_to_announce(client):
    """The add path toasted and the remove path said nothing at all -- and the toast is not a
    fallback, because `#toast-container` carries no aria-live, so ToastManager is never announced."""
    owner = _staff(client)
    game_list = _list(owner, 1)

    owner_body = client.get(_url(game_list)).content.decode()
    # The PAIR, on one tag. `aria-live="polite"` renders five times on this page -- both character
    # counters, the adder's status line, and the navbar's -- so asserting it anywhere in the body
    # was satisfied by any of them, and stripping it from `data-gl-status` left this green.
    assert re.search(r'<p[^>]*aria-live="polite"[^>]*data-gl-status', owner_body), (
        'the owner status region does not announce'
    )

    # RENDERED FOR A NON-OWNER TOO, which is the opposite of what this asserted before.
    #
    # The old line read "Not rendered for someone who cannot act" and pinned a real hole. Reporting
    # is an action available precisely to somebody who is NOT the owner -- `can_act` is literally
    # "not is_owner" -- so gating the only live region on `is_owner` made the two populations
    # disjoint: every announcement the report dialog made, success and refusals alike, wrote into
    # an element that was never on the page. A blind reporter pressed Send and heard nothing, with
    # no fallback, because the toast carries no `aria-live` either.
    author = ProfileFactory(is_linked=True, psn_username='author')
    theirs = _list(author, 1)
    _staff(client, psn='reader')
    reader_body = client.get(_url(theirs)).content.decode()

    assert re.search(r'<p[^>]*aria-live="polite"[^>]*data-gl-status', reader_body), (
        'a non-owner has no live region, so the report dialog announces into nothing'
    )
    # And the thing that needs it is actually on their page, so the assertion above is about the
    # pairing rather than about an empty element nobody writes to.
    assert 'data-gl-report-form' in reader_body


def test_the_adder_lives_in_the_toolbar_card_and_uses_the_shared_field(client):
    """One control surface, not two. The adder was a hand-rolled card sitting directly beneath the
    shared `.pp-toolbar-card`, which put two visual languages back to back and read as bolted-on.

    Also pins the shared FIELD (`.pp-bgal__search`, the class browse.html uses) over the private one
    that had drifted from it on padding, radius, icon offset, font-size and background.
    """
    owner = _staff(client)
    game_list = _list(owner, 2)

    body = client.get(_url(game_list)).content.decode()

    # CONTAINMENT, not document order. `bar_end` was derived from the adder's own index, so the
    # second assertion could not fail arithmetically; the first only proved the adder came after the
    # card, so moving it into the page footer passed.
    card = body[body.index('pp-toolbar-card'):body.index('id="gl-items-panel"')]
    assert 'data-gl-adder' in card, 'the adder is not inside the toolbar card'

    assert 'pp-bgal__search' in body, 'the adder is not using the shared search field'
    # The shared chrome, all three pieces, plus the "/" hint the other browse toolbars carry.
    assert 'pp-search-spin' in body
    assert 'data-search-clear' in body
    assert 'pp-search-kbd' in body
    assert 'data-page-search' in body


def test_the_adder_is_outside_the_sort_form(client):
    """Load-bearing. `browse-filters.js` wires every input inside a `[data-browse-form]` and
    auto-submits it, so nesting the adder in the sort form would fire a sort request per keystroke
    AND serialize the search text into the sort URL."""
    owner = _staff(client)
    game_list = _list(owner, 2)

    body = client.get(_url(game_list)).content.decode()

    form_open = body.index('data-browse-form')
    adder_at = body.index('data-gl-adder')
    assert adder_at < form_open, 'the adder is inside the sort form and will be serialized into it'


def test_the_results_panel_is_an_overlay_not_in_flow():
    """In flow it pushed the whole grid down by up to 336px on every debounced keystroke that changed
    the result count -- on a 375px phone that left about one tile row visible. Asserted against the
    BUILT stylesheet, because that is what the browser loads."""
    built = _read('static/css/output.css')

    rule = re.search(r'\.gl-adder__panel\{([^}]*)\}', built)
    assert rule, 'the results panel has no rule in the built CSS'
    body = rule.group(1)
    assert 'position:absolute' in body, 'the results panel is still in flow'
    assert 'z-index' in body
def test_the_edit_form_carries_the_same_ceilings_as_the_create_dialog(client):
    """Without them `maxlength="{{ name_max_length }}"` renders empty, browsers ignore it, and the
    counter has no ceiling -- so the field accepts more than the service will store and the first a
    hunter hears of it is a refusal on save."""
    from gamelists.models import DESCRIPTION_MAX_LENGTH, NAME_MAX_LENGTH

    owner = _staff(client)
    game_list = _list(owner, 1)

    body = client.get(_url(game_list)).content.decode()

    assert f'maxlength="{NAME_MAX_LENGTH}"' in body
    assert f'maxlength="{DESCRIPTION_MAX_LENGTH}"' in body
    assert 'data-charcount' in body


def test_a_visitor_gets_no_edit_form_at_all(client):
    author = ProfileFactory(is_linked=True, psn_username='author')
    game_list = _list(author, 1)
    _staff(client, psn='reader')

    body = client.get(_url(game_list)).content.decode()

    assert 'data-gl-identity-edit' not in body
    assert 'data-gl-edit-open' not in body
    assert 'data-gl-visibility' not in body
    # The list itself still rendered, so the absences above mean something.
    assert 'data-gl-identity-view' in body


@pytest.mark.parametrize('sort', ['name', 'name_desc', 'added', 'oldest'])
def test_every_sort_asks_the_database_for_a_TOTAL_order(client, sort):
    """`position` tiebreaks every sort, so tied rows can never come back in two different orders.

    Why this asserts the QUERY and not the rows: tied-row order is not reliably observable from the
    application layer. Postgres leaves it unspecified, and in practice a small fresh table comes back
    in physical order -- which, in every fixture I could build, coincided with the answer the tiebreak
    produces. Three successive attempts to catch a removed tiebreak by comparing rendered orders all
    passed the mutation: first because sequential `auto_now_add` values never tie, then because
    physical order equalled insertion order, then because the UPDATEs that reordered `position`
    rewrote the rows in the very order being asserted. A test that only passes by coincidence is
    worse than none, because it reads as protection.

    So this checks the mechanism, which is the thing actually guaranteed: the ORDER BY that reaches
    Postgres names `position` as well as its primary key. That cannot be satisfied by luck.

    It matters because `added_at` is `auto_now_add`, set in Python -- ties are rare rather than
    impossible, and a bulk-add path would tie outright -- and two concepts can share a title. Without
    a total order the same list renders differently on two loads: a flicker that reads as a bug and
    cannot be reproduced on demand.
    """
    owner = _staff(client)
    game_list = _list(owner, 3)

    with CaptureQueriesContext(connection) as captured:
        client.get(_url(game_list), {'sort': sort})

    ordered = [q['sql'] for q in captured.captured_queries
               if 'gamelists_gamelistitem' in q['sql'] and 'ORDER BY' in q['sql']]
    assert ordered, 'no ordered query against the items table -- the scan is broken, not the view'

    order_by = ordered[0].rsplit('ORDER BY', 1)[1]
    assert 'position' in order_by, f'{sort!r} has no tiebreak: ORDER BY{order_by}'
    # And the tiebreak is a TIEBREAK, not the primary key -- it must not be the only term.
    assert order_by.count(',') >= 1, f'{sort!r} orders by position alone: ORDER BY{order_by}'


def test_the_publish_sweep_is_clipped_to_the_card():
    """It shipped escaping the card and sweeping the whole page.

    The beat is a pseudo-element the width of the header card that travels from -30% to 130% of
    itself. `position: relative` establishes a positioning context, not a CLIPPING one, so with
    nothing to clip it the highlight left the card and slid across the viewport. Invisible to every
    other test here -- it renders correctly, it just renders in the wrong place.

    Asserted against the BUILT stylesheet, because that is what the browser loads.
    """
    built = _read('static/css/output.css')

    # ALL the blocks, not the first one. lightningcss splits this selector across three rules -- two
    # of them custom-property fallbacks for `color-mix()` -- and `re.search` returns the fallback,
    # which carries no layout at all. The first version of this test failed against correct CSS for
    # exactly that reason.
    blocks = re.findall(r'\.gl-published\{([^}]*)\}', built)
    assert blocks, 'the publish beat has no rule in the built CSS'
    declarations = ''.join(blocks).replace(' ', '')
    assert 'overflow:hidden' in declarations, (
        f'the sweep is unclipped and will escape the card: {blocks}'
    )

    # And the animation it needs to contain is still there -- so this cannot pass by the beat having
    # been quietly deleted.
    assert '@keyframes glPublishSweep' in built


def test_the_editor_hooks_the_audit_findings_depend_on_are_rendered(client):
    """Each of these is a hook a JS fix reaches for. A renamed or dropped attribute turns the fix
    into a silent no-op, which is how several defects on this branch survived a browser pass."""
    owner = _staff(client)
    game_list = _list(owner, 1)

    body = client.get(_url(game_list)).content.decode()

    # The tallies yield while the editor is open (at 375px they left the name field ~11 chars wide).
    assert 'data-gl-tallies' in body
    # The trailing breadcrumb is the third place the name appears and was the one left stale.
    assert 'data-breadcrumb-current' in body
    # Publish moves focus to the control that replaces it rather than dropping it on <body>.
    assert 'data-gl-unpublish' in body


def test_a_collection_carries_no_drag_hooks(client):
    """`data-item-id` is read only by the reorder payload. A markup hook with no reader is how a
    future reader concludes a feature exists where it does not.

    This was written when drag was deleted, and it kept its value when Ranked brought drag back: the
    hook is now rendered `{% if can_reorder %}`, so it is present exactly where something reads it
    and absent everywhere else. Had it been rendered unconditionally instead, this would have been
    "fixed" by deletion and the site would carry a dead hook on every Collection.
    """
    owner = _staff(client)
    game_list = _list(owner, 2)

    body = client.get(_url(game_list)).content.decode()
    assert 'data-item-id' not in body

    # And the partial's comment names the file that actually wires the reveal. It said `gamelists.js`
    # -- the My Lists page, deliberately not loaded here -- pointing a reader at the wrong file for
    # exactly the blank-grid bug it describes.
    partial = _read('templates/gamelists/partials/detail_items.html')
    assert 'list-detail.js' in partial
    assert 'gamelists.js` wires' not in partial


def test_the_adder_panel_is_reachable_with_a_keyboard_up_and_above_the_tabbar():
    """Two mobile defects in one rule. A fixed 21rem cap put most of the panel off-screen with the
    soft keyboard up, and z-index 30 painted it under `.mobile-tabbar` (z-index 40, fixed, below
    `lg`)."""
    built = _read('static/css/output.css')

    rule = re.search(r'\.gl-adder__panel\{([^}]*)\}', built)
    assert rule, 'the results panel has no rule in the built CSS'
    declarations = rule.group(1)

    assert 'dvh' in declarations, 'the panel cap does not shrink for the soft keyboard'
    z = re.search(r'z-index:(\d+)', declarations)
    assert z and int(z.group(1)) > 40, f'the panel paints under the mobile tabbar: {declarations}'


def test_the_edit_control_is_a_labelled_action_not_an_inline_pencil(client):
    """It opens the WHOLE editing state, so it has to look like it does.

    This was a 44px icon-only button beside the `h1`, and a bare pencil next to a field is the
    inline field-edit idiom -- the one you meet a dozen times down a settings form, each editing
    the value it sits beside. So it read as "rename this heading" and went unfound, while what it
    actually opens is name, description, sections and arrange mode.

    The 44px rule it used to be pinned on was the right rule for a bare glyph and does not apply to
    a labelled button: it now sits in `.gl-actions` at exactly the size of Like, Follow, Report and
    Publish, which is the page's own standard for an action.

    What is pinned instead: it carries a WORD, it lives in the actions band, and it states the
    panel it controls.
    """
    owner = _staff(client, psn='owner')
    game_list = _list(owner, 1)

    body = client.get(_url(game_list)).content.decode()
    band = body[body.index('class="gl-actions"'):]
    band = band[:band.index('</div>')]

    assert 'data-gl-edit-open' in band, 'the edit control is not in the actions band'
    assert 'Edit list' in band, 'the edit control is still unlabelled'
    # Named state, so it is not an anonymous toggle to a screen reader.
    # `max(0, ...)`: Edit is the FIRST control in the band, so a bare `- 200` went negative and
    # Python read it as "200 from the end" -- slicing the tail of the band instead of the button.
    start = max(0, band.index('data-gl-edit-open') - 200)
    opener = band[start:]
    opener = opener[:opener.index('</button>')]
    assert 'aria-expanded' in opener and 'aria-controls="gl-edit-panel"' in opener, (
        'the edit control does not say what it opens'
    )
    # And the panel it names exists, so `aria-controls` is not pointing at nothing.
    assert 'id="gl-edit-panel"' in body


def test_the_edit_opener_is_looked_up_where_it_actually_lives(client):
    """THE CONTROL SHIPPED DEAD, and every other test on this page passed.

    `wireIdentityEditor` scopes its lookups to `[data-gl-identity]`, which was correct while the
    opener was a pencil inside that block. Moving it into the `.gl-actions` band made it a SIBLING
    of that element, so `root.querySelector` returned null, no click listener was ever bound, and
    the button did nothing. Nothing caught it: every assertion here reads rendered HTML, and no
    server-side test can see that a listener was not attached.

    So the pairing is pinned directly -- the opener lives outside the identity root, therefore the
    lookup must be document-scoped. Either half moving without the other fails here.
    """
    owner = _staff(client, psn='owner')
    game_list = _list(owner, 1)
    body = client.get(_url(game_list)).content.decode()
    js = _decommented(_read('static/js/list-detail.js'))

    # Half one: the button really is outside the block the editor scopes itself to.
    #
    # The `assert identity` is not decoration. If the actions band ever moves ABOVE the identity
    # block, `start > stop` and Python hands back an empty string -- so the membership check below
    # becomes trivially true and this half of a two-half pairing stops constraining anything,
    # while the docstring still claims "either half moving without the other fails here".
    start, stop = body.index('data-gl-identity'), body.index('class="gl-actions"')
    identity = body[start:stop]
    assert identity, 'the actions band no longer follows the identity block; the slice is empty'
    assert 'data-gl-edit-open' not in identity, (
        'the opener moved back inside the identity root; this guard no longer describes the page')

    # Half two: so no lookup may be scoped to that root.
    assert "root.querySelector('[data-gl-edit-open]')" not in js, (
        'the opener is looked up inside a root it does not live in -- the button will not bind')
    assert "document.querySelector('[data-gl-edit-open]')" in js, (
        'the opener is not looked up from the document')


def test_the_edit_control_keeps_its_expanded_state_honest():
    """`aria-expanded` that never changes is worse than none -- it states a fact and then lies.
    Both edges are wired, and both are pinned, because the close path is the easy one to forget."""
    js = _decommented(_read('static/js/list-detail.js'))

    # BOTH VALUES, not a headcount. `js.count(...) == 2` is the idiom this very file rejects two
    # hundred lines up ("the count cannot tell a new GUARDED write from a new UNGUARDED one; both
    # move it by one"). Here it could not tell which element was being written, nor that one call
    # sets `true` and the other `false` -- two `'true'` calls passed just as happily.
    assert "setAttribute('aria-expanded', 'true')" in js, 'opening never marks the panel expanded'
    assert "setAttribute('aria-expanded', 'false')" in js, 'closing never marks it collapsed'
    # And both are written against the opener the template actually renders, not some other node.
    assert 'editOpener()' in js, 'the aria state is no longer maintained on the edit opener'


def test_the_header_card_holds_no_page_action(client):
    """Every other rebuilt header on the site is identity plus its headline number and nothing else.
    A button sharing the tally's slot sits at the same optical level as a display number and the two
    compete -- which is why browse's "My lists" and My Lists' "New list" moved to their toolbars.

    The detail page's actions are the exception ON PURPOSE: they act on the very thing the header
    describes, and the toolbar below is about the games. So they stay, in their own band with a rule
    above them rather than loose under the title.
    """
    owner = _staff(client)
    game_list = _list(owner, 2)

    body = client.get(_url(game_list)).content.decode()

    assert 'gl-actions' in body
    head = body[body.index('pp-head-cascade'):body.index('gl-actions')]
    # The identity block and the tallies -- and no controls competing with them.
    assert 'data-game-count' in head
    assert 'data-gl-publish' not in head, 'an action is back in the identity row'


def test_the_action_caption_can_actually_take_its_own_row(client):
    """The mobile rule for this band matched NOTHING as first written.

    `.gl-actions > span:not(.contents)` looked right and reached nothing: the caption is a
    GREAT-grandchild, nested under two `display: contents` wrappers. `display: contents` promotes an
    element into the parent's flex LAYOUT but not into its DOM position, and combinators match the
    DOM -- so at 375px the caption was still wedged against a button, which is the exact defect the
    band was introduced to fix. Only the selector was wrong, which is why it looked correct.
    """
    owner = _staff(client)
    private = _list(owner, 1, public=False)

    body = client.get(_url(private)).content.decode()
    assert 'gl-actions__note' in body, 'the caption carries no hook for the mobile rule'

    built = _read('static/css/output.css')
    assert '.gl-actions__note{flex-basis:100%}' in built.replace(' ', ''), (
        'the caption rule is missing from the built CSS'
    )
    # And the dead child-combinator form is gone rather than left beside the working one.
    assert '.gl-actions>span:not(.contents)' not in built.replace(' ', '')


def test_the_games_use_the_shared_card_not_the_overlay_tile(client):
    """A game should look the same everywhere on the site, and the overlay tile was the wrong
    primitive here for a structural reason rather than a stylistic one.

    `.pp-gtile` puts its title on the art behind a scrim. That works for a genre or franchise tile,
    where the image is ONE backdrop serving as decoration. Here the cover IS the content -- the grid
    is scanned by art to find a specific game -- and the scrim needed to keep white text legible over
    marketing covers darkens exactly what is being scanned. Protecting the text hid the art;
    protecting the art lost the text.

    Both halves pinned: the card is used, AND the tile is gone. Asserting only the first would pass
    with both rendered.
    """
    owner = _staff(client)
    game_list = _list(owner, 3)

    body = client.get(_url(game_list)).content.decode()
    grid = body[body.index('id="gl-items"'):body.index('id="gl-items"') + 6000]

    assert 'pp-gcard__cover' in grid and 'pp-gcard__title' in grid
    assert 'pp-gtile__scrim' not in grid, 'the overlay tile is still rendering'
    assert 'pp-gtile__body' not in grid

    # The column ladder moved with it -- more per row, which is what pays for the taller card.
    assert 'pp-gbrowse__grid' in body


def test_the_reveal_observer_follows_the_card_class(client):
    """`.pp-reveal .pp-gcard { opacity: 0 }` holds every card hidden until an observer clears it, so
    the selector the JS hands `staggerReveal` has to match the class the template renders. Leaving it
    on `.pp-gtile` would have found nothing and left the whole grid invisible after any sort -- the
    same blank-grid failure this page already shipped once."""
    js = _decommented(_read('static/js/list-detail.js'))

    assert "cardSelector: '.pp-gcard'" in js
    assert "cardSelector: '.pp-gtile'" not in js


# ── coverage the audit found missing ─────────────────────────────────────────────────────────────

def test_opening_the_editor_is_what_reveals_the_bar(client):
    """The whole feature hangs off two lines nothing pinned.

    `syncPositionsVisibility` gates on `editorOpen`, and `close()` setting it false WAS asserted --
    but `open()` setting it true was not. Delete those two lines and the position bar is `hidden`
    forever, the mode is unreachable, and every test stays green.
    """
    js = _decommented(_read('static/js/list-detail.js'))

    open_body = js[js.index('function open() {'):js.index('function close() {')]
    assert 'editorOpen = true' in open_body
    assert 'syncPositionsVisibility()' in open_body, 'opening the editor never reveals the bar'


def test_something_actually_calls_enter_positioning(client):
    """The only call site is the toggle's click handler. Remove the listener and a rendered button
    does nothing -- and the one test that mentions `enterPositioning()` asserts it is ABSENT from the
    swap handler, so nothing anywhere required it to be reachable."""
    js = _decommented(_read('static/js/list-detail.js'))

    wire = js[js.index('function wirePositioning('):js.index('function onGrabClick(')]
    assert "toggle.addEventListener('click'" in wire
    assert 'enterPositioning()' in wire and 'exitPositioning()' in wire


def test_the_grip_does_something_when_pressed(client):
    """It is a <button> announced as "Reorder <game>". Nothing bound a click to it: `onCardClick`
    requires a `.pp-gcard` ancestor and the grip is the card's SIBLING, so pressing it -- by mouse,
    Enter or Space -- did nothing at all. A button that is inert on activation is a broken promise
    however good the arrow-key path beside it is."""
    js = _decommented(_read('static/js/list-detail.js'))

    grab = js[js.index('function onGrabClick('):js.index('function boot(')]
    assert "closest('[data-gl-grab]')" in grab
    assert 'togglePicked(row)' in grab
    assert "addEventListener('click', onGrabClick)" in js, 'the grip handler is never attached'


def test_the_picked_state_is_reported_to_assistive_tech(client):
    """Announcing the pick-up once told somebody at the moment it happened and then left no way to
    ask again. The grip is a toggle button now, so "pressed" is a standing answer to "which card am
    I holding"."""
    js = _decommented(_read('static/js/list-detail.js'))

    setter = js[js.index('function setGrabPressed('):js.index('function togglePicked(')]
    assert "setAttribute('aria-pressed'" in setter
    assert 'setGrabPressed(row, true)' in js and 'setGrabPressed(pickedRow, false)' in js

    # ...and the BAR's toggle must NOT also carry `aria-pressed`, because its label changes with
    # state. Doing both makes "Done, pressed" ambiguous about whether Done is the state or the act.
    paint = js[js.index('function paintPositionsToggle('):js.index('function setPositionsStatus(')]
    assert "removeAttribute('aria-pressed')" in paint


def test_the_type_radios_actually_render_for_the_owner(client):
    """The JS looks up `[name="list_type"][value="..."]` and the page carries `data-list-type`, both
    asserted -- but nothing required a matching input to exist. Delete the fieldset and the type
    becomes unswitchable from the detail page with everything green."""
    owner = _staff(client, psn='owner')
    ranked = _ranked(owner, 2)

    body = client.get(_url(ranked)).content.decode()
    assert 'name="list_type" value="collection"' in body
    assert 'name="list_type" value="ranked"' in body

    client.logout()
    _staff(client, psn='visitor')
    assert 'name="list_type"' not in client.get(_url(ranked)).content.decode()


def test_renumber_repaints_both_the_numeral_and_the_spoken_rank(client):
    """Screen-reader-only, and it shipped broken once already (as an `sr-only` span nothing
    announced). The prefix strip is what stops "Number 2: Number 1: Elden Ring" accumulating."""
    js = _decommented(_read('static/js/list-detail.js'))

    start = js.index('function renumber(')
    fn = js[start:js.index('function boot(', start)]
    # `running`, not a per-grid `i + 1`. The counter walks EVERY grid on the page, so an index scoped
    # to one grid would restart at 1 under each header regardless of which numbering mode the list is
    # in -- showing the restart mode to somebody who chose continue-through.
    assert 'badge.textContent = String(running)' in fn
    assert "'Number ' + running + ': '" in fn
    assert 'replace(/^Number' in fn, 'the rank prefix will stack on every move'
    assert 'restartNumbering()' in fn, 'the repaint must honour the mode the list is actually in'


def test_an_arrow_at_the_end_of_the_list_keeps_its_normal_meaning(client):
    """The early return has to come BEFORE `preventDefault`, or pressing Down on the last card
    swallows the key and the page stops scrolling."""
    js = _decommented(_read('static/js/list-detail.js'))

    start = js.index('function onPositionKey(')
    fn = js[start:js.index('function itemIdsIn(', start)]
    # Scoped to the ARROW branch. Compared against the whole function, the first `preventDefault` is
    # ESCAPE's -- which legitimately comes before the neighbour check -- so the assertion was really
    # about an unrelated line and failed for a reason that had nothing to do with what it tests.
    arrow = fn[fn.index('var neighbour'):]
    assert arrow.index('if (!neighbour') < arrow.index('e.preventDefault()'), \
        'the no-op case swallows the arrow key instead of letting the page scroll'


def test_the_chrome_fragment_is_not_served_on_a_full_page_render(client):
    """`?chrome=1` was gated on the querystring alone, so typing or sharing that URL rendered the
    FULL page -- which already contains the position slot and the sort control -- and then had the
    items partial render both again inside the grid panel. Two elements per id, so the second bar is
    dead markup and a stray unlabelled <select> sits below the grid."""
    owner = _staff(client)
    ranked = _ranked(owner, 3)

    page = client.get(_url(ranked), {'chrome': '1'}).content.decode()

    assert page.count('id="gl-positions-slot"') == 1, 'the position bar is rendered twice'
    assert page.count('id="gl-sort-select"') == 1, 'the sort control is rendered twice'
    assert 'hx-swap-oob' not in page, 'out-of-band markup has no meaning in a full page'


def test_an_empty_ranked_list_is_not_offered_a_reorder_mode(client):
    """Owner + ranked + zero games satisfied every other clause, so the bar appeared and the grid
    carried `data-gl-reorder` over nothing -- a mode offered for a list with no order, whose one
    possible action the endpoint 400s."""
    owner = _staff(client)
    empty = _ranked(owner, 0)

    resp = client.get(_url(empty))

    assert resp.context['can_reorder'] is False
    assert 'data-gl-positions' not in resp.content.decode()


def test_the_mode_ends_when_the_list_stops_being_ranked(client):
    """THE WORST BUG THIS AUDIT FOUND. `syncPositionsVisibility` bailed when `[data-gl-positions]`
    was absent -- and switching Ranked -> Collection DELETES it, because the slot renders the bar only
    under `can_reorder`. `exitPositioning` is the only thing that turns the mode off, so it became
    unreachable in exactly the case that needs it.

    The mode then stayed on forever: every remove button hidden on a Collection, the document keydown
    still bound, and `pickedRow` still pointing at a detached row whose grid kept `data-reorder-url`
    -- so arrow keys silently rewrote positions on a list nobody could see.
    """
    js = _decommented(_read('static/js/list-detail.js'))

    # Ends at the next thing DEFINED BELOW it. `attachDrag` sits 150 lines ABOVE, so searching
    # from 0 returned an earlier index and the slice came back empty -- the same slice-direction
    # mistake this file has now made four times, which is why the order is checked, not assumed.
    start = js.index('function syncPositionsVisibility(')
    fn = js[start:js.index('var GRAB_EARLIER', start)]
    assert fn.index('exitPositioning(true)') < fn.index("querySelector('[data-gl-positions]')"), \
        'the mode can only be left while the bar still exists -- which is not when it must be left'
    # `!editorOpen` and not `!show`: the bar carries the section controls too, and a member owner of
    # a list with nothing to drag still gets the "Add a section" row. Tying the whole bar to whether
    # a DRAG is possible hid the only way to create the sections that would make one possible.
    assert 'if (block) { block.hidden = !editorOpen; }' in fn, 'a missing bar must not abort the sync'


def test_a_failed_reorder_is_not_re_applied_by_the_one_queued_behind_it(client):
    """The chain stops the writes racing; it does not stop a queued write from UNDOING the recovery.
    First POST fails, its handler refreshes the grid back to the server's order, and the second
    request then goes out carrying a body captured from the pre-refresh DOM -- a complete, valid
    order including the move the hunter was just told was not saved."""
    js = _decommented(_read('static/js/list-detail.js'))

    fn = js[js.index('function saveOrder('):js.index('function renumber(')]
    assert 'var generation = orderGen' in fn
    assert 'if (generation !== orderGen) { return null; }' in fn
    assert 'orderGen += 1' in fn, 'a failure must retire everything already queued'
    assert 'pendingSaves' in fn, '"Saved" can appear over a write still in flight'


def test_the_chrome_refresh_verifies_it_actually_swapped(client):
    """htmx resolves its ajax promise for EVERY status and maps 4xx/5xx to `swap: false`, which
    `refreshItems` documents at length and guards against. The chrome refresh had only a `.catch`,
    which fires on a network error alone -- so a 500 left numerals and grips on a list the server no
    longer calls ranked, with no sign anywhere."""
    js = _decommented(_read('static/js/list-detail.js'))

    start = js.index('function refreshAfterTypeChange() {')
    fn = js[start:js.index('var TOGGLES', start)]
    assert 'refreshSeq' in fn, 'an out-of-order chrome response can repaint over a newer one'
    assert 'did not swap' in fn, 'a non-2xx resolves quietly and is treated as success'


def test_the_adder_results_do_not_also_move_the_picked_card(client):
    """The results rows are <button>s, so `isTyping` does not exclude them and the document-level
    position handler ran too: arrowing through search results moved the picked card and fired a
    reorder write, and Escape both closed the panel and dropped the pick.

    READ FROM `utils.js` since the adder became `PP.GameAdder`. The document-level position handler
    this defends against is still list-detail's, so the guard is still this page's to keep -- it just
    now pins the shared component every adder gets, which is strictly more coverage than before.
    """
    js = _decommented(_read('static/js/utils.js'))

    start = js.index("panel.addEventListener('keydown'")
    handler = js[start:js.index('});', start)]
    assert handler.count('e.stopPropagation()') >= 2, \
        'the arrow and Escape branches must both stop the position handler seeing the key'


def test_boot_resets_the_modes_state_not_just_the_grid(client):
    """The file commits to honouring the `onPageReady` restore contract even though this site's htmx
    config never fires it. Under that contract a restored page painted "Done" on a toggle whose panel
    has no `[data-positioning]`, showed the bar over a CLOSED editor, and kept a live Sortable and a
    document keydown bound to a discarded grid."""
    js = _decommented(_read('static/js/list-detail.js'))

    fn = js[js.index('function boot(first) {'):js.index('if (PP.onPageReady)')]
    for reset in ('detachDrag()', 'positioning = false', 'editorOpen = false',
                  'orderChain = Promise.resolve()'):
        assert reset in fn, f'{reset} is not reset on boot'


def test_exiting_the_mode_actually_clears_the_flag(client):
    """`exitPositioning` is the only thing that sets `positioning` back to false, and nothing pinned
    that it does. Without it every later `if (!positioning) return` guard passes forever."""
    js = _decommented(_read('static/js/list-detail.js'))

    start = js.index('function exitPositioning(')
    fn = js[start:js.index('function paintPositionsToggle(', start)]
    assert 'positioning = false' in fn
    assert 'detachDrag()' in fn, 'leaving the mode must tear the drag down with it'
    assert "delete panel.dataset.positioning" in fn, 'the CSS state outlives the JS state'


def test_an_unlinked_owner_is_not_offered_the_reorder_handles(client):
    """`ReorderItemsView` carries `_LinkedProfileRequired`, which answers a JSON caller with an HTML
    redirect. `can_act` documents this exact reasoning for the social buttons; the reorder affordance
    was missing it, so the page and the endpoint disagreed about who can act.

    ASSERTED AGAINST THE RENDER rather than against the source. This read the `can_reorder` clause out
    of `views.py` and searched it for `viewer.is_linked` -- which broke the moment that condition
    moved into a shared `arrangeable` local, even though the behaviour was identical. A test that
    fails when correct code is refactored is testing the spelling."""
    # Built while linked and unlinked AFTERWARDS, because `create_list` refuses an unlinked author --
    # which means this state is only reachable the way a real hunter reaches it, by unlinking a PSN
    # account they already had.
    owner = _staff(client, psn='unlinked')
    game_list = _ranked(owner, 3)
    owner.is_linked = False
    owner.save(update_fields=['is_linked'])

    resp = client.get(_url(game_list))

    assert resp.context['is_owner'] is True, 'the fixture stopped testing what it claims'
    assert resp.context['can_reorder'] is False, 'the page offers a drag the endpoint would redirect'
    assert resp.context['can_arrange'] is False
    assert 'data-gl-grab' not in resp.content.decode()


def test_the_drag_manager_defaults_its_touch_threshold(client):
    """`|| 5` at the point of USE meant a caller passing 0 to disable the threshold silently got 5,
    and the default was skipped entirely unless `delay` was truthy. The default belongs in the
    constructor, where `=== undefined` can tell "not passed" from "passed as zero"."""
    utils = _decommented(_read('static/js/utils.js'))

    start = utils.index('this.touchStartThreshold')
    assign = utils[start:start + 200]
    assert '=== undefined ? 5' in assign, 'a caller passing 0 is silently overridden'
    assert 'sortableConfig.touchStartThreshold = this.touchStartThreshold;' in utils,         'the default is re-applied at the point of use, so 0 can never reach SortableJS'


def test_the_remove_control_steps_aside_while_arranging(client):
    """Documented in three places and asserted nowhere. It is also what made the HIGH bug above
    damaging rather than merely untidy: with the mode stuck on, this hid every remove button on a
    Collection."""
    css = _read('static/css/components/gamelists.css')
    assert '#gl-items-panel[data-positioning] .gl-item__remove { display: none; }' in css


# ── sections ─────────────────────────────────────────────────────────────────────────────────────

def _member(client, psn='member'):
    profile = _staff(client, psn=psn)
    profile.user_is_premium = True
    profile.save(update_fields=['user_is_premium'])
    return profile


def _sectioned(client, psn='member'):
    """A ranked list with two sections and four games, interleaved so that grouping and ORDERING
    cannot be confused with one another: positions 0 and 2 go in the first section, 1 and 3 in the
    second. A test that put 0,1 in one and 2,3 in the other would pass with the grouping ignored."""
    owner = _member(client, psn=psn)
    game_list = _ranked(owner, 0)
    items = []
    for title in ('Alpha', 'Bravo', 'Charlie', 'Delta'):
        concept = ConceptFactory(unified_title=title)
        GameFactory(concept=concept, title_platform=['PS5'])
        items.append(svc.add_concept(game_list, owner, concept))

    first = svc.create_section(game_list, owner, name='Finished')
    second = svc.create_section(game_list, owner, name='Playing')
    svc.assign_item(game_list, owner, items[0], first)
    svc.assign_item(game_list, owner, items[2], first)
    svc.assign_item(game_list, owner, items[1], second)
    svc.assign_item(game_list, owner, items[3], second)
    return owner, game_list, items, first, second


def test_a_list_without_sections_renders_exactly_as_it_did(client):
    """The common path, and the one that must not change: `groups` is None until somebody makes a
    section, so an ordinary list renders the single `#gl-items` grid it always did."""
    owner = _staff(client)
    game_list = _list(owner, 3)

    resp = client.get(_url(game_list))
    body = resp.content.decode()

    assert resp.context['groups'] is None
    assert body.count('id="gl-items"') == 1
    assert 'gl-section__head' not in body, 'an unsectioned list rendered a section header'


def test_a_sectioned_list_groups_its_games_under_their_headers(client):
    """The owner's view carries an EMPTY ungrouped bucket as well, because it is the only way back
    out of a section -- see `test_the_ungrouped_bucket_leads_and_only_shows_when_it_holds_something`.
    A reader gets exactly the two named groups."""
    owner, game_list, items, first, second = _sectioned(client)
    client.logout()

    resp = client.get(_url(game_list))
    groups = resp.context['groups']

    assert [s.name if s else None for s, _ in groups] == ['Finished', 'Playing']
    assert [[i.concept.unified_title for i in bucket] for _, bucket in groups] == [
        ['Alpha', 'Charlie'], ['Bravo', 'Delta']]

    body = resp.content.decode()
    assert '>Finished</h2>' in body and '>Playing</h2>' in body
    # One grid PER GROUP, and `#gl-items` on none of them -- an id has to be unique.
    assert body.count('pp-gbrowse__grid') == 2
    assert 'id="gl-items"' not in body


def test_the_ungrouped_bucket_leads_and_stays_for_whoever_can_file(client):
    """A list that has just gained sections has EVERYTHING unassigned, so this is the normal state on
    the way in rather than an error. Burying it under the named sections would hide the games somebody
    is about to file.

    IT ALSO STAYS WHEN EMPTY, but only for somebody who can arrange. A header for nothing is noise to
    a reader, so they never see it -- but for the owner it is the only way back OUT of a section:
    filing the last loose card used to remove the bucket and take the drop target with it, so nothing
    could be un-filed by pointer (no grid to drop onto) or by keyboard (no group before the first
    section) until they deleted a whole section to get their game back."""
    owner, game_list, items, first, _second = _sectioned(client)

    # Everything is filed. The OWNER keeps the empty bucket...
    owner_groups = client.get(_url(game_list)).context['groups']
    assert [s.name if s else None for s, _ in owner_groups] == [None, 'Finished', 'Playing']
    assert owner_groups[0][1] == [], 'the kept bucket should be the empty one'
    assert 'Not in a section' in client.get(_url(game_list)).content.decode()

    # ...and a READER does not, because for them it is a header over nothing.
    client.logout()
    assert [s.name for s, _ in client.get(_url(game_list)).context['groups']] == \
        ['Finished', 'Playing']
    assert 'Not in a section' not in client.get(_url(game_list)).content.decode()

    # Un-file one, and the bucket carries it, still FIRST, for everyone.
    svc.assign_item(game_list, owner, items[0], None)

    groups = client.get(_url(game_list)).context['groups']
    assert groups[0][0] is None, 'the ungrouped bucket is not first'
    assert [i.concept.unified_title for i in groups[0][1]] == ['Alpha']
    assert 'Not in a section' in client.get(_url(game_list)).content.decode()


def test_numbering_runs_through_the_whole_list_by_default(client):
    """Continue-through is the default because it is what a ranked list already MEANS: adding
    sections to one should group it, not renumber it underneath the author.

    ONCE THERE ARE SECTIONS, THE SECTIONS ARE PART OF THE SEQUENCE. Alpha and Charlie hold positions
    0 and 2 and sit together in the first section, so they read 1 and 2 -- not 1 and 3. This asserted
    1 and 3 for one slice, pinning `position + 1` straight through: individually true numerals that
    no reader could follow down the column, because grouping reorders the page and `position` does
    not know it happened."""
    owner, game_list, items, _first, _second = _sectioned(client)

    groups = client.get(_url(game_list)).context['groups']
    ranks = {i.concept.unified_title: i.display_rank for _s, bucket in groups for i in bucket}

    assert ranks == {'Alpha': 1, 'Charlie': 2, 'Bravo': 3, 'Delta': 4}


def test_a_rank_does_not_change_when_the_sort_does(client):
    """THE INVARIANT THAT DECIDED THE RULE ABOVE, and the reason "just number down the page" is wrong.

    `detail_card.html` shows the plate on every sort because a rank is a fact about the ENTRY rather
    than about the view. Numbering the RENDERED order honours that on the rank sort and destroys it
    everywhere else: sorted A-Z, the list would renumber 1..N alphabetically and claim the alphabet
    was the author's ranking. So the rank comes from the canonical order -- sections in their own
    order, `position` within each -- and is merely DISPLAYED under whatever sort is showing."""
    owner, game_list, items, _first, _second = _sectioned(client)

    def ranks(sort):
        groups = client.get(_url(game_list) + f'?sort={sort}').context['groups']
        return {i.concept.unified_title: i.display_rank for _s, bucket in groups for i in bucket}

    canonical = ranks('rank')
    assert canonical == {'Alpha': 1, 'Charlie': 2, 'Bravo': 3, 'Delta': 4}
    # Z-A, so the buckets arrive REVERSED and a page-order count would hand out 1 and 2 to Charlie
    # and Alpha instead. Same entries, same numbers.
    assert ranks('name_desc') == canonical


def test_numbering_can_restart_in_each_section(client):
    owner, game_list, items, _first, _second = _sectioned(client)
    svc.update_list(game_list, owner, restart_numbering=True)

    groups = client.get(_url(game_list)).context['groups']
    ranks = {i.concept.unified_title: i.display_rank for _s, bucket in groups for i in bucket}

    assert ranks == {'Alpha': 1, 'Charlie': 2, 'Bravo': 1, 'Delta': 2}

    # ...and the STORED order is untouched, which is the whole point of computing this at render.
    assert list(game_list.items.order_by('position').values_list('position', flat=True)) == [0, 1, 2, 3]


def test_the_visible_plate_and_the_spoken_rank_agree(client):
    """They are computed from one field now, which is why. The plate is `aria-hidden` and the rank
    reaches a screen reader through the card's `aria-label`, so the two drifting apart is silent."""
    owner, game_list, items, _first, _second = _sectioned(client)
    svc.update_list(game_list, owner, restart_numbering=True)

    body = client.get(_url(game_list)).content.decode()

    assert re.findall(r'<span class="gl-rank"[^>]*>(\d+)</span>', body) == ['1', '2', '1', '2']
    assert 'aria-label="Number 1: Alpha"' in body
    assert 'aria-label="Number 2: Charlie"' in body
    assert 'aria-label="Number 1: Bravo"' in body


def test_a_sort_orders_within_each_section_rather_than_flattening_it(client):
    """Sections are STRUCTURE; a sort is a view of it. Sorting A-Z across a sectioned list would
    dissolve the grouping, which is the one thing the author built."""
    owner, game_list, items, _first, _second = _sectioned(client)

    groups = client.get(_url(game_list), {'sort': 'name_desc'}).context['groups']

    # The NAMED groups. An owner also carries the empty ungrouped bucket, and what this test is about
    # is that a sort orders WITHIN a section rather than flattening the grouping away -- not how many
    # buckets the page happens to render.
    named = [s.name for s, _ in groups if s is not None]
    assert named == ['Finished', 'Playing'], 'the sort reordered the sections'
    assert [[i.concept.unified_title for i in bucket] for s, bucket in groups if s is not None] == [
        ['Charlie', 'Alpha'], ['Delta', 'Bravo']], 'the sort did not apply inside each section'


def test_a_sectioned_ranked_list_can_be_both_ordered_and_filed(client):
    """The slice this waited for. `can_reorder` was held false on any sectioned list because dragging
    across a boundary is a cross-container drop PLUS an assignment and only the first half was wired
    -- a drag that worked in one direction, which is worse than none.

    Both flags now, and they are not the same one: `can_arrange` is "a card can be dragged at all"
    and `can_reorder` is the stricter "a drop POSITION means something"."""
    owner, game_list, _items, _first, _second = _sectioned(client)

    resp = client.get(_url(game_list))

    assert resp.context['can_arrange'] is True
    assert resp.context['can_reorder'] is True
    body = resp.content.decode()
    assert 'data-gl-grab' in body
    # Every group is a drop target, and the SECTION each one stands for travels with it -- without
    # that a drop has nowhere to report it landed. THREE, not two: the owner also keeps the empty
    # ungrouped bucket, which is the only way back OUT of a section.
    assert body.count('data-gl-arrange') == 3
    assert 'data-gl-arrange data-section-id=""' in body, 'no way back out of a section'
    assert f'data-section-id="{_first.id}"' in body and f'data-section-id="{_second.id}"' in body


def test_a_sectioned_collection_can_be_filed_but_not_ordered(client):
    """THE CASE THE SINGLE FLAG COULD NOT EXPRESS, and the reason there are two.

    A Collection has no `rank` sort at all, so a drop position is an artefact of whatever sort is
    showing and writing it back would invent an order the author never chose. But filing a game under
    a header is exactly what sections are for, and withholding the drag because ordering is
    impossible left every sectioned Collection with headers nothing could be moved into."""
    owner = _member(client, psn='collector')
    game_list = _list(owner, 2)
    svc.create_section(game_list, owner, name='Playing')

    resp = client.get(_url(game_list))

    assert resp.context['can_arrange'] is True
    assert resp.context['can_reorder'] is False
    body = resp.content.decode()
    assert 'data-gl-arrange' in body
    # No ordering hooks: no grips (the keyboard moves a card through a sequence, and there is none)
    # and no reorder endpoint on the grids.
    assert 'data-gl-grab' not in body
    assert 'data-gl-reorder' not in body
    # ...but the cards still carry an identity and somewhere to report a move to.
    assert 'data-assign-url' in body


def test_a_list_with_no_sections_and_no_order_offers_no_drag_at_all(client):
    """The negative that keeps `can_arrange` from meaning "owner". A Collection with no sections has
    nowhere to file anything and no sequence to change, so a drag would be a gesture with no act
    behind it."""
    owner = _member(client, psn='plain')
    game_list = _list(owner, 3)

    resp = client.get(_url(game_list))

    assert resp.context['can_arrange'] is False
    body = resp.content.decode()
    assert 'data-gl-arrange' not in body and 'data-item-id' not in body


def test_a_sectioned_page_does_not_scale_with_its_sections(client):
    """The whale rule, on the axis sections added. `test_the_page_does_not_scale_with_the_list`
    covers items; nothing covered headers, and a per-section queryset is the obvious way to write
    this feature.

    Sections cost ONE query total: the headers are fetched once and the grouping happens in Python
    over rows already in hand. Both sides are bounded -- 200 items, 20 sections -- which is the
    bounded-slice form CLAUDE.md permits rather than the per-row iteration it bans.

    WARMED UP FIRST, because the opening request of a test pays for session load and permission
    caching. Measured without it, the bigger list came out CHEAPER (11 against 15), which reads as
    the opposite of a regression and would have hidden a real one.
    """
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    owner = _member(client, psn='flat')
    small = _ranked(owner, 0)
    big = _ranked(owner, 0)
    for game_list, n_sections, n_items in ((small, 2, 6), (big, 12, 24)):
        sections = [svc.create_section(game_list, owner, name=f'S{i}') for i in range(n_sections)]
        for i in range(n_items):
            concept = ConceptFactory(unified_title=f'G{i:03d}')
            GameFactory(concept=concept, title_platform=['PS5'])
            item = svc.add_concept(game_list, owner, concept)
            svc.assign_item(game_list, owner, item, sections[i % len(sections)])

    client.get(_url(small))
    client.get(_url(big))

    with CaptureQueriesContext(connection) as few:
        client.get(_url(small))
    with CaptureQueriesContext(connection) as many:
        client.get(_url(big))

    assert len(many.captured_queries) == len(few.captured_queries), (
        f'{len(few.captured_queries)} queries with 2 sections, '
        f'{len(many.captured_queries)} with 12')


def test_a_free_hunter_sees_a_sectioned_list_exactly_as_anyone_does(client):
    """Sections are the AUTHOR's tool. Nobody needs a membership to read a list that has them, and a
    reader's view must not differ -- the gate is on making them, not on seeing them."""
    owner, game_list, _items, _first, _second = _sectioned(client, psn='author')
    svc.update_list(game_list, owner, is_public=True)

    client.logout()
    reader = _staff(client, psn='freereader')
    reader.user_is_premium = False
    reader.save(update_fields=['user_is_premium'])

    resp = client.get(_url(game_list))

    assert resp.status_code == 200
    assert [s.name for s, _ in resp.context['groups']] == ['Finished', 'Playing']
    assert '>Finished</h2>' in resp.content.decode()


# ── the arrange bar and the section controls ─────────────────────────────────────────────────────

def test_a_member_owner_gets_the_section_controls_and_a_reader_never_does(client):
    owner, game_list, _items, first, _second = _sectioned(client)

    body = client.get(_url(game_list)).content.decode()
    assert 'data-gl-section-add' in body
    assert f'data-rename-url="/community/lists/{game_list.id}/sections/{first.id}/rename/"' in body
    assert f'data-delete-url="/community/lists/{game_list.id}/sections/{first.id}/delete/"' in body

    client.logout()
    reader = client.get(_url(game_list)).content.decode()
    assert 'data-gl-section-add' not in reader
    assert 'data-gl-section-rename' not in reader
    assert 'data-gl-section-delete' not in reader


def test_a_free_owner_keeps_their_sections_and_is_told_what_changed(client):
    """Membership ending must not delete data or reshuffle a list. What they lose is making MORE --
    so the headers, their delete controls and the drag all stay, and the one thing that goes is said
    out loud rather than silently absent, which on a list that visibly HAS sections reads as a bug."""
    owner, game_list, _items, first, _second = _sectioned(client)
    owner.user_is_premium = False
    owner.save(update_fields=['user_is_premium'])

    resp = client.get(_url(game_list))
    body = resp.content.decode()

    assert resp.context['can_manage_sections'] is False
    assert resp.context['can_arrange'] is True, 'a lapsed member can still tidy their own list'
    assert 'data-gl-section-add' not in body
    assert 'data-gl-section-rename' not in body
    # DELETE SURVIVES: removing your own thing is not the act the perk covers.
    assert 'data-gl-section-delete' in body
    # `gl-lockup`, not `gl-sections__locked`: the lapsed-member line moved OUT of the arrange bar and
    # became the second state of the CTA block, because the bar it lived in is hidden until the
    # editor is opened and does not render at all for a section-less Collection.
    assert 'gl-lockup' in body
    assert 'Your sections are still here' in body


def test_renaming_is_member_gated_and_deleting_is_not(client):
    """The affordances have to match the service exactly rather than approximately, or a hunter is
    shown a control whose endpoint refuses them."""
    service = _read('gamelists/services/game_list_service.py')

    for fn, gated in (('create_section', True), ('rename_section', True),
                      ('delete_section', False), ('reorder_sections', False),
                      ('assign_item', False)):
        start = service.index(f'def {fn}(')
        body = service[start:service.index('\n\n\n', start)]
        assert ('_refuse_if_not_member' in body) is gated, f'{fn} has the wrong membership gate'


def test_the_numbering_toggle_appears_only_where_it_is_a_real_question(client):
    """Two modes that mean the same thing is a question with one answer. It needs a ranked list (a
    Collection has no numerals) AND at least one section (nothing to restart at)."""
    owner, sectioned, _items, _first, _second = _sectioned(client)
    assert 'data-gl-numbering' in client.get(_url(sectioned)).content.decode()

    plain_ranked = _ranked(owner, 2)
    assert 'data-gl-numbering' not in client.get(_url(plain_ranked)).content.decode()

    collection = _list(owner, 2)
    svc.create_section(collection, owner, name='Playing')
    assert 'data-gl-numbering' not in client.get(_url(collection)).content.decode()


def test_an_empty_section_still_has_something_to_drop_onto(client):
    """A grid with no children collapses to zero height, and receiving the first card is the entire
    purpose of an empty section -- `emptyInsertThreshold` cannot rescue a box with no box."""
    owner, game_list, _items, _first, _second = _sectioned(client)
    empty = svc.create_section(game_list, owner, name='Backlog')

    body = client.get(_url(game_list)).content.decode()

    # A ::before ON THE GRID, drawn from this attribute, and NOT a child element. SortableJS's
    # empty-container detection tests `lastChild` with no selector, so any element child switches off
    # the generous `emptyInsertThreshold` hit band -- the placeholder added to make an empty section
    # droppable was the reason it was hard to drop into.
    assert 'data-empty-label="Drop a game here"' in body
    assert 'gl-group__empty' not in body, 'the placeholder is back to being a real child'
    # The empty section is still a DROP TARGET, which is the only reason the box is there.
    assert f'data-section-id="{empty.id}"' in body


def test_a_reader_is_not_invited_to_drop_anything(client):
    """An invitation to an action that does not exist for you is how a page teaches somebody it is
    broken."""
    owner, game_list, _items, _first, _second = _sectioned(client)
    svc.create_section(game_list, owner, name='Backlog')
    client.logout()

    body = client.get(_url(game_list)).content.decode()

    assert 'Drop a game here' not in body
    assert 'data-empty-label="Nothing here yet"' in body,         'the box must keep its shape or the section reads as broken'


def test_the_loose_bucket_is_a_destination_and_not_a_missing_value(client):
    """`data-section-id` is EMPTY on the ungrouped bucket rather than absent. Empty means "out of
    every section", which is a real place to land; absent would make un-filing impossible to express
    and is why the client reads the attribute's presence rather than its truthiness."""
    owner, game_list, items, _first, _second = _sectioned(client)
    svc.assign_item(game_list, owner, items[0], None)

    body = client.get(_url(game_list)).content.decode()

    assert 'data-gl-arrange data-section-id=""' in body


def test_the_swap_sentinel_exists_in_every_shape_of_the_partial(client):
    """Both refresh helpers prove a swap happened by comparing the node they remembered against the
    node that is there afterwards. They used `#gl-items`, which a SECTIONED list does not render at
    all -- so the comparison was `null === null` and every successful refresh of a sectioned list
    reported a failure over a swap that had just worked."""
    owner = _member(client, psn='shapes')

    empty = _list(owner, 0)
    flat = _list(owner, 2, name='Flat')
    _sectioned_owner, sectioned, _i, _f, _s = _sectioned(client, psn='shapes2')

    for game_list in (empty, flat):
        assert 'id="gl-items-root"' in client.get(_url(game_list)).content.decode()
    client.logout()
    assert 'id="gl-items-root"' in client.get(_url(sectioned)).content.decode()

    js = _decommented(_read('static/js/list-detail.js'))
    assert "getElementById('gl-items-root')" in js
    # And nothing still reaches for the flat grid's id, which is the id that does not always exist.
    assert "getElementById('gl-items')" not in js


def test_the_items_partial_closes_every_element_it_opens(client):
    """A stray `</div>` left over from the card extraction closed `#gl-items-panel` early, so the
    truncation line and the out-of-band chrome rendered outside the panel they travel with while a
    second `</div>` in `detail.html` closed the page wrapper. Counted rather than eyeballed."""
    partial = _read('templates/gamelists/partials/detail_items.html')
    source = re.sub(r'{% comment %}.*?{% endcomment %}', '', partial, flags=re.S)

    opens = len(re.findall(r'<div\b', source))
    closes = len(re.findall(r'</div>', source))
    assert opens == closes, f'{opens} <div> against {closes} </div> in detail_items.html'


def test_a_section_change_asks_for_the_chrome_and_an_ordinary_refresh_does_not(client):
    """The arrange bar lives OUTSIDE the swap target and its contents depend on whether any section
    exists, so a section change has to re-render it -- while an add or a remove changes none of it and
    re-sending the sort select mid-sort would replace the control the hunter just used."""
    js = _decommented(_read('static/js/list-detail.js'))

    for fn in ('onSectionAdd', 'onSectionDelete', 'onSectionRename'):
        body = js[js.index(f'function {fn}('):js.index('\n    }', js.index(f'function {fn}('))]
        assert 'refreshItems(true)' in body, f'{fn} leaves the bar describing the previous list'

    # Ends at `wireAdder`, the next function in the file. It used to end at `placeholderIcon`, which
    # moved into `PP.GameAdder` -- an incidental anchor. A section-comment would read better and
    # cannot be used: `_decommented` strips comments before this runs, so the anchor has to be code.
    remove = js[js.index('function onRemove('):js.index('function wireAdder(')]
    assert 'refreshItems()' in remove and 'refreshItems(true)' not in remove


def test_the_numbering_toggle_has_exactly_one_writer(client):
    """It saves through `list_update`, the same call the identity editor uses, because it is a
    property of the list exactly as its type is. `set_section_numbering` existed for one slice and was
    folded in: two ways to write one field is how they drift."""
    service = _read('gamelists/services/game_list_service.py')
    assert 'def set_section_numbering' not in service

    js = _decommented(_read('static/js/list-detail.js'))
    body = js[js.index('function onNumberingChange('):js.index('function boot(')]
    assert 'updateUrl' in body
    # 'on'/'' is what a checkbox posts and what `safe_bool` reads. A literal 'true' is the bug
    # `is_public` already shipped once.
    assert "'on'" in body


def test_a_group_change_refreshes_and_a_plain_reorder_does_not(client):
    """A card CHANGING GROUP changes things the optimistic repaint cannot reach: the count beside each
    header, and whether the group it left still exists at all (the loose bucket is omitted when empty,
    so emptying it by hand leaves a header reading 0 over nothing).

    A plain reorder must NOT refresh: it would spend a round trip redrawing forty covers that did not
    change and tear down the Sortable instance mid-interaction. The rank text is the only thing a
    reorder alters on screen, which is why `renumber` exists."""
    js = _decommented(_read('static/js/list-detail.js'))

    drop = js[js.index('function onCrossSectionDrop('):js.index('function fullOrder(')]
    assert 'refresh: true' in drop, 'a cross-group drop leaves both section counts stale'

    save = js[js.index('function saveOrder('):js.index('function renumber(')]
    assert 'move.refresh' in save, 'the flag is set and never read'
    # Guarded on the flag, so the ordinary drag keeps its cheap repaint.
    assert 'if (move && move.refresh)' in save

    # The within-grid path sends no move at all, so it cannot ask for one.
    attach = js[js.index('function attachDragTo('):js.index('function onCrossSectionDrop(')]
    reorder_cb = attach[attach.index('onReorder: function'):attach.index('onEnd: function')]
    assert 'refresh' not in reorder_cb, 'a plain reorder must not round-trip the whole grid'


# -- the audit round ------------------------------------------------------------------------------

def test_a_cross_section_drop_reads_where_the_card_LANDED(client):
    """THE WORST BUG THE AUDIT FOUND, and it made the whole feature a no-op.

    SortableJS routes its `end` event to the Sortable the drag STARTED in, not the one that received
    the drop -- the bundle dispatches `add` with `rootEl: parentEl` (the destination) but dispatches
    `end` with `sortable: this` (the source). `utils.js` asserted the opposite in a comment for a long
    time, so `onCrossSectionDrop` read the section id off its OWN container and posted the card
    straight back where it came from: on a Collection the refresh snapped it home, and on a Ranked
    list it wrote the right order with the wrong filing -- the exact "correctly placed and wrongly
    filed" state the single-request design exists to prevent.
    """
    js = _decommented(_read('static/js/list-detail.js'))

    drop = js[js.index('function onCrossSectionDrop('):js.index('function fullOrder(')]
    assert 'evt.to' in drop, 'the drop reports the section it came FROM'
    # And not off the manager's own container, which is the origin.
    assert 'grid.dataset.sectionId' not in drop

    # The shared primitive has to stop telling the next caller the wrong thing.
    utils = _read('static/js/utils.js')
    assert 'the manager whose container the drop landed in' not in utils, \
        'the routing comment that caused this is still there for the next caller to believe'


def test_the_keyboard_can_cross_a_section_boundary(client):
    """A pointer could move a card between headers and a keyboard could not: `onPositionKey` stepped
    to `previousElementSibling` within one grid and bailed at its edge, so a card at the top of
    "Backlog" could not reach "Playing" by any key at all. On a sectioned Collection there was no
    keydown listener bound whatsoever, while the button read "Move games between sections"."""
    js = _decommented(_read('static/js/list-detail.js'))

    key = js[js.index('function onPositionKey('):js.index('function stepIntoNeighbourGrid(')]
    assert 'stepIntoNeighbourGrid(' in key, 'the arrow keys still stop at a section boundary'
    # Crossing a header is a SECTION change, so it must reach the same two writes the drag picks
    # between -- not the reorder endpoint regardless of what the page can honour.
    assert 'saveAssignment(' in key and 'orderingLive(' in key

    attach = js[js.index('function attachDrag('):js.index('function attachDragTo(')]
    assert "document.addEventListener('keydown', onPositionKey)" in attach
    assert 'if (ordering) { document.addEventListener' not in attach, \
        'arrange-only mode has no keyboard again'


def test_the_numbering_repaint_survives_a_lapsed_membership(client):
    """`can_arrange` does not require membership -- a lapsed member keeps arranging, by design -- but
    the numbering CHECKBOX renders only under `can_manage_sections`, which does. Reading the absent
    box as "continue through" repainted every badge into the wrong mode on each drag, and the next
    page load silently put them back."""
    owner, game_list, _items, _first, _second = _sectioned(client)
    svc.update_list(game_list, owner, restart_numbering=True)
    owner.user_is_premium = False
    owner.save(update_fields=['user_is_premium'])

    body = client.get(_url(game_list)).content.decode()

    assert 'data-gl-numbering' not in body, 'the fixture stopped testing what it claims'
    assert 'data-restart-numbering="1"' in body, 'the repaint has no way to know the mode'

    js = _decommented(_read('static/js/list-detail.js'))
    fn = js[js.index('function restartNumbering('):js.index('function rankOf(')]
    assert 'itemsRoot()' in fn, 'the fallback the gated checkbox cannot provide'


def test_the_spoken_rank_matches_the_printed_one(client):
    """`onPositionKey` announced a continue-through number unconditionally, so with restart-numbering
    on a card whose plate read "1" was announced as "number 24 of 40". A sighted owner and a blind one
    were told two different facts about the same move, and the spoken one was unverifiable."""
    js = _decommented(_read('static/js/list-detail.js'))

    # `announceAndSave`, NOT `onPositionKey`. The composing code moved out of the key handler when the
    # boundary-crossing branch needed it too, so slicing the handler asserted against a function that
    # no longer contains the line -- mutation testing caught this assertion passing over the bug it
    # names. `announceAndSave` is defined BELOW `stepIntoNeighbourGrid`, so the slice runs to the end
    # of the file rather than to a marker that sits above it.
    compose = js[js.index('function announceAndSave('):]
    assert 'rankOf(row)' in compose, 'the announcement is not computed in the printed mode'
    assert 'fullOrder().indexOf' not in compose, \
        'the announcement counts straight through regardless of the numbering mode'

    rank = js[js.index('function rankOf('):js.index('function sectionNameFor(')]
    assert 'restartNumbering()' in rank
    # The TOTAL moves with the mode too: "2 of 5" about a list of forty is a different claim, not a
    # smaller one.
    assert 'restart ? rows.length : fullOrder().length' in rank


def test_section_writes_queue_behind_the_arrangement_writes(client):
    """A queued `saveOrder` carries a body captured from the DOM as it was. A section delete that
    lands first leaves that reorder posting a deleted section id: the server refuses, the pill flips
    to "Not saved", and the owner is told a move failed that they never made."""
    js = _decommented(_read('static/js/list-detail.js'))

    for fn in ('onSectionAdd', 'onSectionRename', 'onSectionDelete', 'onNumberingChange'):
        start = js.index('function ' + fn + '(')
        body = js[start:js.index('\n    }', start)]
        assert 'queueSectionWrite(' in body, fn + ' races the drag writes'


def test_the_add_field_keeps_focus_across_its_own_refresh(client):
    """`refreshItems(true)` out-of-band swaps the bar and takes the field with it, so adding two
    sections meant tabbing from the top of the document between them. `restoreRemoveFocus` pays the
    same debt for the remove button."""
    js = _decommented(_read('static/js/list-detail.js'))

    assert 'function restoreSectionFocus(' in js
    settle = js[js.index('function onAfterSettle('):js.index('function wirePositioning(')]
    assert 'restoreSectionFocus()' in settle
    # AFTER the re-wiring, or the field being focused is the node about to be replaced.
    assert settle.index('wireSections()') < settle.index('restoreSectionFocus()')

    # And the remove-focus helper stopped keying on the flat grid's id, which a sectioned list has
    # never rendered -- so focus fell to <body> after every removal there.
    restore = js[js.index('function restoreRemoveFocus('):js.index('function restoreSectionFocus(')]
    assert "'#gl-items-root [data-gl-remove]'" in restore


def test_a_labelled_grid_carries_a_role_that_can_hold_the_label(client):
    """A role-less <div> maps to `generic`, which does not support name-from-author -- so
    `aria-labelledby` on it is dropped and four sections read as four undifferentiated runs of links.
    Only where there IS a label: a role with no accessible name is noise."""
    owner, game_list, _items, first, _second = _sectioned(client)

    body = client.get(_url(game_list)).content.decode()

    assert 'role="group" aria-labelledby="gl-section-' + str(first.id) + '"' in body

    # A FLAT list has no heading to point at, so it gets neither.
    plain = _ranked(owner, 2)
    flat = client.get(_url(plain)).content.decode()
    assert 'role="group"' not in flat and 'aria-labelledby="gl-section' not in flat


def test_the_section_controls_are_real_touch_targets_and_do_not_stick(client):
    """Rename and delete sit ~10px apart. At 28px a mis-tap on "Rename" lands on "Delete the section",
    and an unguarded `:hover` leaves the red wash painted on after the confirm is dismissed -- which
    reads as "the delete is still armed"."""
    css = _read('static/css/components/gamelists.css')

    act = css[css.index('.gl-section__act {'):css.index('.gl-section__head .gl-section__name')]
    assert 'width: 44px; height: 44px;' in act, 'a 28px target between two destructive neighbours'
    assert '@media (hover: hover)' in act, 'the hover state sticks after a tap on touch'
    assert act.index('@media (hover: hover)') < act.index('.gl-section__act:hover')

    # The one control you must hit to use the feature at all was the one under the floor.
    field = css[css.index('.gl-sections__input {'):css.index('.gl-sections__go {')]
    assert 'min-height: 44px' in field


def test_an_empty_grid_is_actually_empty_so_the_drop_box_can_render(client):
    """THE FIX THAT DID NOTHING. The empty-section box is a `::before` gated on `:empty`, and `:empty`
    (Selectors 3, which is what every shipping engine implements) does NOT match an element holding a
    whitespace text node. The tag was laid out over four lines, so an empty grid still contained
    "\n    \n" -- the box never drew, in any state, which is the exact failure it was introduced to
    cure. The first test only asserted the ATTRIBUTE was present, so it passed over an invisible box.
    """
    owner, game_list, _items, _first, _second = _sectioned(client)
    empty = svc.create_section(game_list, owner, name='Backlog')

    body = client.get(_url(game_list)).content.decode()

    # The GRID, not the delete button -- which now carries `data-section-id` as well, so the bare
    # marker matched the button and this test measured the inside of a <button>.
    marker = f'data-gl-arrange data-section-id="{empty.id}"'
    start = body.index(marker)
    open_end = body.index('>', start)
    close = body.index('</div>', open_end)
    between = body[open_end + 1:close]

    assert between == '', f'the grid is not :empty, so the drop box cannot render: {between!r}'


def test_the_empty_box_rule_is_scoped_to_this_features_grid(client):
    """`.pp-gbrowse__grid:empty::before` was site-wide. Any genuinely childless browse grid -- Browse
    Games, a franchise or company page -- would have drawn an 18px dashed box whose
    `attr(data-empty-label)` resolves to the empty string: a bordered blank. It was masked only by the
    whitespace bug above, which the same change removes."""
    css = _read('static/css/components/gamelists.css')

    assert '.gl-group__grid:empty::before' in css
    assert '.pp-gbrowse__grid:empty' not in css, 'the rule still reaches every grid on the site'


def test_a_keyboard_move_across_a_header_keeps_the_card_picked_up(client):
    """Crossing a section ALWAYS refreshes -- the counts and the group membership change -- and the
    refresh tears down and re-attaches the drag, whose `detachDrag` drops the pick SILENTLY. So the
    keyboard path worked exactly once per pick: a second arrow press did nothing, with no announcement
    explaining why, and focus had already fallen to <body>. Walking one card down several headers is
    the gesture the path was added for."""
    js = _decommented(_read('static/js/list-detail.js'))

    key = js[js.index('function onPositionKey('):js.index('function stepIntoNeighbourGrid(')]
    # THE BOUNDARY BRANCH ONLY. Slicing to the end of the function swept in the within-group move
    # below it, which legitimately still focuses the grip -- nothing refreshes there, so that node
    # survives. The first version of this assertion covered both and failed on correct code.
    # ...and it ends where the within-group path BEGINS, not at the first `return;` -- that one is
    # `if (!moved) { return; }` two lines in, which made the slice four words long and the assertion
    # below vacuous in the other direction.
    branch_start = key.index('stepIntoNeighbourGrid(')
    branch = key[branch_start:key.index('if (earlier) { grid.insertBefore(', branch_start)]
    assert 'pendingPickId = row.dataset.itemId' in branch, 'the pick is lost on every crossing'
    assert 'grab.focus()' not in branch, 'focus is put on a row that is about to be replaced'

    assert 'function restorePick(' in js
    settle = js[js.index('function onAfterSettle('):js.index('function wirePositioning(')]
    # AFTER `syncPositioning`, whose `detachDrag` drops any pick -- restoring before it is undone one
    # line later.
    assert settle.index('syncPositioning()') < settle.index('restorePick()')


def test_a_failed_refresh_is_not_reported_as_a_failed_section_write(client):
    """Each handler was `post(...).then(refresh).catch(report)`, and `refreshItems` rejects on a
    4xx/5xx -- so a failed re-render reported "That section could not be added" over a section that
    exists. The owner adds it again, `create_section` does not dedupe names, and now there are two
    identical headers and two of twenty slots spent. `onRemove`, the adder and `saveOrder` all carry
    the inner catch and say the same thing."""
    js = _decommented(_read('static/js/list-detail.js'))

    for fn, nxt in (('onSectionAdd', 'onSectionRename'),
                    ('onSectionRename', 'onSectionDelete'),
                    ('onNumberingChange', 'boot')):
        body = js[js.index('function ' + fn + '('):js.index('function ' + nxt + '(')]
        if 'refreshItems(' in body:
            assert 'refreshItems(true).catch(' in body, \
                fn + ' reports a stale view as a failed write'

    delete_body = js[js.index('function onSectionDelete('):js.index('function onNumberingChange(')]
    assert 'refreshItems(true).catch(' in delete_body


def test_a_doomed_section_stops_being_a_drop_target_at_once(client):
    """The header and its grid stay on screen for the whole delete round trip. A card dropped into
    them in that window queues a reorder carrying a section id that is about to stop existing -- the
    server refuses it, and the owner is told their ORDER could not be saved, which was never the
    problem."""
    js = _decommented(_read('static/js/list-detail.js'))

    body = js[js.index('function onSectionDelete('):js.index('function onNumberingChange(')]
    assert "removeAttribute('data-gl-arrange')" in body, 'a doomed section is still a drop target'
    # BEFORE the write is queued, not in its callback -- the window is the round trip itself.
    assert body.index("removeAttribute('data-gl-arrange')") < body.index('queueSectionWrite(')

    owner, game_list, _items, first, _second = _sectioned(client)
    rendered = client.get(_url(game_list)).content.decode()
    assert f'data-gl-section-delete\n            data-section-id="{first.id}"' in rendered \
        or f'data-section-id="{first.id}"' in rendered, 'the client cannot find the doomed grid'


def test_the_section_field_does_not_steal_a_caret_that_moved_on(client):
    """The refresh settles ~300ms later, and by then the owner may have clicked into the description
    and started typing. `onRemove` guards its own restore the same way and says why."""
    js = _decommented(_read('static/js/list-detail.js'))

    fn = js[js.index('function restoreSectionFocus('):js.index('function restorePick(')]
    assert 'document.activeElement' in fn, 'focus is taken back unconditionally'
    assert 'document.body' in fn


# -- the adder as the toolbar's focal point --------------------------------------------------------

def test_the_adder_is_raised_without_forking_the_shared_field(client):
    """Owner's note, 2026-09: the one control on the page that ADDS anything sat in a grey bar between
    a section title and a sort select, all three the same weight, and read as one more filter.

    RAISED, NOT REBUILT. `.gl-adder` layers over `.pp-bgal__search` rather than replacing it, so the
    geometry, the `/` hint, the clear button and the spinner stay the shared ones. This page already
    carries the scar from the other approach -- a private field that drifted off the shared values on
    every dimension until two search fields on one feature disagreed."""
    owner = _staff(client)
    game_list = _list(owner, 2)

    body = client.get(_url(game_list)).content.decode()

    assert 'class="pp-bgal__search gl-adder"' in body, 'the modifier replaced the shared class'

    css = _read('static/css/components/gamelists.css')
    block = css[css.index('.gl-adder {'):css.index('.gl-adder__panel {')]
    assert 'flex: 1 1 320px' in block, 'it does not take the bar\'s slack'

    # THE REST RULE ONLY. Slicing to the whole block swept in `:hover` and `:focus`, which carry the
    # same accent declaration -- so the assertion passed with the resting accent removed, which is
    # precisely the state it exists to protect. A field that only lights up once you touch it cannot
    # be the thing your eye lands on first.
    rest = block[block.index('.gl-adder input {'):block.index('.gl-adder input::placeholder')]
    assert 'border-color: color-mix(in oklab, var(--pp-primary)' in rest, \
        'the accent is focus-only again, so the adder is quiet until you touch it'
    assert 'background: color-mix(in oklab, var(--pp-primary)' in rest
    # The 44px floor every other control in this feature's bar carries.
    assert 'min-height: 44px' in rest


def test_the_adder_glyph_says_add_rather_than_search(client):
    """Searching is the means here; adding is the act, and a leading icon should say what pressing the
    thing achieves. The magnifier is what made it read as a filter."""
    owner = _staff(client)
    game_list = _list(owner, 1)

    body = client.get(_url(game_list)).content.decode()
    adder = body[body.index('class="pp-bgal__search gl-adder"'):body.index('id="gl-adder-input"')]

    assert 'M12 8v8M8 12h8' in adder, 'the plus glyph is not there'
    # The magnifier's circle+handle pair, which is what it replaced.
    assert 'x1="21" y1="21"' not in adder, 'the field still leads with a magnifier'


# -- previewing the non-member render --------------------------------------------------------------

def test_a_free_owner_gets_no_section_controls_but_is_told_they_exist(client):
    """WHAT A NON-MEMBER ACTUALLY SEES, asserted rather than reasoned about.

    THIS TEST DID NOT DO ITS JOB and the reason is worth keeping. It was written to record that a free
    owner saw no trace of sections anywhere, and its own comment promised that "if an upsell line is
    ever added here, this is the assertion that fails and asks for the decision". The upsell was added
    and it passed: the hook list named `gl-sections__locked`, that class was deleted in the SAME
    change, and the block that replaced it is called `gl-lockup`. A negative assertion over a list of
    names cannot notice a name that did not exist when it was written, so it quietly became a test of
    nothing while still claiming, in its title, to guard the opposite of what now ships.

    The fix is to assert the POSITIVE alongside the negatives: the controls are absent AND the CTA is
    present. A test that says what should be there fails when that stops being true; a test that only
    lists what should not be there passes by default forever."""
    owner = _staff(client, psn='free')

    # 1. A ranked list: the arrange bar renders (arranging is ungated) with no section controls.
    ranked = _ranked(owner, 3)
    ranked_body = client.get(_url(ranked)).content.decode()
    assert 'data-gl-positions' in ranked_body, 'a free owner lost the arrange bar'

    # 2. A collection with no sections: no bar at all, which is the case the CTA had to live outside
    #    the bar to reach.
    collection = _list(owner, 3)
    collection_body = client.get(_url(collection)).content.decode()
    assert 'data-gl-positions' not in collection_body

    for page in (ranked_body, collection_body):
        # No CONTROL they cannot use...
        for hook in ('gl-section__head', 'data-gl-section-add', 'data-gl-numbering',
                     'data-gl-section-rename'):
            assert hook not in page, f'a free owner is shown {hook} after all'
        # ...and the OFFER, which is the half that has to be asserted positively.
        assert 'gl-lockup' in page, 'the perk is invisible to the hunter who might buy it'
        assert 'Group this list into sections' in page


def test_the_preview_renders_the_page_as_a_non_member_sees_it(client):
    """`?preview=lists-free`. Sections are the one membership-gated thing on this page, so a single
    flag is the whole surface."""
    owner = _member(client, psn='member')
    game_list = _ranked(owner, 3)
    svc.create_section(game_list, owner, name='Playing')

    normal = client.get(_url(game_list))
    assert normal.context['can_manage_sections'] is True
    assert 'data-gl-section-add' in normal.content.decode()

    preview = client.get(_url(game_list) + '?preview=lists-free')

    assert preview.context['free_preview'] is True
    assert preview.context['can_manage_sections'] is False
    body = preview.content.decode()
    assert 'data-gl-section-add' not in body, 'the member control survived the preview'
    assert 'data-gl-section-rename' not in body
    # EVERYTHING UNGATED STAYS. A preview that also withdrew the ungated controls would answer a
    # different question than the one it is asked.
    assert 'data-gl-section-delete' in body, 'deleting your own section is not the gated act'
    assert preview.context['can_arrange'] is True
    assert 'gl-lockup' in body, 'the lapsed-member line is part of what they see'


def test_the_preview_announces_itself(client):
    """For a free owner with no sections the honest answer is that NOTHING appears, which is
    indistinguishable from a broken preview without a line saying so."""
    owner = _member(client, psn='member')
    game_list = _list(owner, 2)

    body = client.get(_url(game_list) + '?preview=lists-free').content.decode()

    assert 'gl-preview' in body
    assert 'Previewing as a non-member' in body
    # The exit link is the page's own address: no state is written by a preview, so leaving one is
    # just dropping the querystring.
    assert f'href="/community/lists/{game_list.id}/"' in body


def test_the_preview_door_is_team_only(client):
    """Several of these doors bypass the gate they exist to preview, so the querystring cannot be
    something anybody can type. A non-member who typed it would otherwise be shown the gate they are
    already behind, which is harmless -- but a MEMBER typing it silently loses their own controls and
    reports it as a bug."""
    owner = ProfileFactory(is_linked=True, psn_username='plain')
    owner.user_is_premium = True
    owner.save(update_fields=['user_is_premium'])
    client.force_login(owner.user)

    game_list = _list(owner, 2)
    svc.create_section(game_list, owner, name='Playing')

    resp = client.get(_url(game_list) + '?preview=lists-free')

    assert resp.context['free_preview'] is False, 'a non-team hunter opened the door'
    assert resp.context['can_manage_sections'] is True
    assert 'gl-preview' not in resp.content.decode()


def test_the_preview_writes_nothing(client):
    """The property that makes it safe to hand to somebody and say "just add this to the URL"."""
    owner = _member(client, psn='member')
    game_list = _ranked(owner, 2)
    section = svc.create_section(game_list, owner, name='Playing')
    before = game_list.updated_at

    client.get(_url(game_list) + '?preview=lists-free')

    game_list.refresh_from_db()
    section.refresh_from_db()
    owner.refresh_from_db()
    assert game_list.updated_at == before
    assert section.name == 'Playing'
    assert owner.user_is_premium is True, 'the preview touched the real membership flag'
    assert GameListSection.objects.filter(game_list=game_list).count() == 1


def test_the_preview_door_is_the_shared_one(client):
    """An anti-drift test walks the tree and fails any module that hand-rolls `GET.get('preview')` --
    a door that opens only half of a thing is not a preview, and copies of the gate drift. This is the
    local half of that rule, so the coupling is visible from here too."""
    source = _read('gamelists/views.py')

    # THE POSITIVE ONLY. The first version asserted the hand-rolled read was ABSENT, and matched the
    # comment explaining the rule instead of any code -- `_decommented` in this file strips JS
    # comments, not Python ones, so it could not have helped either. The negative is already owned,
    # tree-wide, by `test_every_team_preview_door_is_the_same_door`; a second, weaker copy of it here
    # is exactly the duplication that guard exists to prevent.
    assert 'from core.previews import previewing' in source
    assert "previewing(self.request, 'lists-free')" in source


# -- the sections CTA ------------------------------------------------------------------------------

def test_a_free_owner_is_told_sections_exist_and_what_adds_them(client):
    """THE GAP THIS CLOSES. A free owner whose list had never had a section saw no trace of the
    feature anywhere on the page, so the perk was invisible to exactly the hunter who might buy it."""
    owner = _staff(client, psn='free')
    game_list = _list(owner, 3)

    body = client.get(_url(game_list)).content.decode()

    assert 'gl-lockup' in body
    assert 'Group this list into sections' in body
    # SCOPED TO THE BLOCK. The navbar and the footer both link to /support/ on every page, so a bare
    # `href="/support/" in body` was true with the CTA's own link deleted -- it was asserting that
    # the site chrome exists.
    block = body[body.index('gl-lockup'):body.index('</div>', body.index('gl-lockup__go'))]
    assert f'href="{reverse("support_hub")}"' in block, 'the CTA has no way to act on'
    assert 'See membership' in block

    # ON A COLLECTION WITH NO SECTIONS, which is the case the old line could not reach at all: the
    # arrange bar does not render here, so anything inside it was invisible.
    assert 'data-gl-positions' not in body, 'the fixture stopped testing the hard case'


def test_the_cta_is_outside_the_bar_that_hides_itself(client):
    """The arrange bar is `hidden` until the identity editor is opened. A CTA inside it would be
    behind a control the hunter has no reason to press, which is where the line it replaced lived."""
    owner = _staff(client, psn='free')
    game_list = _ranked(owner, 3)

    body = client.get(_url(game_list)).content.decode()

    bar_start = body.index('<div class="gl-positions"')
    bar_end = body.index('</div>', body.index('data-gl-positions-status'))
    assert body.index('gl-lockup') > bar_end, 'the CTA is inside the bar that hides itself'
    assert bar_start < bar_end


def test_a_lapsed_member_is_told_what_they_keep_first(client):
    """Two states of ONE block. Somebody who has never had sections needs to be told what they are;
    somebody whose membership lapsed needs to be told what they keep -- and on a list that visibly HAS
    sections, a rename control quietly going missing reads as a bug rather than a membership change."""
    owner, game_list, _items, _first, _second = _sectioned(client)
    owner.user_is_premium = False
    owner.save(update_fields=['user_is_premium'])

    body = client.get(_url(game_list)).content.decode()

    assert 'Your sections are still here' in body
    assert 'Group this list into sections' not in body, 'both states rendered at once'

    # THE OTHER DIRECTION, which is what actually pins the branch: a free owner with NO sections must
    # get the other copy. Asserting only that the lapsed page lacks the never-had copy passes with the
    # condition hard-wired to the lapsed branch.
    client.logout()
    newcomer = _staff(client, psn='newcomer')
    plain = _list(newcomer, 2)
    other = client.get(_url(plain)).content.decode()
    assert 'Group this list into sections' in other
    assert 'Your sections are still here' not in other

    # The 44px floor every control this feature renders carries, including the one in the offer.
    css = _read('static/css/components/gamelists.css')
    go = css[css.index('.gl-lockup__go {'):css.index('@media (max-width: 519px)',
                                                    css.index('.gl-lockup__go {'))]
    assert 'min-height: 44px' in go
    # Their sections and the ungated controls are all still there, which is what the copy promises.
    assert 'gl-section__head' in body
    assert 'data-gl-section-delete' in body


def test_the_cta_is_never_shown_to_somebody_it_cannot_help(client):
    """Four negatives, and the unlinked one is the one worth having: an unlinked owner also fails
    `can_manage_sections`, and selling them a membership answers a question they did not ask. What
    stands between them and sections is linking a PSN account."""
    owner = _member(client, psn='member')

    # 1. A MEMBER, who already has them.
    member_list = _list(owner, 2)
    assert 'gl-lockup' not in client.get(_url(member_list)).content.decode()

    # 2. A FREE owner's EMPTY list: sections group games, and this would be selling a way to
    #    organise nothing. The owner has to be free, or the CTA is absent for the membership reason
    #    and this proves nothing about `bool(items)` -- which is how it first passed with that clause
    #    deleted.
    client.logout()
    pauper = _staff(client, psn='pauper')
    empty = _list(pauper, 0)
    assert 'gl-lockup' not in client.get(_url(empty)).content.decode(), \
        'a free owner of an empty list was sold a way to organise nothing'
    populated = _list(pauper, 2, name='Has games')
    assert 'gl-lockup' in client.get(_url(populated)).content.decode(), \
        'the premise: the same owner DOES get it once there are games'

    # 3. A READER, on somebody else's public list.
    free = _staff(client, psn='free')
    theirs = _list(free, 2, public=True)
    client.logout()
    assert 'gl-lockup' not in client.get(_url(theirs)).content.decode()

    # 4. An UNLINKED owner. Built while linked, because `create_list` refuses otherwise.
    unlinked = _staff(client, psn='unlinked')
    their_list = _list(unlinked, 2)
    unlinked.is_linked = False
    unlinked.save(update_fields=['is_linked'])
    resp = client.get(_url(their_list))
    assert resp.context['is_owner'] is True, 'the fixture stopped testing what it claims'
    assert 'gl-lockup' not in resp.content.decode(), 'an unlinked owner was sold a membership'


def test_the_cta_runs_no_per_user_data_path(client):
    """CLAUDE.md's premium-preview rule, which exists because a locked UI twice ran its real data path
    for people who could not use it. This is a flag, a heading and a link: rendering it must cost the
    same as not rendering it."""
    free = _staff(client, psn='free')
    game_list = _list(free, 4)

    client.get(_url(game_list))  # warm whatever the first render of a page fills
    with CaptureQueriesContext(connection) as locked:
        client.get(_url(game_list))

    free.user_is_premium = True
    free.save(update_fields=['user_is_premium'])
    client.get(_url(game_list))  # ...and again for the other branch
    with CaptureQueriesContext(connection) as unlocked:
        resp = client.get(_url(game_list))

    assert 'gl-lockup' not in resp.content.decode(), 'the premise: the member render has no CTA'
    assert len(locked.captured_queries) == len(unlocked.captured_queries), \
        'the CTA costs queries a member does not pay'


def test_the_preview_shows_the_cta_a_free_owner_would_get(client):
    """The whole point of the door: `can_manage_sections` going false has to carry the CTA with it, or
    the preview shows an absence rather than the real render."""
    owner = _member(client, psn='member')
    game_list = _list(owner, 2)

    body = client.get(_url(game_list) + '?preview=lists-free').content.decode()

    assert 'gl-lockup' in body
    assert 'Group this list into sections' in body


# -- the adder typeahead ---------------------------------------------------------------------------

def test_the_typeahead_reads_only_the_results_it_is_about(client):
    """CLAUDE.md's whale rule, on the one surface here with no cap at all.

    This read every `concept_id` on the list into a Python set on EVERY KEYSTROKE:
    `list(qs.values_list(...))` followed by Python membership, the third anti-pattern the rule names.
    Lists are uncapped by design and the view's own comment says one account can build a 50,000-item
    list, so the cost scaled with the hunter's list while the cache above protected only the
    catalogue half of the answer.

    ASSERTED ON ROWS FETCHED, not on query count -- both implementations issue exactly one query, so
    counting them cannot tell them apart. What differs is how much that one query returns, which is
    the whole defect."""
    owner = _staff(client)
    game_list = _list(owner, 0)

    # One game the search will match, and a pile that it will not. The pile is what an unbounded read
    # drags back; a bounded one never sees it.
    wanted = ConceptFactory(unified_title='Findable Quest')
    GameFactory(concept=wanted, title_platform=['PS5'])
    svc.add_concept(game_list, owner, wanted)
    for n in range(30):
        other = ConceptFactory(unified_title=f'Unrelated {n:03d}')
        GameFactory(concept=other, title_platform=['PS5'])
        svc.add_concept(game_list, owner, other)

    url = reverse('list_game_search', args=[game_list.id])
    client.get(url, {'q': 'Findable'})  # warm the catalogue cache, which is a separate concern

    with CaptureQueriesContext(connection) as ctx:
        resp = client.get(url, {'q': 'Findable'})

    assert resp.status_code == 200
    results = resp.json()['results']
    assert [r['title'] for r in results] == ['Findable Quest']
    assert results[0]['already_added'] is True, 'the answer itself is wrong'

    # THE MEMBERSHIP QUERY, found by its shape rather than by position so a re-ordering of the view
    # does not silently retarget this.
    member_reads = [q for q in ctx.captured_queries
                    if 'gamelists_gamelistitem' in q['sql'] and 'concept_id' in q['sql']]
    assert len(member_reads) == 1, f'expected one membership read, got {len(member_reads)}'
    # Bounded BY THE RESULTS: the id of the one row being rendered appears in the WHERE clause, so
    # the database returns at most that many rows rather than all thirty-one.
    assert 'IN (' in member_reads[0]['sql'], \
        'the membership check reads the whole list rather than the page of results'


def test_the_typeahead_answer_does_not_change_with_the_bound(client):
    """The bound is a performance change and must not be a behaviour change. Two games on the list,
    two matches in the results, one of each state."""
    owner = _staff(client)
    game_list = _list(owner, 0)

    on_list = ConceptFactory(unified_title='Zebra Alpha')
    GameFactory(concept=on_list, title_platform=['PS5'])
    svc.add_concept(game_list, owner, on_list)

    off_list = ConceptFactory(unified_title='Zebra Bravo')
    GameFactory(concept=off_list, title_platform=['PS5'])

    resp = client.get(reverse('list_game_search', args=[game_list.id]), {'q': 'Zebra'})

    marks = {r['title']: r['already_added'] for r in resp.json()['results']}
    assert marks == {'Zebra Alpha': True, 'Zebra Bravo': False}


def test_one_hunters_list_never_marks_anothers_results(client):
    """The catalogue half is cached on the query alone and is the same for everybody; the membership
    half is per-list and applied AFTER the cache. Bounding it must not have moved it inside."""
    first = _staff(client, psn='first')
    theirs = _list(first, 0)
    shared_game = ConceptFactory(unified_title='Shared Title')
    GameFactory(concept=shared_game, title_platform=['PS5'])
    svc.add_concept(theirs, first, shared_game)

    mine_marked = client.get(reverse('list_game_search', args=[theirs.id]),
                             {'q': 'Shared'}).json()['results']
    assert mine_marked[0]['already_added'] is True

    client.logout()
    second = _staff(client, psn='second')
    empty = _list(second, 0)

    # Same query, so the catalogue cache is warm from the request above.
    theirs_marked = client.get(reverse('list_game_search', args=[empty.id]),
                               {'q': 'Shared'}).json()['results']
    assert [r['title'] for r in theirs_marked] == ['Shared Title'], 'the cache stopped working'
    assert theirs_marked[0]['already_added'] is False, "one hunter's list leaked into another's"


# -- the size cap ----------------------------------------------------------------------------------

def test_a_list_stops_accepting_games_at_the_cap(client, monkeypatch):
    """Owner's call, 2026-09-14. Lists were uncapped because the system this replaced gave members
    unlimited games per list, and that argument belonged to the old membership system.

    Patched down rather than building 201 games: the boundary is what is under test, and the real
    number would push this into minutes."""
    from gamelists.services import game_list_service as service

    # `service.MAX_ITEMS_PER_LIST`, not the model's: the service imports the name, so that binding is
    # what `add_concept` actually reads. `monkeypatch` restores it even when an assertion raises.
    monkey = 3
    monkeypatch.setattr(service, 'MAX_ITEMS_PER_LIST', monkey)
    owner = _staff(client)
    game_list = _list(owner, 0)
    for n in range(monkey):
        svc.add_concept(game_list, owner, ConceptFactory(unified_title=f'Fits {n}'))

    with pytest.raises(svc.ListError) as refusal:
        svc.add_concept(game_list, owner, ConceptFactory(unified_title='One too many'))

    # The message NAMES the cap and offers the way out, rather than saying "no".
    assert str(monkey) in str(refusal.value)
    assert 'another list' in str(refusal.value).lower()
    assert game_list.items.count() == monkey


def test_the_cap_counts_rows_rather_than_the_drifting_counter(client, monkeypatch):
    """`game_count` drifts HIGH and nothing repairs it: `GameListItem.concept` is CASCADE, so
    deleting a Concept removes rows with no service involvement and `_recount` only runs from add and
    remove. Capping on that counter would lock a hunter out of a list that has room, permanently,
    with no action that clears it.

    This is the same trap the truncation flag fell into before it was deleted, which is why the
    lesson outlived the code that taught it."""
    from gamelists.services import game_list_service as service

    monkeypatch.setattr(service, 'MAX_ITEMS_PER_LIST', 3)
    owner = _staff(client)
    game_list = _list(owner, 0)
    for n in range(3):
        svc.add_concept(game_list, owner, ConceptFactory(unified_title=f'Fits {n}'))

    # Exactly the drift: a row vanishes without the service, so the counter now overstates.
    game_list.items.first().delete()
    game_list.refresh_from_db()
    assert game_list.game_count == 3, 'the premise: the counter still says three'
    assert game_list.items.count() == 2

    # Two rows under a cap of three has room, and only the stale counter says otherwise.
    svc.add_concept(game_list, owner, ConceptFactory(unified_title='Room for this'))
    assert game_list.items.count() == 3


def test_a_list_already_over_the_cap_keeps_every_row(client, monkeypatch):
    """Enforced on the way IN and never by deletion. A cap that removes somebody's games is the one
    version of this worth regretting, and "no list is over 200 today" is a fact about today rather
    than a guarantee about the shell, the admin or the importer."""
    from gamelists.services import game_list_service as service
    from gamelists import views as gl_views

    owner = _staff(client)
    game_list = _list(owner, 5)

    # The cap arrives AFTER the list already exceeded it, which is the only way this state occurs.
    # BOTH constants, because the render reads its own. Patching only the service's left
    # `MAX_ITEMS_RENDERED` at 200, so "5 rows still render" was true for a list of five no matter what
    # the cap said -- the render half of this test could not fail. They are the same number in
    # production, so patching them apart is the test's job.
    monkeypatch.setattr(service, 'MAX_ITEMS_PER_LIST', 2)
    monkeypatch.setattr(gl_views, 'MAX_ITEMS_RENDERED', 2)
    assert game_list.items.count() == 5

    resp = client.get(_url(game_list))
    # THE SLICE IS WHAT DEFENDS THE RENDER, and this is where that is asserted: an over-cap list is
    # bounded on the page rather than rendered whole. The rows themselves are untouched, which the
    # database assertions below prove.
    assert len(resp.context['items']) == 2, 'the render bound stopped applying'
    assert game_list.items.count() == 5, 'the render bound deleted rows'

    with pytest.raises(svc.ListError):
        svc.add_concept(game_list, owner, ConceptFactory(unified_title='Nope'))
    assert game_list.items.count() == 5, 'a refused add removed something'

    # ...and they can still take one OUT, which is how they get back under.
    svc.remove_concept(game_list, owner, game_list.items.first())
    assert game_list.items.count() == 4


def test_the_cap_and_the_render_bound_are_the_same_number(client):
    """THE WHOLE DESIGN, in one assertion. A list cannot exceed what one page shows, so no list is
    ever truncated, every list is fully reorderable, and section counts are always the real ones.

    Decoupling them re-creates a bug family: while a list could outgrow one render, the page carried
    a truncation notice, `can_reorder` carried a clause for it, the section counts had to be omitted
    rather than shown wrong, and two real defects came out of exactly those branches."""
    from gamelists.models import MAX_ITEMS_PER_LIST
    from gamelists.views import MAX_ITEMS_RENDERED

    assert MAX_ITEMS_RENDERED == MAX_ITEMS_PER_LIST == 200

    # DERIVED, not retyped. Two literals that happen to match today are two literals that drift.
    source = _read('gamelists/views.py')
    assert 'MAX_ITEMS_RENDERED = MAX_ITEMS_PER_LIST' in source


def test_nothing_still_apologises_for_a_truncated_page(client):
    """The state is gone, so its explanations must go with it -- copy that describes something the
    product can no longer do is worse than no copy, because a reader trusts it."""
    owner = _staff(client)
    game_list = _ranked(owner, 3)
    body = client.get(_url(game_list)).content.decode()

    assert 'Showing the first' not in body
    assert 'Reordering needs the whole list on screen' not in body

    # THE CONTEXT KEY, not the bare word. `_decommented` strips JS comments and not Python ones, so a
    # bare search matched the comment that explains why the flag was removed -- a comment is a claim,
    # and here it was failing a test over prose that is worth keeping. The flag itself is what must
    # not exist, and `context['...']` is how it would.
    views = _read('gamelists/views.py')
    assert "context['items_truncated']" not in views, 'the flag survived the state it described'
    assert 'MAX_ITEMS_RENDERED + 1' not in views, 'the extra row that fed the flag is still fetched'
    for template in ('templates/gamelists/partials/detail_items.html',
                     'templates/gamelists/partials/detail_group.html'):
        assert 'items_truncated' not in _read(template), template


def test_the_section_counts_are_the_real_ones_again(client):
    """They were omitted on a truncated render because `items` was a bucket built from a slice, so a
    section holding 300 games on a 400-game list read "12". With the cap equalling the render bound
    the bucket IS the section, so the number is simply true."""
    owner, game_list, _items, first, _second = _sectioned(client)

    body = client.get(_url(game_list)).content.decode()

    head = body[body.index(f'id="gl-section-{first.id}"'):]
    head = head[:head.index('</div>')]
    assert '<span class="gl-section__count">2</span>' in head


def test_the_adder_takes_its_own_row_on_a_phone(client):
    """Owner's call: with the title, the adder and the sort competing for one line at 375px, the adder
    is squeezed to a few characters and the row reads as three equal fragments rather than a label, a
    view control and an action. It drops beneath them instead.

    `order` alone does it, because the shared bar already wraps and `game-browse.css` already gives
    `.pp-gbrowse__sort` `flex: 1 1 auto` below 768 -- so once the adder leaves row one, the sort grows
    to fill it. Restating either here would be a second copy that drifts."""
    css = _read('static/css/components/gamelists.css')

    # 599, not this file's usual 519: the row needs a ~591px VIEWPORT once the gutter and the
    # card padding are taken off, which is the arithmetic the first version got wrong.
    rule = css[css.index('@media (max-width: 599px) {', css.index('.gl-adder input:focus')):]
    rule = rule[:rule.index('}\n}') + 3]
    assert 'order: 1' in rule, 'the adder does not move below the sort'
    assert 'flex: 1 1 100%' in rule, 'it does not take the full row it moved to'


def test_the_phone_layout_cannot_reach_the_browse_toolbars(client):
    """`.pp-gbrowse__bar` is shared with twelve browse surfaces. An unscoped rule here would reorder
    the search field on Browse Games, Companies and the rest -- pages this branch has no business
    touching."""
    css = _read('static/css/components/gamelists.css')
    start = css.index('@media (max-width: 599px) {', css.index('.gl-adder input:focus'))
    rule = css[start:start + 200]

    assert '.gl-toolbar ' in rule, 'the rule is not scoped to this page'

    # ...and the hook is actually rendered, which is the half a CSS-only assertion cannot see.
    owner = _staff(client)
    body = client.get(_url(_list(owner, 2))).content.decode()
    assert 'pp-gbrowse__toolbar gl-toolbar' in body

    # The shared stylesheet stays untouched: this branch must not have edited it to get here.
    shared = _read('static/css/components/game-browse.css')
    assert 'gl-adder' not in shared and 'gl-toolbar' not in shared


def test_the_preview_survives_every_refresh_this_page_makes(client):
    """A DOOR THAT OPENS HALF A THING IS NOT A PREVIEW -- `core/previews.py` says so in its own
    docstring, and this broke exactly that way.

    Every htmx path here rebuilds its querystring from scratch: `refreshItems` from the sort form's
    `hx-get`, `refreshAfterTypeChange` from `window.location.pathname` (which it also rewrites with
    `replaceState`). So the parameter was dropped on the first add, remove, sort or section change,
    the server computed the MEMBER's answer, and the out-of-band chrome swap restored the section
    controls underneath a banner still announcing the non-member render."""
    owner = _member(client, psn='member')
    game_list = _ranked(owner, 3)
    svc.create_section(game_list, owner, name='Playing')

    # The htmx paths that actually re-render the chrome, asked for the way the client asks.
    for params in ({'preview': 'lists-free', 'chrome': '1'},
                   {'preview': 'lists-free', 'sort': 'name'},
                   {'preview': 'lists-free'}):
        resp = client.get(_url(game_list), params, HTTP_HX_REQUEST='true')
        assert resp.context['free_preview'] is True, params
        assert resp.context['can_manage_sections'] is False, params
        assert 'data-gl-section-add' not in resp.content.decode(), params


def test_the_client_carries_the_preview_through_its_own_requests(client):
    """The server half is only half. These are the three places the parameter is re-attached, and the
    hook it is read from."""
    owner = _member(client, psn='member')
    game_list = _list(owner, 2)

    body = client.get(_url(game_list) + '?preview=lists-free').content.decode()
    # ON THE PANEL, which is htmx's swap target and keeps its own attributes -- not on swapped
    # content, and not read from `window.location`, which `replaceState` rewrites.
    assert 'id="gl-items-panel" data-preview="lists-free"' in body
    # The sort form is submitted by htmx directly, so it carries a field rather than a JS hook.
    assert '<input type="hidden" name="preview" value="lists-free" />' in body

    # ...and a member NOT previewing gets neither, so nothing leaks into the normal render.
    plain = client.get(_url(game_list)).content.decode()
    assert 'data-preview' not in plain
    assert 'name="preview"' not in plain

    js = _decommented(_read('static/js/list-detail.js'))
    assert 'function previewParam(' in js
    refresh = js[js.index('function refreshItems('):js.index('function itemsRoot(')]
    assert 'previewParam()' in refresh, 'an add or remove drops the preview'
    # `var TOGGLES`, not the comment banner that used to separate them -- `_decommented` strips
    # exactly that banner before this runs, so the anchor could never be found.
    retype = js[js.index('function refreshAfterTypeChange('):js.index('var TOGGLES')]
    assert 'previewParam()' in retype, 'a type change drops the preview'
    # `replaceState` must not strip it back out of the address bar either -- and this asserts what
    # `cleaned` is BUILT FROM, not merely that a variable by that name reaches `replaceState`. The
    # first version checked the name, so blanking the composition left it passing.
    assert "path + (preview ? '?' + preview : '')" in retype, \
        'a type change rewrites the address bar without the preview, so a reload leaves it'


def test_the_cap_refusal_does_not_name_a_remedy_the_other_cap_refuses(client):
    """"or start another list" is good advice to a member with room and a dead end to a free hunter
    already holding three, who would follow it into a second refusal. The same defect class
    `DeleteListView` was written about: a cap message naming a way out that did not exist."""
    from gamelists.services import game_list_service as service
    from gamelists.models import FREE_MAX_LISTS

    monkeypatch_cap = 2
    owner = _staff(client, psn='free')

    full = _list(owner, 0, name='Full one')
    # Fill the remaining list allowance, so "start another list" is genuinely unavailable.
    for n in range(FREE_MAX_LISTS - 1):
        _list(owner, 0, name=f'Spare {n}')

    import pytest as _pytest
    with _pytest.MonkeyPatch.context() as mp:
        mp.setattr(service, 'MAX_ITEMS_PER_LIST', monkeypatch_cap)
        for n in range(monkeypatch_cap):
            svc.add_concept(full, owner, ConceptFactory(unified_title=f'Fits {n}'))

        with _pytest.raises(svc.ListError) as refusal:
            svc.add_concept(full, owner, ConceptFactory(unified_title='Over'))
        assert 'another list' not in str(refusal.value), \
            'the refusal sends a capped-out hunter into a second refusal'
        assert 'Remove one' in str(refusal.value)

        # ...and a hunter WITH room is still told the useful thing.
        roomy = _member_with_room(client)
        roomy_list = svc.create_list(roomy, name='Theirs')
        for n in range(monkeypatch_cap):
            svc.add_concept(roomy_list, roomy, ConceptFactory(unified_title=f'R{n}'))
        with _pytest.raises(svc.ListError) as second:
            svc.add_concept(roomy_list, roomy, ConceptFactory(unified_title='Over'))
        assert 'another list' in str(second.value)


def _member_with_room(client):
    """A member with one list, so the list-count cap is nowhere near."""
    profile = ProfileFactory(is_linked=True, psn_username='roomy')
    profile.user_is_premium = True
    profile.save(update_fields=['user_is_premium'])
    return profile


def test_the_deletion_sweep_left_nothing_dangling(client):
    """The cap commit removed a context flag, a template block, a `can_reorder` clause and a
    section-count branch. Deletion sweeps are where a dangling reference survives, so this names the
    specific leftovers an audit found rather than trusting a grep done once."""
    views = _read('gamelists/views.py')
    assert "context['items_shown']" not in views, 'a context key with no reader'

    items = _read('templates/gamelists/partials/detail_items.html')
    assert '{% load humanize %}' not in items, 'a load tag with no filter left to serve'

    detail = _read('templates/gamelists/detail.html')
    assert 'The truncation line used to sit HERE' not in detail, \
        'a comment pointing at a block that exists nowhere'

    # THE POSITIVE, because the negative could not survive its own fix: correcting these documents
    # meant QUOTING the reverted claim to say it was reverted, which a "this phrase must not appear"
    # grep then failed on. Asserting that each one names the cap is the version that fails when a
    # document silently goes back to describing an uncapped system.
    for path in ('gamelists/models.py', 'docs/architecture/data-model.md',
                 'docs/features/game-lists.md'):
        assert 'MAX_ITEMS_PER_LIST' in _read(path), f'{path} does not mention the cap at all'


def test_the_adder_actually_wins_the_cascade(client):
    """THE DEFECT THIS TEST EXISTS FOR, and the one its predecessor could not see.

    `game-browse.css` sets `.pp-gbrowse__bar > .pp-bgal__search { flex: 1 1 200px }` -- specificity
    (0,2,0). The prominence rule was a bare `.gl-adder` at (0,1,0), so it LOST, and the adder kept the
    shared basis for as long as the rule existed. Specificity beats source order, and the old
    assertion ("the declaration is in the file") was true the whole time.

    Asserted against the COMPILED bundle, because that is where the question lives. Two conditions
    together are sufficient: the winning rule is at least as specific (two classes), and it comes
    later in the sheet (so an equal-specificity tie breaks our way)."""
    css = _read('static/css/output.css')

    shared = css.index('.pp-gbrowse__bar>.pp-bgal__search{flex:200px}')
    ours = css.index('.gl-toolbar .gl-adder{flex:320px}')

    assert ours > shared, 'the adder rule is compiled BEFORE the shared one and loses the tie'
    # Two classes, matching the shared selector rather than being outranked by it. A single-class
    # selector here is the bug, whatever its source position.
    assert '.gl-toolbar .gl-adder{flex:320px}' in css, 'the rule lost its qualifying class'


def test_the_two_wrapping_bodies_take_the_space_that_is_left(client):
    """`flex-basis: auto` resolves to MAX-CONTENT for flex line-breaking, and `min-width: 0` does not
    reduce it -- it is a lower clamp, not a basis. So both of these asked for ~600-800px, did not fit
    beside their icon at 375px, and broke to their own line: three rows where two were designed, with
    the icon stranded alone on the first."""
    css = _read('static/css/components/gamelists.css')

    for rule in ('.gl-lockup__body { flex: 1 1 0; min-width: 0; }',
                 '.gl-preview__body { flex: 1 1 0; min-width: 0; }'):
        assert rule in css, rule
    assert '.gl-lockup__body { flex: 1 1 auto' not in css
    assert '.gl-preview__body { flex: 1 1 auto' not in css


def test_the_section_controls_hit_areas_do_not_overlap(client):
    """The visible buttons are 28px with a 10px gap, so their centres sit 38px apart while each 44px
    `::before` is 44 wide -- 6px of overlap, won by whichever paints last. That is DELETE, so a thumb
    landing just right of the pencil opened "delete this section"."""
    css = _read('static/css/components/gamelists.css')

    assert '.gl-section__act + .gl-section__act { margin-left: 8px; }' in css, \
        'the two 44px targets overlap again, and the overlap belongs to Delete'


def test_the_phone_reorder_is_withheld_where_nothing_competes(client):
    """The sort form renders only `{% if items %}`, so an empty list's bar holds just the title and
    the adder -- which fit one line at 375px. Reordering there strands "Games" alone on a row above
    the adder, on the one page state where the adder is the entire point."""
    owner = _staff(client)

    empty = client.get(_url(_list(owner, 0))).content.decode()
    assert 'gl-toolbar' not in empty, 'an empty list spends a row on a reorder with nothing to reorder'

    populated = client.get(_url(_list(owner, 2, name='Has games'))).content.decode()
    assert 'pp-gbrowse__toolbar gl-toolbar' in populated
