"""The rebuilt Game Lists browse page.

Carries forward the assertions `test_lists_browse.py` already wrote for the 2026-08 rebuild, since
those encode work that was done once and should not be re-derived: the query flatness, the HTMX
partial contract, the infinite-scroll branch, the mosaic composition, the privacy rule and the sort
clamp. What changed underneath is the KEYING -- items point at Concepts now, so the covers come from
a batched service rather than a prefetch, and the flatness tests are the ones that notice if that
regresses.

The page is staff-gated while this branch is open (lists ship with the Challenges beta, in one
update), so every request here logs in as staff. That gate is a `_DevelopmentGate` mixin and its
removal is the whole of "turn it on".
"""
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from gamelists.services import game_list_service as svc
from tests.factories import ConceptFactory, GameFactory, ProfileFactory, UserFactory

pytestmark = pytest.mark.django_db

BROWSE = '/community/lists/'


@pytest.fixture
def staff_client(client):
    """Every request in this file. The gate is temporary; the page under it is not."""
    staff = UserFactory()
    staff.role = 'admin'
    staff.save()
    client.force_login(staff)
    return client


def _hunter(psn='curator', premium=True):
    """Premium by default: several tests build more than the free cap of three lists, and the cap
    is the service's own guard rather than something to work around."""
    profile = ProfileFactory(is_linked=True, psn_username=psn)
    if premium:
        profile.user_is_premium = True
        profile.save(update_fields=['user_is_premium'])
    return profile


def _list(owner, games, *, name, public=True):
    game_list = svc.create_list(owner, name=name, is_public=public)
    for _ in range(games):
        concept = ConceptFactory()
        GameFactory(concept=concept, title_platform=['PS5'])
        svc.add_concept(game_list, owner, concept)
    return game_list


#: Tables the page under test is allowed to touch. Counting the WHOLE page instead makes this
#: measure the site chrome too -- and the chrome caches, so an unrelated `art_reveal` lookup fires on
#: the first render and not the second, which showed up as the grid getting FASTER as it grew. A
#: flatness test that moves for reasons outside the page cannot say anything about the page.
#: `trophies_profile` is here because of `select_related('owner')`. Without it, dropping that
#: select_related adds a query PER CARD -- the exact N+1 this file exists to catch -- and every
#: flatness test stays green, because those queries hit a table the filter ignores.
_LIST_TABLES = ('gamelists_', 'trophies_game"', 'trophies_concept', 'trophies_profile')


def _queries(client, url):
    with CaptureQueriesContext(connection) as ctx:
        resp = client.get(url)
    # A redirect runs no page queries, so `few == many` would hold at 0 == 0 and every flatness test
    # here would pass green on a broken gate, a renamed URL or a regressed profile guard. The
    # equivalent guard already existed in `test_lists_hidden` and was not carried across.
    assert resp.status_code == 200, f'{url} answered {resp.status_code}; the count proves nothing'
    return len([
        q for q in ctx.captured_queries
        if any(table in q['sql'] for table in _LIST_TABLES)
    ])


# ── the headline: the page does not scale with its own content ───────────────────────────────────

def test_the_browse_grid_costs_the_same_for_4_lists_as_for_10(staff_client):
    """The original measured 8 queries for 5 lists and 23 for 20, because the tile read a
    `first_game_image` PROPERTY that ran its own query per card while reading like a field. The
    concept re-key could reintroduce exactly that shape -- a cover needs a Game and an item now
    holds a Concept -- so this is the test that guards the re-key, not just the old rebuild."""
    owner = _hunter()
    for i in range(4):
        _list(owner, 3, name=f'List {i}')
    small = _queries(staff_client, BROWSE)

    for i in range(4, 10):
        _list(owner, 3, name=f'List {i}')
    large = _queries(staff_client, BROWSE)

    assert large == small, (
        f'the grid grew from {small} to {large} queries between 4 and 10 lists -- something '
        f'resolves per card again'
    )


def test_a_bigger_list_does_not_cost_the_grid_more(staff_client):
    """The mosaic is bounded to four covers in the SERVICE. Bounding it in the template instead
    would still fetch every item on every list -- a 200-game list loading 200 rows to draw four."""
    owner = _hunter()
    _list(owner, 2, name='Small')
    small = _queries(staff_client, BROWSE)

    _list(owner, 40, name='Huge')
    large = _queries(staff_client, BROWSE)

    assert large == small, f'{small} queries with a 2-game list, {large} once a 40-game one appeared'


def test_the_cover_join_does_not_drag_the_igdb_blob_along(staff_client):
    """`raw_response` is the ~30 KB IGDB blob no cover template reads and the documented trigger for
    the May 2026 OOM. Pulling it back in would not change any query COUNT, so the tests above cannot
    see it -- this reads the SQL."""
    owner = _hunter()
    _list(owner, 3, name='Covered')

    with CaptureQueriesContext(connection) as ctx:
        staff_client.get(BROWSE)

    joined = [q['sql'] for q in ctx.captured_queries if 'igdb' in q['sql'].lower()]
    assert joined, 'the browse page no longer joins the cover chain at all'
    for sql in joined:
        assert 'raw_response' not in sql.lower(), 'the IGDB blob is being fetched for a cover'


# ── it behaves like the other browse pages ───────────────────────────────────────────────────────

def test_htmx_gets_the_grid_and_nothing_else(staff_client):
    owner = _hunter()
    _list(owner, 2, name='Swapped')

    body = staff_client.get(BROWSE, HTTP_HX_REQUEST='true').content.decode()

    assert 'items-grid' in body
    assert '<!doctype html' not in body.lower(), 'the filter swap returned the whole page'
    assert '<nav' not in body.lower()


def test_the_partial_bakes_in_the_reveal_class_and_the_full_page_does_not(staff_client):
    """htmx's settle step restores server attributes on id'd swapped elements, so a class added by
    JS in afterSwap is wiped again and the cards unhide with a flash. It has to be in the markup the
    swap returns, and only there."""
    owner = _hunter()
    _list(owner, 2, name='Revealed')

    full = staff_client.get(BROWSE).content.decode()
    swapped = staff_client.get(BROWSE, HTTP_HX_REQUEST='true').content.decode()

    assert 'pp-reveal' in swapped
    assert 'pp-reveal' not in full


def test_the_infinite_scroller_page_fetch_also_gets_the_partial(staff_client):
    """`InfiniteScroller` sends `X-Requested-With`, NOT an htmx header. A guard that checks only
    `request.htmx` returns a whole document to be appended into the grid."""
    owner = _hunter()
    _list(owner, 2, name='Scrolled')

    body = staff_client.get(
        BROWSE, HTTP_X_REQUESTED_WITH='XMLHttpRequest').content.decode()

    assert 'items-grid' in body
    assert '<!doctype html' not in body.lower()


def test_the_header_count_is_the_one_the_grid_is_showing(staff_client):
    """It used to run a second, unfiltered `COUNT(*)`, so the header disagreed with the grid the
    moment anybody searched.

    Asserted on what is RENDERED. The first version read `context['total_lists']` -- a key no
    template reads, since the header renders `paginator.count` -- and then compared `paginator.count`
    against itself, which `ListView` guarantees by construction. Neither line could have detected the
    regression the docstring names.
    """
    owner = _hunter()
    _list(owner, 1, name='Alpha')
    _list(owner, 1, name='Beta')

    body = staff_client.get(BROWSE, {'q': 'Alpha'}).content.decode()

    assert 'data-countup="1"' in body, 'the header is not showing the filtered count'
    assert 'Beta' not in body


# ── the rules that are not about speed ───────────────────────────────────────────────────────────

def test_a_private_list_is_invisible_to_everyone_else(staff_client):
    """Even to staff on this page: the browse grid is the PUBLIC catalogue, and a staff account
    browsing it is still browsing it."""
    owner = _hunter()
    _list(owner, 1, name='Kept back', public=False)
    _list(owner, 1, name='Shared')

    body = staff_client.get(BROWSE).content.decode()

    assert 'Shared' in body
    assert 'Kept back' not in body


def test_a_junk_sort_falls_back_rather_than_rendering_an_unselected_toolbar(staff_client):
    """Dropping to no ordering is how a browse grid ends up in whatever order the database felt
    like, which looks like a bug and cannot be reproduced."""
    owner = _hunter()
    _list(owner, 1, name='Sorted')

    resp = staff_client.get(BROWSE, {'sort': 'nonsense'})

    assert resp.context['current_sort'] == 'popular'


@pytest.mark.parametrize('sort, expected', [
    ('popular', ['Loved', 'Newest', 'Big']),
    ('most_games', ['Big', 'Newest', 'Loved']),
    ('alpha', ['Big', 'Loved', 'Newest']),
    ('recent', ['Newest', 'Loved', 'Big']),
    ('updated', ['Newest', 'Loved', 'Big']),
])
def test_every_offered_sort_actually_sorts(staff_client, sort, expected):
    """ORDER, not an echo of the parameter.

    The first version asserted `current_sort == sort` and a row count -- the input coming straight
    back out -- and its two same-shaped lists could not have distinguished the orderings anyway, so
    pointing every entry in `_ORDERING` at `-created_at` would have passed all five parametrizations.
    This builds rows that differ on each axis the sorts read and compares the sequence.
    """
    from gamelists.models import GameList

    owner = _hunter()
    big = _list(owner, 4, name='Big')
    loved = _list(owner, 1, name='Loved')
    newest = _list(owner, 2, name='Newest')

    GameList.objects.filter(pk=loved.pk).update(like_count=9)
    GameList.objects.filter(pk=newest.pk).update(like_count=5)
    for pk, day in ((big.pk, '2026-01-01'), (loved.pk, '2026-02-01'), (newest.pk, '2026-03-01')):
        GameList.objects.filter(pk=pk).update(
            created_at=f'{day}T00:00:00+00:00', updated_at=f'{day}T00:00:00+00:00')

    names = [gl.name for gl in staff_client.get(BROWSE, {'sort': sort}).context['game_lists']]

    assert names == expected, f'sort={sort} produced {names}'


def test_alphabetical_sorting_ignores_case(staff_client):
    """Postgres files uppercase before lowercase, so a plain `name` sort puts "apex" after
    "Zenith" -- the house rule is `Lower()` on every front-facing name column."""
    owner = _hunter()
    _list(owner, 1, name='apex predator')
    _list(owner, 1, name='Zenith')

    names = [gl.name for gl in staff_client.get(BROWSE, {'sort': 'alpha'}).context['game_lists']]

    assert names == ['apex predator', 'Zenith']


def test_search_matches_a_list_name_a_description_and_a_creator(staff_client):
    owner = _hunter(psn='searchme')
    svc.create_list(owner, name='Findable', description='nothing special', is_public=True)
    svc.create_list(owner, name='Other', description='a haystack needle here', is_public=True)

    for term, expected in (('Findable', 1), ('needle', 1), ('searchme', 2)):
        found = staff_client.get(BROWSE, {'q': term}).context['game_lists']
        assert len(found) == expected, f'searching {term!r} found {len(found)}'


def test_the_game_count_range_filters_both_ends(staff_client):
    owner = _hunter()
    _list(owner, 1, name='Tiny')
    _list(owner, 5, name='Middling')
    _list(owner, 9, name='Big')

    found = staff_client.get(BROWSE, {'min_games': '3', 'max_games': '7'}).context['game_lists']

    assert [gl.name for gl in found] == ['Middling']


@pytest.mark.parametrize('bad', [
    'lots',
    '-',
    '-5',
    '\u00b2',      # superscript two: isdigit() is True and int() RAISES -- this was a 500
    '\u0663',      # Arabic-Indic three: both agree, but nobody typed it into a game-count box
    '9' * 40,      # larger than the column can hold
])
def test_a_junk_range_is_ignored_rather_than_crashing_or_emptying_the_grid(staff_client, bad):
    """A filter is not a form: somebody arriving on a mangled link should see the grid, not an error.

    The superscript case is the one that mattered. `str.isdigit()` is True for it and `int()` then
    raises, so `?min_games=` with a superscript two was an unhandled ValueError -- a 500 on a public
    browse page, reachable from a query string.
    """
    owner = _hunter()
    _list(owner, 2, name='Still here')

    resp = staff_client.get(BROWSE, {'min_games': bad, 'max_games': bad})

    assert resp.status_code == 200, f'{bad!r} took the page down'
    assert len(resp.context['game_lists']) == 1


# ── the mosaic ───────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('games, expected', [(1, 1), (2, 2), (3, 3), (5, 4)])
def test_the_mosaic_composes_around_however_many_covers_there_are(staff_client, games, expected):
    """`is-N` picks the composition, so a two-game list reads as composed rather than as a four-slot
    grid with holes."""
    owner = _hunter()
    _list(owner, games, name=f'{games} games')

    resp = staff_client.get(BROWSE)

    assert len(resp.context['game_lists'][0].cover_items) == expected
    assert f'pp-gtile__mosaic is-{expected}' in resp.content.decode()


def test_an_empty_list_draws_the_placeholder_rather_than_a_broken_mosaic(staff_client):
    owner = _hunter()
    svc.create_list(owner, name='Nothing yet', is_public=True)

    body = staff_client.get(BROWSE).content.decode()

    assert 'pp-gtile__art--empty' in body
    assert 'pp-gtile__mosaic' not in body


def test_the_tile_uses_the_sites_own_cover_chain(staff_client):
    """The pre-rebuild tile read `game.title_image` directly, which is why lists were the one
    surface showing PSN art where the rest of the site shows the IGDB cover."""
    from pathlib import Path

    tile = (Path(__file__).resolve().parents[2]
            / 'templates' / 'gamelists' / 'partials' / 'list_tile.html').read_text(encoding='utf-8')

    assert 'display_image_url' in tile
    assert 'item.title_image' not in tile


def test_only_controls_that_submit_dim_the_grid():
    """A dim is a promise that something is coming.

    `onFormChangeDim` used to dim for anything that was not a text or search input, which caught this
    toolbar's `min_games` / `max_games` NUMBER inputs -- and `browse-filters.js` auto-submits only
    checkboxes, radios, selects and `[data-auto-submit]`. So changing a game-count value greyed
    `#browse-results` to 40% and no request ever fired to clear it. Live, visible, and inherited
    verbatim from the page this one was ported from.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    js = (root / 'static' / 'js' / 'lists-browse.js').read_text(encoding='utf-8')
    tpl = (root / 'templates' / 'gamelists' / 'browse.html').read_text(encoding='utf-8')

    assert 'type="number"' in tpl, 'the number inputs this guards moved or were renamed'

    dim = js[js.index('function onFormChangeDim'):]
    dim = dim[:dim.index('\n    }')]
    assert "t.type === 'checkbox'" in dim and "t.tagName === 'SELECT'" in dim, (
        'the dim no longer matches what browse-filters.js actually submits'
    )
    assert "'text'" not in dim, 'back to an exclusion list, which is what let number inputs through'


def test_the_browse_scroll_branch_answers_a_real_page_fetch(staff_client):
    """`X-Requested-With` PLUS `?page` -- what the scroller actually sends. The existing test sent
    the header alone, so `_is_scroll_fetch()` was False and the countless branch never ran."""
    # Spread across owners: 26 lists is past any one hunter's cap, and a public catalogue is made of
    # many people's lists anyway.
    for n in range(26):
        _list(_hunter(psn=f'scroller{n}'), 1, name=f'Scrolled {n}')

    first = staff_client.get(BROWSE, {'page': 1}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
    assert first.status_code == 200
    assert first['X-Has-Next'] == '1'

    second = staff_client.get(BROWSE, {'page': 2}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
    assert second['X-Has-Next'] == '0'
    assert '<!doctype html' not in second.content.decode().lower()

    assert staff_client.get(
        BROWSE, {'page': 99}, HTTP_X_REQUESTED_WITH='XMLHttpRequest').status_code == 404



# ── empty states offer a way out ─────────────────────────────────────────────────────────────────

@pytest.fixture
def linked_staff_client(client):
    """Staff (the gate) WITH a linked profile -- "Make a list" is gated on `user.profile`, and the
    file's plain `staff_client` has none, so it could never see that branch."""
    user = UserFactory()
    user.role = 'admin'
    user.save()
    ProfileFactory(user=user, is_linked=True, psn_username='curator')
    client.force_login(user)
    return client


def test_a_narrowed_to_nothing_grid_offers_to_clear_the_filters(linked_staff_client):
    """An empty state that names the situation and offers no action is a dead end. "Try a different
    term" is advice; this is the button that follows it -- a reader should not have to work out
    which of four controls emptied the grid."""
    resp = linked_staff_client.get(BROWSE, {'q': 'zzzznothingmatchesthis'})
    body = resp.content.decode()

    assert resp.context['has_filters'] is True
    assert 'Clear filters' in body
    assert 'Make a list' not in body, 'the wrong action for a filtered grid'


def test_a_genuinely_empty_catalogue_offers_to_start_one(linked_staff_client):
    """The opposite situation needs the opposite answer: nothing to clear, so offer the thing that
    would fill it."""
    resp = linked_staff_client.get(BROWSE)
    body = resp.content.decode()

    assert resp.context['has_filters'] is False
    assert 'Make a list' in body
    assert 'Clear filters' not in body, 'offering to clear filters that are not applied'


def test_junk_filters_do_not_count_as_filters(linked_staff_client):
    """Read through the same parsers the queryset uses. `_count_filter` discards junk, so
    `?min_games=abc` narrows nothing and must not claim a filter is on -- otherwise the empty state
    offers to clear a filter that was never applied."""
    resp = linked_staff_client.get(BROWSE, {'min_games': 'abc'})

    assert resp.context['has_filters'] is False
    assert 'Make a list' in resp.content.decode()


def test_a_signed_in_reader_without_a_profile_is_offered_nothing_they_cannot_do(staff_client):
    """No linked profile means no lists, so the empty state offers no action rather than a button
    that would bounce them to PSN linking."""
    resp = staff_client.get(BROWSE)

    assert 'Make a list' not in resp.content.decode()


def test_the_browse_action_sits_in_the_toolbar_not_the_header(linked_staff_client):
    """The tally slot is a DISPLAY register. A control in it reads at the same optical level as the
    number, so the two compete -- and no other rebuilt header on the site does it. The destination
    belongs at the right end of the control row, which is where every filter toolbar worth copying
    puts it."""
    body = linked_staff_client.get(BROWSE).content.decode()

    header_end = body.index('</section>')
    assert 'My lists' not in body[:header_end], 'the action is back in the header card'
    assert 'My lists' in body[header_end:]
    # And it is inside the toolbar's control row rather than floating between the two.
    bar = body[body.index('pp-gbrowse__bar'):]
    assert 'My lists' in bar[:bar.index('</form>')]
