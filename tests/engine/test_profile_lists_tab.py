"""The profile's Lists tab, rebuilt against `gamelists` (2026-09).

The legacy tab was pulled when the old list system was parked, and the note left in
profile_detail.html asked the revamp to bring its own rather than inherit it. Before it came back
there was no author-scoped view of lists ANYWHERE -- browse filters on text, game count and a follow
scope, never on a person -- so meeting a list and asking who wrote it dead-ended on a profile that
never mentioned lists.

Two rules carry most of the weight here and both are load-bearing rather than tidy:

  PUBLIC ONLY, including on your own profile. `/my-lists/` owns private lists and the management
  surface; this tab answers what a hunter has put OUT.

  THE CHIP IS CONDITIONAL. A door is only offered when there is something behind it.
"""
import re
from pathlib import Path

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from gamelists.models import GameList
from gamelists.services import game_list_service as svc
from tests.factories import ConceptFactory, GameFactory, ProfileFactory

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[2]
CF = {'HTTP_CF_RAY': '8f0000000000abcd-LHR'}
PAGE = Path(ROOT / 'templates' / 'trophies' / 'profile_detail.html')
PANEL = Path(ROOT / 'templates' / 'trophies' / 'partials' / 'profile_detail' / 'tabs' / 'lists_tab.html')

#: The switcher markup only, so a chip assertion cannot be satisfied by the word "Lists" appearing
#: in a tile, a heading, a breadcrumb or the footer. Scoped the way the assertion memo asks: a bare
#: `'Lists' in html` would pass on a page that renders no chip at all.
_TAB_BAR = re.compile(r'<div class="pp-switch".*?</div>', re.S)

#: Django block comments. Source assertions strip these first, because a template's prose routinely
#: NAMES the thing it deliberately does not do -- so an un-stripped check answers about the
#: documentation rather than the markup, and fails on a file that is already correct.
_COMMENT = re.compile(r'\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}', re.S)


def _hunter(psn='curator', premium=True):
    """Premium by default: some cases build more lists than the free cap of three, and that cap is
    the service's own guard rather than something to route around."""
    profile = ProfileFactory(is_linked=True, psn_username=psn)
    if premium:
        profile.user_is_premium = True
        profile.save(update_fields=['user_is_premium'])
    return profile


def _list(owner, *, name, games=2, public=True):
    game_list = svc.create_list(owner, name=name, is_public=public)
    for _ in range(games):
        concept = ConceptFactory()
        # `['PS5']`, a LIST -- `title_platform` is a JSONField and the cover picker ranks on it.
        # A bare string is a shape the schema cannot hold and it hid a four-surface 500 once.
        GameFactory(concept=concept, title_platform=['PS5'])
        svc.add_concept(game_list, owner, concept)
    return game_list


def _profile_url(profile, tab=None):
    url = f'/hunters/{profile.psn_username}/'
    return f'{url}?tab={tab}' if tab else url


def _tab_bar(html):
    """The rendered switcher, or '' when the page drew none (a private profile)."""
    found = _TAB_BAR.search(html)
    return found.group(0) if found else ''


# ── the chip ────────────────────────────────────────────────────────────────────────────────────

def test_a_published_list_earns_the_chip(client):
    hunter = _hunter()
    _list(hunter, name='Cosy platinums')

    html = client.get(_profile_url(hunter), **CF).content.decode()

    assert 'tab=lists' in _tab_bar(html)


def test_no_lists_means_no_chip(client):
    """At launch this is almost every profile. A chip that is empty for everybody is chrome on every
    render plus crowding on a switcher that has to survive 375px."""
    hunter = _hunter()

    html = client.get(_profile_url(hunter), **CF).content.decode()

    bar = _tab_bar(html)
    assert bar, 'the switcher did not render at all; this test would pass vacuously'
    assert 'tab=lists' not in bar


def test_a_private_list_does_not_earn_the_chip(client):
    """The chip advertises PUBLISHED work. Offering it for a private list would open a tab that,
    by the public-only rule below, renders nothing."""
    hunter = _hunter()
    _list(hunter, name='Secret backlog', public=False)

    html = client.get(_profile_url(hunter), **CF).content.decode()

    bar = _tab_bar(html)
    assert bar, 'the switcher did not render at all; this test would pass vacuously'
    assert 'tab=lists' not in bar


def test_the_owner_gets_no_chip_for_their_own_private_list(client):
    """Same rule seen from the inside. The owner's private lists live at My Lists; this tab is not
    a second place to manage them."""
    hunter = _hunter()
    _list(hunter, name='Secret backlog', public=False)
    client.force_login(hunter.user)

    html = client.get(_profile_url(hunter), **CF).content.decode()

    bar = _tab_bar(html)
    assert bar, 'the switcher did not render at all; this test would pass vacuously'
    assert 'tab=lists' not in bar


def test_a_soft_deleted_list_does_not_earn_the_chip(client):
    hunter = _hunter()
    game_list = _list(hunter, name='Gone')
    svc.delete_list(game_list, hunter)

    html = client.get(_profile_url(hunter), **CF).content.decode()

    bar = _tab_bar(html)
    assert bar, 'the switcher did not render at all; this test would pass vacuously'
    assert 'tab=lists' not in bar


# ── the wall ────────────────────────────────────────────────────────────────────────────────────

def test_the_tab_renders_this_hunters_published_lists(client):
    hunter = _hunter()
    _list(hunter, name='Cosy platinums')

    html = client.get(_profile_url(hunter, 'lists'), **CF).content.decode()

    assert 'Cosy platinums' in html
    assert 'id="lists-grid"' in html


def test_the_tab_shows_only_this_hunters_lists(client):
    """The tab is author-scoped; that is the entire reason it exists. A queryset that forgot the
    owner filter would render the whole site's lists on everybody's profile and still look right on
    a one-hunter fixture."""
    hunter = _hunter(psn='curator')
    stranger = _hunter(psn='passerby')
    _list(hunter, name='Mine to show')
    _list(stranger, name='Somebody elses')

    html = client.get(_profile_url(hunter, 'lists'), **CF).content.decode()

    assert 'Mine to show' in html
    assert 'Somebody elses' not in html


def test_the_owner_does_not_see_their_private_lists_here(client):
    """PUBLIC ONLY EVEN FOR YOU. If this ever flips, the tab becomes a second management surface for
    the same objects and My Lists stops being the one place they live."""
    hunter = _hunter()
    _list(hunter, name='Out in the world')
    _list(hunter, name='Still a draft', public=False)
    client.force_login(hunter.user)

    html = client.get(_profile_url(hunter, 'lists'), **CF).content.decode()

    assert 'Out in the world' in html
    assert 'Still a draft' not in html


def test_a_soft_deleted_list_is_gone_from_the_wall(client):
    hunter = _hunter()
    _list(hunter, name='Kept')
    doomed = _list(hunter, name='Deleted one')
    svc.delete_list(doomed, hunter)

    html = client.get(_profile_url(hunter, 'lists'), **CF).content.decode()

    assert 'Kept' in html
    assert 'Deleted one' not in html


def test_a_moderated_list_shows_the_placeholder_not_its_words(client):
    """`text_hidden` has to hide the words on EVERY surface that renders a list, not just the two it
    was built for. A new grid that reads `name` directly re-opens the hole on a page the moderator
    never thinks to check."""
    hunter = _hunter()
    game_list = _list(hunter, name='Something unrepeatable')
    GameList.objects.filter(pk=game_list.pk).update(text_hidden=True)

    html = client.get(_profile_url(hunter, 'lists'), **CF).content.decode()

    assert 'Something unrepeatable' not in html
    assert GameList.HIDDEN_NAME in html


# ── privacy ─────────────────────────────────────────────────────────────────────────────────────

def test_a_private_history_profile_answers_nothing_over_htmx(client):
    """The documented bug class on this page: an HTMX request is answered with the tab template
    DIRECTLY, which never renders the parent's `{% if profile.psn_history_public %}`. Every tab that
    forgot this returned a private hunter's data to anyone who asked for it by name."""
    hunter = _hunter()
    hunter.psn_history_public = False
    hunter.save(update_fields=['psn_history_public'])
    _list(hunter, name='Cosy platinums')

    resp = client.get(_profile_url(hunter, 'lists'), HTTP_HX_REQUEST='true', **CF)

    # The status check is not ceremony, and the sibling file learned it the hard way: without it a
    # 404 -- a renamed route, a changed factory -- satisfies "the list is absent" while proving
    # nothing, which is how the guard it replaced passed for the whole time the tab was live.
    assert resp.status_code == 200, f'answered {resp.status_code}, so this proves nothing'
    assert 'Cosy platinums' not in resp.content.decode()


def test_a_private_history_profile_answers_nothing_on_the_full_page_either(client):
    """The HTMX path is the one that has bitten before, so it gets its own test above -- but a guard
    that pins only the interesting path leaves the ordinary one to be broken silently."""
    hunter = _hunter()
    hunter.psn_history_public = False
    hunter.save(update_fields=['psn_history_public'])
    _list(hunter, name='Cosy platinums')

    resp = client.get(_profile_url(hunter, 'lists'), **CF)

    assert resp.status_code == 200, f'answered {resp.status_code}, so this proves nothing'
    assert 'Cosy platinums' not in resp.content.decode()


def test_the_htmx_swap_answers_with_the_panel_not_the_whole_document(client):
    """The nesting bug this page keeps re-learning: `get_template_names` had no default, so a slug
    missing from both template maps fell through to the FULL PAGE, which htmx then swapped into the
    tab panel -- a complete document inside a card grid, and the InfiniteScroller appended a second
    copy on the next scroll. `lists` walked into it once already, during the window when its map
    entry had been removed."""
    hunter = _hunter()
    _list(hunter, name='Cosy platinums')

    resp = client.get(_profile_url(hunter, 'lists'),
                      HTTP_HX_REQUEST='true', HTTP_HX_TARGET='tab-content', **CF)
    body = resp.content.decode()

    assert resp.status_code == 200
    assert 'Cosy platinums' in body, 'the panel did not render; the checks below are vacuous'
    assert '<!doctype html' not in body.lower(), 'the swap answered with the entire page'
    assert '<nav' not in body.lower(), 'the swap nested the site chrome inside the tab panel'


def test_the_wall_never_hauls_the_igdb_blob(client):
    """CLAUDE.md's standing rule for any queryset that joins `igdb_match` for cover art:
    `.defer('concept__igdb_match__raw_response')`. That column is the ~30 KB API blob no cover
    template reads and the direct trigger for the May 2026 web-server OOM, and this tab is the
    newest surface to join that relation.

    The existing profile guard claims to cover "any tab" but only exercises `?tab=ratings`.
    """
    hunter = _hunter()
    _list(hunter, name='Cosy platinums')

    with CaptureQueriesContext(connection) as captured:
        resp = client.get(_profile_url(hunter, 'lists'), **CF)

    assert resp.status_code == 200
    hauling = [q['sql'] for q in captured if 'raw_response' in q['sql']]
    assert not hauling, f'the lists wall selected the IGDB blob: {hauling}'


def test_no_chip_means_the_tab_falls_back_rather_than_orphaning_the_switcher(client):
    """A hand-typed `?tab=lists` on a hunter with nothing published used to select a tab that has no
    chip: every chip `aria-selected="false"`, nothing `is-active`, and `slideViewIn` handed a slug
    its order array does not contain. A switcher with nothing selected reads as broken.

    Same path a STALE chip takes -- unpublished, deleted or moderated between the render that drew
    it and the click -- which is the reachable half.
    """
    hunter = _hunter()
    _list(hunter, name='Secret backlog', public=False)

    html = client.get(_profile_url(hunter, 'lists'), **CF).content.decode()

    assert 'id="lists-grid"' not in html, 'the orphan lists panel rendered'
    assert 'id="games-grid"' in html, 'it did not fall back to Games'
    assert 'is-active' in _tab_bar(html), 'the switcher has no chip selected'


def test_a_private_history_profile_draws_no_chip(client):
    """What ENFORCES this is the template's `{% if profile.psn_history_public %}` around the whole
    switcher, not the view's chip gate -- mutation-checked, and the view half survives on its own.
    So this pins the user-visible rule and the next test pins the view half separately."""
    hunter = _hunter()
    hunter.psn_history_public = False
    hunter.save(update_fields=['psn_history_public'])
    _list(hunter, name='Cosy platinums')

    html = client.get(_profile_url(hunter), **CF).content.decode()

    assert 'tab=lists' not in html


def test_a_private_history_profile_costs_no_list_query(client):
    """The other half of the chip gate, and the only thing that makes `_history_visible` load-bearing
    rather than decorative: the switcher is not rendered for this profile at all, so asking whether
    to draw a chip on it is a question nothing can read the answer to.

    Asserts on the TABLE rather than a query count. A number drifts as the rest of the page changes
    and would have to be re-tuned by whoever breaks it, which is how a count assertion turns into a
    rubber stamp; "this table is not touched" stays true no matter what else the page learns to do.
    """
    hunter = _hunter()
    hunter.psn_history_public = False
    hunter.save(update_fields=['psn_history_public'])
    _list(hunter, name='Cosy platinums')

    with CaptureQueriesContext(connection) as captured:
        resp = client.get(_profile_url(hunter), **CF)

    # Same reason as above, and sharper here: a 404 issues almost no queries at all, so "this table
    # was not touched" would be trivially true on a page that never rendered.
    assert resp.status_code == 200, 'the profile did not render; the query assertion is vacuous'
    touched = [q['sql'] for q in captured if 'gamelists_gamelist' in q['sql']]
    assert not touched, f'a private profile still queried the list tables: {touched}'


# ── shape ───────────────────────────────────────────────────────────────────────────────────────

def test_the_tiles_carry_their_cover_mosaic(client):
    """The covers are the thing a reader scans to recognise a list, and their absence is SILENT:
    without `cover_items` the tile falls through to its no-art branch, which renders a tidy glyph,
    issues no extra queries and looks deliberate. The flatness test below cannot see that -- dropping
    the cover service entirely leaves the query count unchanged -- so this is the half that notices.
    """
    hunter = _hunter()
    _list(hunter, name='Cosy platinums', games=2)

    html = client.get(_profile_url(hunter, 'lists'), **CF).content.decode()

    assert 'pp-gtile__mosaic' in html, 'the tile rendered its no-art placeholder instead of covers'
    assert 'pp-gtile__mosaic is-2' in html, 'the mosaic did not compose for two covers'


def test_the_wall_does_not_n_plus_one_on_covers(client):
    """`attach_cover_games` is two queries for the whole wall no matter how many lists it holds.
    Building the mosaic per tile instead would N+1, and every list drags a Concept and an IGDBMatch
    behind it -- the join whose 30 KB blob triggered the May 2026 OOM.

    Asserts the count does not MOVE, not that it is under some number: a ceiling drifts upward one
    forgotten `select_related` at a time, while a delta of zero cannot.
    """
    hunter = _hunter()
    _list(hunter, name='One')
    url = _profile_url(hunter, 'lists')

    client.get(url, **CF)                               # warm any per-process caches
    with CaptureQueriesContext(connection) as first:
        client.get(url, **CF)

    for n in range(2, 7):
        _list(hunter, name=f'Number {n}')
    with CaptureQueriesContext(connection) as sixth:
        client.get(url, **CF)

    assert len(sixth) == len(first), (
        f'six lists cost {len(sixth)} queries where one cost {len(first)}; the wall scales with '
        f'the number of lists'
    )


def test_the_render_carries_its_own_bound(client):
    """`MEMBER_MAX_LISTS` is enforced by the SERVICE, at create time. That makes it a bound on how
    lists normally arrive, not a bound on what this page will draw -- and the page is the thing
    facing the internet. `gamelists/models.py` states the rule for exactly this shape: the render
    slices regardless, so a row that arrived another way cannot turn a public page unbounded.

    Rows are written with `bulk_create` on purpose, because that IS the scenario: a shell write, a
    data migration, or the legacy importer the model comment anticipates. Going through the service
    would only re-test the service's own cap.
    """
    from gamelists.models import MEMBER_MAX_LISTS

    hunter = _hunter()
    GameList.objects.bulk_create([
        GameList(owner=hunter, name=f'Bulk list {n}', is_public=True)
        for n in range(MEMBER_MAX_LISTS + 5)
    ])

    html = client.get(_profile_url(hunter, 'lists'), **CF).content.decode()

    # The full class ATTRIBUTE, which only the tile's anchor emits. Counting the bare
    # `pp-gcard--list` was off by one: profile_detail.html's inline script explains the reveal
    # branch in a JS comment that names the class, and JS comments ship to the page. A count
    # assertion is only as good as the uniqueness of the thing it counts.
    drawn = html.count('class="pp-gcard pp-gcard--list"')
    assert drawn == MEMBER_MAX_LISTS, (
        f'the wall drew {drawn} tiles against a cap of {MEMBER_MAX_LISTS}; the render is unbounded'
    )


def test_the_panel_renders_no_sentinel(client):
    """No sentinel is how profile_detail.html is told not to build an InfiniteScroller. The wall is
    capped at `MEMBER_MAX_LISTS`, so it has no second page to fetch -- and a scroller pointed at a
    grid with no next page re-fetches page 1 after an HTMX history restore."""
    hunter = _hunter()
    _list(hunter, name='Cosy platinums')

    html = client.get(_profile_url(hunter, 'lists'), **CF).content.decode()

    # THE POSITIVE HALF FIRST. On its own, `'lists-sentinel' not in html` cannot fail if the feature
    # is removed: drop `'lists'` from `_TAB_TEMPLATES` and the tab normalizes to Games, which renders
    # no lists sentinel either. The assertion would then be green while testing nothing.
    assert 'id="lists-grid"' in html, 'the lists wall did not render; the check below is vacuous'
    assert 'lists-sentinel' not in html


def test_the_reveal_selector_has_a_lists_branch():
    """Structural, because the failure is invisible to a render test that only checks the HTML.

    The page picks `revealSel` per tab and defaults to `.card`. That selector matches nothing on
    this wall, so `staggerReveal` would bail with nothing to animate while
    `.pp-reveal .pp-gcard { opacity: 0 }` kept every tile hidden -- a blank panel, server-side
    correct, with no error anywhere. The same bug is recorded twice already in that file.
    """
    src = PAGE.read_text(encoding='utf-8')

    assert re.search(r"tab === 'lists'\s*\)\s*\{\s*cardSel = revealSel = '\.pp-gcard'", src), (
        'profile_detail.html has no reveal-selector branch for the lists tab'
    )


def test_the_panel_reuses_the_shared_tile():
    """Not a style preference. The tile carries the cover mosaic, the moderated-name rule and the
    author mark; a hand-rolled card on this page would be a third list card to keep in step, and
    the first one to drift silently."""
    src = PANEL.read_text(encoding='utf-8')

    assert 'gamelists/partials/list_tile.html' in src


def test_the_panel_does_not_offer_the_privacy_chip():
    """Every list on this wall is public by definition, so a chip marking that is noise -- and
    passing `show_privacy` here would be the first sign somebody had widened the queryset.

    Comments are STRIPPED before the check. The panel's own prose explains why it omits
    `show_privacy`, and the first cut of this test matched that sentence and failed on a file that
    was already correct -- an assertion answering about the documentation instead of the code.
    """
    src = _COMMENT.sub('', PANEL.read_text(encoding='utf-8'))

    assert 'show_privacy' not in src
    assert 'list_tile.html' in src, 'comments stripped away the markup; the check is now vacuous'
