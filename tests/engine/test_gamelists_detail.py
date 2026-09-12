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

from gamelists.models import LIST_TYPE_RANKED
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

    assert 'Follow' in button
    assert 'Save' not in button

    # No claim of alerts anywhere on the page while there is nowhere for one to arrive.
    for promise in ('notify', 'notified', 'alert', "we'll let you know", 'get updates'):
        assert promise not in body.lower(), f'the page promises {promise!r} with no surface for it'


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


def test_a_truncated_ranked_list_withholds_the_handles_and_says_why(client, monkeypatch):
    """`reorder` refuses a partial order by design, so a page that cannot render the whole list
    cannot post a valid one. Offering a handle here would fail on every single use.

    Patching the ceiling rather than building 201 lists: the boundary is what is under test, and the
    fixture cost of the real number would push this test into minutes.
    """
    from gamelists import views as gl_views
    monkeypatch.setattr(gl_views, 'MAX_ITEMS_RENDERED', 2)

    owner = _staff(client)
    game_list = _ranked(owner, 3)

    resp = client.get(_url(game_list))
    body = resp.content.decode()

    assert resp.context['items_truncated'] is True
    assert resp.context['can_reorder'] is False
    assert 'data-gl-grab' not in body
    assert 'Reordering needs the whole list on screen' in body, \
        'their absence must be explained, or it reads as a bug on the one type built for ordering'


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
    assert 'outline: 2px solid var(--pp-primary)' in picked[:400]
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

    assert 'ArrowUp' in js and 'ArrowDown' in js
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
    assert 'opacity: 0' not in css[css.index('#gl-items-panel:not([data-positioning])'):
                                   css.index('.gl-item__grab {')]


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
    assert 'attachDrag(grid)' in sync, 'a replaced grid must be re-attached while the mode is on'
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

    guard = js[js.index('function onCardClick('):js.index('function syncPositioning(')]
    assert 'if (!positioning) { return; }' in guard, \
        'a click guard that outlives the mode makes the list unclickable'
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
    assert 'delay:' in attach
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
    mode_start = css.index('POSITION-EDITING MODE')
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
    assert '#gl-items-panel[data-positioning] #gl-items {' in mode
    assert 'inset 0 1px 3px' in mode, 'the tray has no recess, so nothing is raised relative to it'
    assert '#gl-items-panel[data-positioning] .gl-item .pp-gcard {' in mode
    lifted = mode[mode.index('#gl-items-panel[data-positioning] .gl-item .pp-gcard {'):]
    assert 'box-shadow' in lifted[:400], 'the cards carry no elevation, only a border'

    # It arrives as a transition, so there is one moment of change and no loop -- and the END state is
    # identical under reduced motion, or the signal would be a feature only some readers receive.
    assert 'transition: background 0.2s ease, box-shadow 0.2s ease' in mode
    assert '#gl-items { transition: none; }' in mode


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
    assert "'?chrome=1'" in refresh or "+ '?chrome=1'" in refresh, 'the refresh must ask for the chrome'
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
    # From the response. Taking it off the radio would record the request rather than the result, and
    # would be wrong for any write the service normalises or refuses.
    assert 'root.dataset.listType = typeField.value' not in handler

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

    # Sliced FORWARD from `reset()`, because `var opener = root.querySelector(` also appears inside
    # `close()` higher up -- searching from 0 found that one, produced an empty slice, and made this
    # test fail for a reason that had nothing to do with what it checks. A mutation run reported it
    # as "killed" on that basis, which is a false pass hiding inside a false failure.
    start = js.index('function reset() {')
    reset_body = js[start:js.index('var opener = root.querySelector(', start)]
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


def test_truncation_is_read_from_the_rows_not_the_drifting_counter(client, monkeypatch):
    """`game_count` drifts HIGH and nothing repairs it -- `GameListItem.concept` is CASCADE, so
    deleting a Concept removes rows without the service. Deriving truncation from that counter meant
    one deleted concept permanently withdrew the drag handles from a six-game ranked list, and
    printed "Reordering needs the whole list on screen" as the reason.
    """
    owner = _staff(client)
    game_list = _ranked(owner, 3)

    # Exactly the drift the service documents: rows gone, counter untouched.
    game_list.items.first().delete()
    assert game_list.game_count == 3, 'the premise: the counter still says three'

    resp = client.get(_url(game_list))

    assert resp.context['items_truncated'] is False, 'a 2-game list is not truncated'
    assert resp.context['can_reorder'] is True, 'counter drift must not withdraw the handles'


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


def test_a_very_long_list_renders_a_bounded_page(client):
    """Query COUNT was already O(1) and would stay O(1) at half a million rows -- the failure mode
    is rows and bytes. List size is uncapped by design, so this number is attacker-controlled."""
    from gamelists.views import MAX_ITEMS_RENDERED

    owner = _staff(client)
    game_list = _list(owner, 0)
    for n in range(MAX_ITEMS_RENDERED + 5):
        concept = ConceptFactory(unified_title=f'Bulk {n:04d}')
        svc.add_concept(game_list, owner, concept)

    resp = client.get(_url(game_list))

    assert len(resp.context['items']) == MAX_ITEMS_RENDERED
    assert resp.context['items_truncated'] is True
    assert 'Showing the first' in resp.content.decode()



# -- the owner's edit controls -------------------------------------------------------------------

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

    # Staff, because every gamelists surface is still behind `_DevelopmentGate`. Without this the
    # request redirects and `owner_body` is '' -- where every `not in` assertion passes vacuously.
    author.user.role = 'admin'
    author.user.save()
    client.force_login(author.user)
    owner_resp = client.get(_url(game_list))
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
    adder = body[body.index('<div class="pp-bgal__search"'):body.index('data-gl-adder-results')]
    assert 'data-search-wrap' in adder
    assert 'pp-search-spin' in adder
    assert 'data-search-clear' in adder

    js = _decommented(_read('static/js/list-detail.js'))
    assert 'wireSearchField(' in js


def test_the_detail_page_does_not_load_the_my_lists_script(client):
    """`gamelists.js` is the My Lists page (a create dialog and a scope switcher). This page loaded
    all of it and used none of it."""
    template = _read('templates/gamelists/detail.html')

    assert 'js/list-detail.js' in template
    assert 'js/gamelists.js' not in template


def test_the_adder_min_query_matches_the_endpoint(client):
    """Two copies of a threshold drift. If the endpoint's floor rises, the client must not keep
    firing requests below it that can only ever return nothing."""
    from gamelists.views import ListGameSearchView

    js = _decommented(_read('static/js/list-detail.js'))
    match = re.search(r'MIN_QUERY\s*=\s*(\d+)', js)

    assert match, 'the adder no longer declares a MIN_QUERY'
    assert int(match.group(1)) == ListGameSearchView.MIN_QUERY


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
    built = (root / 'staticfiles' / 'css' / 'output.css').read_text(encoding='utf-8')

    used = set()
    for path in glob.glob(str(root / 'templates' / 'gamelists' / '**' / '*.html'), recursive=True):
        for attr in re.findall(r'class="([^"]+)"', Path(path).read_text(encoding='utf-8')):
            used.update(c for c in attr.split() if c.startswith('gl-'))

    assert used, 'found no gl-* classes at all -- the scan is broken, not the CSS'
    orphaned = sorted(name for name in used if f'.{name}' not in built)
    assert not orphaned, f'gl-* classes with no rule in the built CSS: {orphaned}'


def test_the_truncation_line_re_renders_with_the_games(client):
    """It sat outside `#gl-items-panel`, so every add and remove left it quoting a stale count next
    to a header tally that HAD just been updated."""
    from gamelists.views import MAX_ITEMS_RENDERED

    owner = _staff(client)
    game_list = _list(owner, 0)
    for n in range(MAX_ITEMS_RENDERED + 3):
        svc.add_concept(game_list, owner, ConceptFactory(unified_title=f'Bulk {n:04d}'))

    # The partial alone -- what an add or remove actually re-renders -- must carry the sentence.
    swapped = client.get(_url(game_list), HTTP_HX_REQUEST='true').content.decode()

    assert 'Showing the first' in swapped


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

    # Not rendered for someone who cannot act.
    author = ProfileFactory(is_linked=True, psn_username='author')
    theirs = _list(author, 1)
    _staff(client, psn='reader')
    assert 'data-gl-status' not in client.get(_url(theirs)).content.decode()


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
    built = _read('staticfiles/css/output.css')

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
    built = _read('staticfiles/css/output.css')

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
    built = _read('staticfiles/css/output.css')

    rule = re.search(r'\.gl-adder__panel\{([^}]*)\}', built)
    assert rule, 'the results panel has no rule in the built CSS'
    declarations = rule.group(1)

    assert 'dvh' in declarations, 'the panel cap does not shrink for the soft keyboard'
    z = re.search(r'z-index:(\d+)', declarations)
    assert z and int(z.group(1)) > 40, f'the panel paints under the mobile tabbar: {declarations}'


def test_the_rename_control_is_a_real_touch_target():
    """26px, in a file that bumps a less consequential control to 44 citing the design system, and
    that explains two hundred lines later why every button needs an explicit `cursor: pointer`."""
    built = _read('staticfiles/css/output.css')

    blocks = re.findall(r'\.gl-edit-open\{([^}]*)\}', built)
    assert blocks, 'the rename control has no rule in the built CSS'
    declarations = ''.join(blocks)

    assert 'width:44px' in declarations, f'the pencil is under the 44px minimum: {declarations}'
    assert 'cursor:pointer' in declarations


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

    built = _read('staticfiles/css/output.css')
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
