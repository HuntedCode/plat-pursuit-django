"""The Spotlight: one featured list, above the Game Lists browse grid.

Three things here are worth more than "does it render", and each has a test that fails loudly if it
stops being true:

1. IT MUST NOT COST ANYTHING ON A FILTERED OR PARTIAL REQUEST. The band lives outside
   `#browse-results`, so an HTMX filter swap and an InfiniteScroller page both render the grid
   partial and never render the band -- but `get_context_data` still runs for them. Without the
   gate in `BrowseListsView._spotlight`, the query fires on every keystroke of live search to build
   a value that is discarded. The query-count tests are the ones that notice.

2. IT MUST NOT PROMOTE WHAT THE GRID WOULD REFUSE. `featured()` is built on `public()`, so a list
   that is un-published, deleted or moderated after being featured drops out on its own rather than
   sitting at the top of the page with a stale flag behind it.

3. IT MUST STAY OUTSIDE THE SWAP TARGET. That is a structural property of the template, invisible
   to any assertion about rendered text, so it gets an explicit test on the markup's ORDER.
"""
import re
from pathlib import Path

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from gamelists.models import GameList
from gamelists.services import game_list_service as svc
from tests.factories import ConceptFactory, GameFactory, ProfileFactory, UserFactory

pytestmark = pytest.mark.django_db

BROWSE = reverse('lists_browse')

#: Resolved from this file, not from the working directory. The source-reading tests below were
#: opening bare relative paths, so they passed or errored depending on where pytest was invoked.
ROOT = Path(__file__).resolve().parents[2]

#: The band's own marker. Scoped to the class rather than to its copy: "Our pick" is two common
#: words and would match a list somebody named "Our picks" in a fixture.
BAND = 'class="gl-spotlight"'


@pytest.fixture
def viewer(client):
    user = UserFactory()
    user.role = 'admin'
    user.save()
    client.force_login(user)
    return client


def _hunter(psn=None):
    """`psn=None` lets the factory's sequence name them.

    It defaulted to a fixed 'curator', which collided the moment a test built a second list --
    `psn_username` is unique. Tests that care about the name still pass one.
    """
    kwargs = {'psn_username': psn} if psn else {}
    profile = ProfileFactory(is_linked=True, **kwargs)
    profile.user_is_premium = True
    profile.save(update_fields=['user_is_premium'])
    return profile


def _list(owner=None, *, name='A list', public=True, games=2, description=''):
    owner = owner or _hunter()
    game_list = svc.create_list(owner, name=name, is_public=public)
    if description:
        game_list.description = description
        game_list.save(update_fields=['description'])
    for _ in range(games):
        concept = ConceptFactory()
        GameFactory(concept=concept, title_platform=['PS5'])
        svc.add_concept(game_list, owner, concept)
    return game_list


def _feature(game_list, *, when=None):
    game_list.featured_at = when or timezone.now()
    game_list.save(update_fields=['featured_at'])
    return game_list


#: The Spotlight's WHERE clause, and nothing else's.
#:
#: NOT just `'featured_at'`. That was the first version, and it matched every query on the table:
#: `featured_at` is a COLUMN now, so Django names it in the SELECT list of every ordinary browse
#: read too. The test failed loudly here, but the same mistake pointed the other way is how an
#: assertion ends up proving nothing -- match the predicate, not the column.
_SPOTLIGHT_PREDICATE = '"featured_at" IS NOT NULL'


def _spotlight_queries(ctx):
    """The queries that exist only because of this feature."""
    return [q for q in ctx.captured_queries if _SPOTLIGHT_PREDICATE in q['sql']]


def _decommented(source):
    """JS with its comments removed.

    Needed because several guards here assert the PRESENCE of a token in a source file, and this
    codebase writes long comments that quote the very code they replaced -- so a raw-text check
    can be satisfied by the explanation of the bug it exists to prevent.
    """
    return re.sub(r'//[^\n]*', '', re.sub(r'/\*.*?\*/', '', source, flags=re.S))


def _wrapper(html):
    """The collapse wrapper's opening tag, which carries `is-collapsed` and `inert`.

    ANCHORED ON THE TAG, not on a character count. These tests used `html[i - 200:i + 60]` around
    `data-spotlight-wrap`, which worked only because `is-collapsed` happened to sit ~50 characters
    before the anchor. Any attribute added ahead of it would slide the marker out of the window --
    and the assertions that read "not in wrap" would then pass forever, silently, which is the
    wrong direction to fail in.
    """
    found = re.search(r'<div[^>]*data-spotlight-wrap[^>]*>', html)
    assert found, 'the Spotlight wrapper did not render; the assertions below prove nothing'
    return found.group(0)


# ── it renders ───────────────────────────────────────────────────────────────────────────────────

def test_a_featured_list_gets_the_band(viewer):
    _feature(_list(name='Weekend Platinums', description='Twelve short, honest platinums.'))

    html = viewer.get(BROWSE).content.decode()

    band = html[html.index(BAND):html.index('</a>', html.index(BAND))]

    assert 'Our pick' in band, 'the band rendered without its eyebrow'
    assert 'Weekend Platinums' in band
    assert 'Twelve short, honest platinums.' in band, "the list's own description is the blurb"
    # The reel is attached by a SEPARATE service call in the view (`attach_cover_games`), which is
    # easy to drop while everything else still renders -- the band just goes art-less, and no other
    # assertion here notices.
    assert 'gl-spotlight__reel' in band, 'the band rendered with no cover reel'
    assert band.count('gl-spotlight__cover') >= 2, 'the reel rendered no covers'


def test_no_band_when_nothing_is_featured(viewer):
    _list(name='Just a list')

    html = viewer.get(BROWSE).content.decode()

    assert BAND not in html
    # The grid still rendered, so the assertion above is about the band and not about a broken page.
    assert 'Just a list' in html


def test_the_most_recently_featured_wins(viewer):
    """`featured_at` is the choice AND the ordering. Featuring a second list is how you replace the
    first, without having to remember to clear it."""
    old = _feature(_list(name='Last month', games=1),
                   when=timezone.now() - timezone.timedelta(days=30))
    new = _feature(_list(name='This week', games=1))

    html = viewer.get(BROWSE).content.decode()
    band = html[html.index(BAND):html.index('</a>', html.index(BAND))]

    assert new.name in band
    assert old.name not in band, 'the older pick is still in the band'


def test_the_band_shows_the_owner_mark(viewer):
    """The Spotlight's whole job is saying who chose this. A staff-written pick that renders an
    unmarked byline says nothing the grid below it does not."""
    owner = _hunter('ppstaff')
    owner.user.role = 'admin'
    owner.user.save()
    _feature(_list(owner, name='Official pick'))

    html = viewer.get(BROWSE).content.decode()
    band = html[html.index(BAND):html.index('</a>', html.index(BAND))]

    assert 'pp-markname__svc' in band, 'the Spotlight byline lost the staff wrench'


# ── it disappears when it should ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('param', ['?q=zelda', '?min_games=1', '?max_games=99'])
def test_a_filtered_page_starts_with_the_band_collapsed(viewer, param):
    """You asked for something specific; our pick is not it.

    THE CONTRACT CHANGED, and the reason is worth keeping. This used to assert the band was absent
    from a filtered page, which was true of a full render and meaningless in practice: live search
    swaps `#browse-results`, the band lives OUTSIDE that target, so nothing the server did could
    remove a band it had already sent. The gate worked only on the path nobody takes interactively,
    and a reader typing in the search box kept a staff pick hovering over their results.

    So the band is always sent on a full render and the page marks its starting state. Filtered
    means collapsed AND inert -- collapsed alone leaves a 0px, transparent, still-clickable link.
    """
    _feature(_list(name='Weekend Platinums'))

    html = viewer.get(BROWSE + param).content.decode()
    wrap = _wrapper(html)

    assert 'is-collapsed' in wrap, 'a filtered page shows the band uncollapsed'
    assert 'inert' in wrap, 'the collapsed band is still focusable and clickable'


def test_an_unfiltered_page_has_the_band_open(viewer):
    """The other half, so the test above cannot pass by the band being collapsed always."""
    _feature(_list(name='Weekend Platinums'))

    html = viewer.get(BROWSE).content.decode()
    wrap = _wrapper(html)

    assert 'is-collapsed' not in wrap, 'the canonical page starts with the band collapsed'
    assert 'inert' not in wrap


def test_the_grid_tells_the_client_whether_filters_are_on(viewer):
    """The swap carries the flag, because the band cannot be in the swap.

    `lists-browse.js` reads `data-has-filters` off the grid after each swap and collapses or
    restores the band to match. It is carried from the server rather than recomputed in JS because
    "is a filter on" already has a precise definition in the view -- a `?q=` under three characters
    narrows nothing and must not count -- and a second copy of that rule is what made the band
    vanish on the first keystroke of live search.
    """
    _feature(_list(name='Weekend Platinums'))

    plain = viewer.get(BROWSE, HTTP_HX_REQUEST='true').content.decode()
    filtered = viewer.get(BROWSE + '?q=zelda', HTTP_HX_REQUEST='true').content.decode()

    assert 'data-has-filters="0"' in plain
    assert 'data-has-filters="1"' in filtered

    # And the client actually reads it. Pinned in source because the collapse is a DOM effect on an
    # element the swap never touches, so no server assertion can see it.
    # DE-COMMENTED. `'inert' in js` against the raw file was satisfied by the comment explaining
    # why `inert` is needed -- delete both `setAttribute`/`removeAttribute` calls and it stayed
    # green. The sibling assertion below already strips comments; this one had not been given the
    # same treatment.
    js_raw = (ROOT / 'static' / 'js' / 'lists-browse.js').read_text(encoding='utf-8')
    js = _decommented(js_raw)
    assert 'hasFilters' in js, 'nothing collapses the band when a filter arrives over htmx'
    assert "setAttribute('inert'" in js, 'the collapsed band is left focusable after a swap'
    assert "removeAttribute('inert')" in js, 'the band is never made interactive again'

    # AND IT IS ACTUALLY CALLED. Asserting the helper's contents survived a mutation that deleted
    # the call site and left the function defined and never run -- the band stayed on screen
    # through every filter, exactly as before the fix.
    swap = js[js.index('function onAfterSwap'):]
    swap = swap[:swap.index('\n    }')]
    assert 'syncSpotlight(' in swap, 'the swap handler never syncs the band'


@pytest.mark.parametrize('typed', ['z', 'ze'])
def test_a_query_too_short_to_filter_does_not_kill_the_band(viewer, typed):
    """THE FIRST TWO KEYSTROKES OF LIVE SEARCH.

    `get_queryset` discards a `?q=` shorter than `MIN_QUERY` (3), so one or two characters narrow
    nothing at all -- the grid below is the complete, unfiltered list. But `has_filters` read the
    RAW querystring, so the page believed a filter was on: the band vanished on keystroke one and
    did not return until keystroke three, above a grid that had not changed.

    Both now read through `_effective_query()`. Pinned at one and two characters because the bug is
    at the boundary, and a test at three would pass against the broken code.
    """
    _feature(_list(name='Weekend Platinums'))

    html = viewer.get(BROWSE + f'?q={typed}').content.decode()
    wrap = _wrapper(html)

    # ASSERTED AS "NOT COLLAPSED", not as "present". `BAND in html` is what this checked first, and
    # it went vacuous the moment the band started being rendered on every full page: the string is
    # now always there and only the collapse state carries the meaning.
    assert 'is-collapsed' not in wrap, (
        f'a {len(typed)}-character query collapsed the band without filtering anything')


def test_an_anonymous_reader_gets_the_band(client):
    """THE PAGE'S ACTUAL AUDIENCE. Browse went anonymous in 2026-09, and every other test in this
    file signs in as an admin-role user -- so without this one the band is never exercised on the
    path almost everybody takes, and a viewer-dependent regression would ship green."""
    _feature(_list(name='Weekend Platinums', description='Twelve short, honest platinums.'))

    resp = client.get(BROWSE)
    html = resp.content.decode()

    assert resp.status_code == 200
    assert BAND in html
    band = html[html.index(BAND):html.index('</a>', html.index(BAND))]
    assert 'Weekend Platinums' in band
    assert 'gl-spotlight__reel' in band, 'the anonymous render lost the cover reel'


def test_sorting_is_not_filtering_so_the_band_stays(viewer):
    """`has_filters` excludes sort on purpose -- re-ordering the grid is not searching it, and the
    empty state draws the same line. Pinned because it is the one case where "no querystring" and
    "no filters" disagree."""
    _feature(_list(name='Weekend Platinums'))

    html = viewer.get(BROWSE + '?sort=recent').content.decode()
    wrap = _wrapper(html)

    # NOT `BAND in html`, which this file's own comment elsewhere records as having gone vacuous:
    # the band renders on every full page now, so the string is always present and only the
    # collapse state carries meaning. Making `?sort=` count as a filter would collapse the band on
    # every sort change and the old assertion would not have noticed.
    assert 'is-collapsed' not in wrap, 'sorting collapsed the band as though it were a filter'


@pytest.mark.parametrize('kill,label', [
    ({'is_public': False}, 'un-published'),
    ({'is_deleted': True}, 'deleted'),
])
def test_a_featured_list_that_leaves_the_grid_leaves_the_band(viewer, kill, label):
    """`featured()` is built on `public()`, so the band cannot promote a list its own grid refuses
    to show. Without that, un-publishing a featured list would leave it on the front of the page."""
    game_list = _feature(_list(name='Weekend Platinums'))
    for field, value in kill.items():
        setattr(game_list, field, value)
    game_list.save(update_fields=list(kill))

    html = viewer.get(BROWSE).content.decode()

    assert BAND not in html, f'a {label} list is still in the Spotlight'


def test_a_moderated_list_shows_its_neutral_name_in_the_band(viewer):
    """A moderator can hide a featured list's words AFTER it was featured -- the admin only guards
    the moment of featuring -- so the band has to read `display_name` like every other surface.

    SCOPED TO THE BAND, and that is the whole test. The first version asserted against the entire
    page, which proved nothing: the featured list is public, so it ALSO renders as a grid tile
    below, and `list_tile.html` already reads `display_name`. Both assertions were satisfied by the
    tile, so the test passed with the band deleted outright. An audit caught it.
    """
    game_list = _feature(_list(name='Something Vile'))
    game_list.text_hidden = True
    game_list.save(update_fields=['text_hidden'])

    html = viewer.get(BROWSE).content.decode()
    band = html[html.index(BAND):html.index('</a>', html.index(BAND))]

    assert 'Something Vile' not in band
    assert GameList.HIDDEN_NAME in band


# ── it costs nothing when it is not shown ────────────────────────────────────────────────────────

def test_the_canonical_page_pays_for_the_band_once(viewer):
    """Bounded, and bounded regardless of how many lists are featured over time."""
    for n in range(4):
        _feature(_list(name=f'Pick {n}', games=1),
                 when=timezone.now() - timezone.timedelta(days=n))

    with CaptureQueriesContext(connection) as ctx:
        resp = viewer.get(BROWSE)

    assert resp.status_code == 200, 'a redirect would make the count below meaningless'
    assert len(_spotlight_queries(ctx)) == 1, (
        f'expected one Spotlight query, got {len(_spotlight_queries(ctx))}')

    # AND EXACTLY ONE EXTRA COVER CALL, not one per cover.
    #
    # `attach_cover_games` costs two queries per CALL regardless of how many lists it is handed, so
    # the canonical page pays two for the grid and two for the reel. It was briefly ONE call for
    # both, which was right while the band showed the tile's four-cover mosaic -- the same rows
    # twice. The reel fetches `SPOTLIGHT_COVERS`, a deeper slice, so they are now genuinely
    # different fetches; merging them again would mean pulling eight covers for all twenty-four
    # grid lists to serve one band.
    #
    # Two is the ceiling this pins. A per-cover regression would show up as nine.
    cover_queries = [q for q in ctx.captured_queries if 'gamelists_gamelistitem' in q['sql']]
    assert len(cover_queries) == 2, (
        f'expected one cover call for the grid and one for the reel, saw {len(cover_queries)}')


def test_a_filtered_full_page_pays_one_lookup_and_no_more(viewer):
    """A filtered FULL render does query for the band now, deliberately, and this pins the price.

    It has to: the band must exist in the DOM, collapsed, so that clearing the filter over htmx can
    restore it. Landing on `?q=soulslike` and clearing the box used to leave no band at all, because
    none had ever been rendered.

    That is one indexed lookup on a direct link or an Enter press. The path that matters -- the
    per-keystroke partial render -- still pays nothing, which the test below pins.
    """
    _feature(_list(name='Weekend Platinums'))

    with CaptureQueriesContext(connection) as ctx:
        resp = viewer.get(BROWSE + '?q=zelda')

    assert resp.status_code == 200
    assert len(_spotlight_queries(ctx)) == 1, (
        f'a filtered page ran {len(_spotlight_queries(ctx))} Spotlight queries, expected one')


@pytest.mark.parametrize('headers,label', [
    ({'HTTP_HX_REQUEST': 'true'}, 'an HTMX filter swap'),
    ({'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'}, 'an infinite-scroll page fetch'),
])
def test_a_partial_render_does_not_query_for_the_band(viewer, headers, label):
    """THE EXPENSIVE ONE IF IT REGRESSES. Both of these render the grid partial, which does not
    contain the band -- but they run `get_context_data` all the same. Live search fires one per
    keystroke."""
    _feature(_list(name='Weekend Platinums'))

    with CaptureQueriesContext(connection) as ctx:
        resp = viewer.get(BROWSE, **headers)

    assert resp.status_code == 200
    # NOT asserting the band is absent from the body: a partial render returns
    # `browse_results.html`, which contains no band markup whatever the gate does, so that
    # assertion could never fail. The query is the only observable difference.
    assert _spotlight_queries(ctx) == [], f'{label} queried for a band it never renders'


# ── the structural rule ──────────────────────────────────────────────────────────────────────────

def test_the_band_sits_outside_the_htmx_swap_target():
    """A TEMPLATE-ORDER TEST, because no rendered-text assertion can see this.

    `#browse-results` is replaced wholesale on every filter swap and every scroll page. A band
    inside it would be torn out and rebuilt constantly -- flickering on each keystroke, and needing
    its query on every partial render, which is exactly what the gate above exists to avoid. The
    mini-bar and the scroll sentinel are outside for the same reason.
    """
    template = (ROOT / 'templates' / 'gamelists' / 'browse.html').read_text(encoding='utf-8')

    band = template.index('class="gl-spotlight"')
    target = template.index('<div id="browse-results">')

    assert band < target, (
        'the Spotlight moved inside #browse-results; it will now re-render on every filter swap')


def test_the_band_reads_the_display_fields_not_the_raw_ones():
    """The moderation readers, pinned in the template. `test_a_moderated_list_...` above proves the
    name; this proves the DESCRIPTION too, which has no separate render test because an empty
    description and a hidden one look identical on the page."""
    template = (ROOT / 'templates' / 'gamelists' / 'browse.html').read_text(encoding='utf-8')
    start = template.index('class="gl-spotlight"')
    band = template[start:template.index('</a>', start)]

    assert 'spotlight.display_name' in band
    assert 'spotlight.display_description' in band
    assert re.search(r'\{\{\s*spotlight\.name\s*\}\}', band) is None, 'the band renders the raw name'
    assert re.search(r'\{\{\s*spotlight\.description\s*\}\}', band) is None, (
        'the band renders the raw description')


def test_the_band_names_its_pick_with_a_heading():
    """The page is `h1 Game Lists` and the grid tiles are `h3`. A `span` here left the single most
    prominent list on the page out of the heading outline entirely, so heading navigation jumped
    from the page title into the grid, past the thing we put at the top deliberately."""
    template = (ROOT / 'templates' / 'gamelists' / 'browse.html').read_text(encoding='utf-8')
    start = template.index('class="gl-spotlight"')
    band = template[start:template.index('</a>', start)]

    # h3, the SAME LEVEL AS A TILE. An h2 here nested all twenty-four grid tiles under the
    # featured list in the heading outline; the band is a peer of the tiles, not their parent.
    assert '<h3 class="gl-spotlight__name">' in band, 'the band no longer names its pick with a heading'
    assert '<h2' not in band, 'the band name outranks the tiles again; they will nest under it'


def test_the_band_does_not_override_its_accessible_name():
    """NO `aria-label` ON THE BAND, and this guard is the reason it stays that way.

    An `aria-label` REPLACES the contents-derived name, so anything not restated inside it goes
    silent. The band once had one, and it had already dropped the author's mark and the blurb -- the
    two things the band exists to say. The tile's own comment records making the identical mistake
    with its Private chip, which is twice.

    Letting the contents speak means the name cannot drift from what is on screen. Adding a label
    back is a decision that should have to argue with this test.
    """
    template = (ROOT / 'templates' / 'gamelists' / 'browse.html').read_text(encoding='utf-8')

    # THE WHOLE OPENING TAG, matched from `<a`. This sliced from `class="gl-spotlight"` to the next
    # `>` -- and `class` is the LAST attribute on that tag, so the window was the twenty characters
    # of the class attribute itself. It could not have contained an `aria-label` under any
    # circumstances, and adding one in the natural place (after `href`) was invisible to it.
    # Unfalsifiable, on the exact regression it names.
    found = re.search(r'<a\b[^>]*class="gl-spotlight"[^>]*>', template)
    assert found, 'the band anchor is gone; this guard no longer describes the page'
    band = found.group(0)

    assert 'aria-label' not in band, (
        'the band overrides its accessible name again; the mark and the blurb go silent')


# ── the CSS contract ─────────────────────────────────────────────────────────────────────────────

def _spotlight_css(selector):
    """One rule block from the source stylesheet, or None."""
    css = (ROOT / 'static' / 'css' / 'components' / 'gamelists.css').read_text(encoding='utf-8')
    found = re.search(re.escape(selector) + r'\s*\{([^}]*)\}', css)
    return found.group(1) if found else None


def test_the_reel_clips_and_fades_its_overflow():
    """THE REEL IS SUPPOSED TO OVERFLOW -- that is the effect, not a bug.

    It holds more covers than fit so the row runs off the edge, which says "there is more of this
    list" where a strip stopping short of the edge just looks unfinished. Two properties make that
    read as deliberate rather than broken: `overflow: hidden` so the spill does not escape the
    band, and a mask so the last cover fades instead of being guillotined mid-image.

    The mask is also what keeps the hover nudge honest: the reel slides left on hover, and without
    the fade the covers would visibly pop at a hard edge.
    """
    block = _spotlight_css('.gl-spotlight__reel')
    faded = _spotlight_css('.gl-spotlight__reel.is-overflowing')

    assert block, '.gl-spotlight__reel is gone'
    assert 'overflow: hidden' in block, 'the reel stopped clipping; covers will spill out'

    # THE FADE IS CONDITIONAL, and that is the point rather than an implementation detail. When it
    # was unconditional, a reel that did NOT overflow -- a short featured list, or any list on a
    # screen wide enough that the reel outgrew its covers -- rendered its last, fully visible cover
    # at about half opacity with empty band beside it. That reads as a rendering fault, which is
    # the opposite of the "there is more of this list" the fade exists to say.
    assert faded, 'the fade is no longer gated on the reel actually overflowing'
    assert 'mask-image' in faded, 'the reel lost its fade; the last cover is cut off square'
    assert '-webkit-mask-image' in faded, 'the reel fade is missing its Safari prefix'
    assert 'mask-image' not in block, (
        'the fade is unconditional again; a short list will ghost its last cover')

    # CSS cannot ask whether a box overflowed, so something has to measure it.
    js = (ROOT / 'static' / 'js' / 'lists-browse.js').read_text(encoding='utf-8')
    assert 'scrollWidth' in js, 'nothing measures the reel, so the fade can never be applied'
    assert 'is-overflowing' in js


def test_the_reel_does_not_move_on_hover():
    """THE ART HOLDS STILL, and this is the third position on it -- the first two each shipped a
    bug, which is why it is pinned rather than left to taste.

    The idea was a nudge uncovering a little more of the next cover:

      1. `transform` on the REEL moved its `overflow` clip and its mask along with the content, so
         the strip slid as a rigid unit and showed exactly the same pixels. It revealed nothing.
      2. `transform` on the COVERS revealed at the right by damaging the left: the row sits flush
         with the reel's left edge, so sliding it left pushed the first cover past the clip and
         sheared a strip off the front of it. That one was spotted on the page.

    Doing it properly needs the row inset by the travel distance at rest, which buys a permanent
    gap that reads as misalignment. The fade mask already says "there is more", always, rather
    than only on hover -- so the band lifts, its border brightens, and the art does not move.
    """
    css = (ROOT / 'static' / 'css' / 'components' / 'gamelists.css').read_text(encoding='utf-8')

    for selector in ('.gl-spotlight:hover .gl-spotlight__cover',
                     '.gl-spotlight:hover .gl-spotlight__reel'):
        assert not re.search(re.escape(selector) + r'\s*\{[^}]*transform', css), (
            f'{selector} moves again -- it either reveals nothing or clips the first cover')

    # The band itself still has its lift, so this is about the ART holding still and not about the
    # hover having been dropped altogether.
    assert re.search(r'\.gl-spotlight:hover\s*\{[^}]*transform:\s*translateY', css), (
        'the band lost its hover lift entirely')


def test_hovering_a_cover_names_the_game_and_picks_it_out(viewer):
    """THE HOVER EARNS ITSELF BY BEING INFORMATIVE.

    The covers are not separately clickable -- the whole band is one link to the list -- so a
    purely decorative flourish on them would promise a target that does not exist. Naming the game
    under the pointer is a reason for the state to be there.

    The dim is `opacity` and nothing else, which is the constraint that makes this work where the
    row nudge did not: the first cover sits flush against the reel's `overflow` clip, so any
    geometric change shears a strip off its leading edge. Pinned, because "just add a small scale"
    is the obvious-looking change that re-breaks it.
    """
    owner = _hunter('theauthor')
    game_list = _list(owner, name='Weekend Platinums', games=3)
    _feature(game_list)

    html = viewer.get(BROWSE).content.decode()
    band = html[html.index(BAND):html.index('</a>', html.index(BAND))]
    css = (ROOT / 'static' / 'css' / 'components' / 'gamelists.css').read_text(encoding='utf-8')

    # Every cover names its game, so the tooltip is not on only the first one.
    covers = re.findall(r'<img[^>]*class="gl-spotlight__cover[^"]*"[^>]*>', band)
    assert len(covers) >= 3, f'expected the reel to render the list, found {len(covers)} covers'
    assert all('title="' in c for c in covers), 'a cover renders without naming its game'

    # The picking-out half, and that it is opacity rather than size.
    assert re.search(r'\.gl-spotlight__reel:hover \.gl-spotlight__cover\s*\{[^}]*opacity', css), (
        'the reel no longer recedes, so nothing picks out the cover under the pointer')
    assert re.search(r'\.gl-spotlight__cover:hover\s*\{[^}]*opacity:\s*1', css), (
        'the hovered cover does not come back to full')


def test_naming_the_covers_costs_no_extra_queries(viewer):
    """`title` reads through `item.concept`, which `cover_games_for` already joins. If that
    `select_related` is ever dropped, the tooltip turns into one query per cover on the page's
    most prominent element -- and the rendered output would look identical."""
    _feature(_list(name='Weekend Platinums', games=4))

    with CaptureQueriesContext(connection) as ctx:
        resp = viewer.get(BROWSE)

    assert resp.status_code == 200
    concept_reads = [q for q in ctx.captured_queries
                     if 'trophies_concept' in q['sql'] and ' WHERE "trophies_concept"."id" = ' in q['sql']]
    assert not concept_reads, (
        f'the reel fetches concepts one at a time: {len(concept_reads)} singleton reads')


def test_the_reel_covers_keep_the_house_image_rules():
    """`object-cover` + `object-position: top` is the project rule for game art: PSN fallback art is
    square or 4:3 and crops into a 3:4 well, so anchoring to the top is what keeps the game's logo
    in frame instead of showing the sky above it. `aspect-ratio` rather than a fixed height so the
    covers keep their shape as the reel's width changes across breakpoints."""
    block = _spotlight_css('.gl-spotlight__cover')

    assert block, '.gl-spotlight__cover is gone'
    assert 'object-fit: cover' in block, 'reel covers must not stretch'
    assert 'object-position: top' in block, 'reel covers lost their top anchor; logos will crop off'
    assert 'aspect-ratio: 3 / 4' in block, 'reel covers are no longer portrait-ratio'


def test_the_band_joins_the_pages_opening_beat():
    """The band must not be the one static thing on a choreographed page.

    This page already sequences: the header card plays `.pp-head-cascade`, then the grid springs in
    tile by tile via `staggerReveal`. The band shipped between them with no entrance and simply
    appeared, which is the "does the thing just appear?" failure the Career standard's polishing
    lens asks about.

    Pinned as THREE properties rather than as exact numbers, because the timings are a judgement and
    the mechanism is not:

    * a reduced-motion gate, so the animation is never forced on a viewer who asked for stillness;
    * TWO animations, because the spring curve's overshoot control point is >1 and putting it on
      opacity spikes the fade to full almost instantly -- a flash, not a fade;
    * `backwards` and never `both`. `both` persists the final transform, which makes the band a
      containing block permanently and silently kills the hover lift.
    """
    css = (ROOT / 'static' / 'css' / 'components' / 'gamelists.css').read_text(encoding='utf-8')

    # MATCHED AS GATE-PLUS-RULE IN ONE PATTERN, not via `_spotlight_css`. `.gl-spotlight` now has
    # two blocks -- the base rule and this gated one -- and the helper returns the FIRST, so it
    # handed back the base block and the test failed claiming the animation was gone. Asserting the
    # gate and its contents together is also stronger: it cannot pass with the animation present
    # but sitting outside the reduced-motion gate.
    gated = re.search(
        r'@media \(prefers-reduced-motion: no-preference\) \{\s*\.gl-spotlight \{([^}]*)\}', css)

    assert gated, 'the band has no page-entry animation behind a reduced-motion gate'
    block = gated.group(1)

    assert 'animation:' in block
    assert block.count('backwards') == 2, (
        'the band should run a separate fade and rise, both filling backwards')
    assert 'both' not in block, (
        "the entrance fills `both`, which pins a transform on the band and breaks its hover lift")


def test_the_reel_is_fetched_deeper_than_the_tile_mosaic():
    """The overflow only happens because the reel holds more covers than a tile does.

    Quietly setting `SPOTLIGHT_COVERS` back to `LIST_TILE_COVERS` would leave every test above
    passing -- there would still be a reel, still clipped, still masked -- while the band went back
    to stopping halfway across a desktop screen, which is the exact complaint the reel was built to
    answer. Compared as constants rather than pinned to 8, because the right number is a judgement
    about widths; what must stay true is the relationship.
    """
    from gamelists.views import LIST_TILE_COVERS, SPOTLIGHT_COVERS

    assert SPOTLIGHT_COVERS > LIST_TILE_COVERS, (
        'the reel no longer fetches deeper than a tile, so it will not reach the edge')


def test_the_spotlight_byline_truncates_like_the_tile_byline():
    """ALL THREE PROPERTIES. The first cut copied `overflow: hidden` and left `white-space` and
    `text-overflow` behind, which inverted the bug: a MARKED name was fine (the mark component
    brings its own) while an UNMARKED one -- nearly all of them -- wrapped to two lines and pushed
    the foot row taller. `overflow: hidden` cannot clip that; a flex item with auto height grows."""
    block = _spotlight_css('.gl-spotlight__by')

    assert block, '.gl-spotlight__by is gone'
    for prop in ('overflow: hidden', 'white-space: nowrap', 'text-overflow: ellipsis'):
        assert prop in block, f'.gl-spotlight__by lost `{prop}`; unmarked bylines will wrap'


# ── the curation desk ────────────────────────────────────────────────────────────────────────────

CHANGELIST = '/admin/gamelists/gamelist/'


@pytest.fixture
def owner_client(client):
    """Django admin is superuser-only (`core/admin_site.py`), so featuring is the owner's lever."""
    user = UserFactory()
    user.is_superuser = user.is_staff = True
    user.save()
    client.force_login(user)
    return client


def _action(client, name, rows):
    return client.post(
        CHANGELIST,
        {'action': name, '_selected_action': [str(r.pk) for r in rows]},
        follow=True)


def test_the_admin_can_feature_a_list(owner_client):
    game_list = _list(name='Weekend Platinums')

    _action(owner_client, 'feature_selected', [game_list])

    game_list.refresh_from_db()
    assert game_list.featured_at is not None


def test_featuring_several_at_once_is_refused(owner_client):
    """"Most recently set wins" means featuring three would quietly pick one and leave the other two
    looking featured in the changelist. A refusal is the honest answer."""
    rows = [_list(name=f'Pick {n}', games=1) for n in range(3)]

    resp = _action(owner_client, 'feature_selected', rows)

    assert 'Pick exactly one list' in resp.content.decode()
    for row in rows:
        row.refresh_from_db()
        assert row.featured_at is None, 'a refused bulk feature still wrote a flag'


@pytest.mark.parametrize('field,value', [('is_public', False), ('text_hidden', True)])
def test_the_admin_refuses_to_feature_what_the_band_cannot_show(owner_client, field, value):
    """The band reads through `public()`, so featuring a private or hidden list writes a flag that
    does nothing -- which reads as the feature being broken. Refuse at the decision instead."""
    game_list = _list(name='Not eligible')
    setattr(game_list, field, value)
    game_list.save(update_fields=[field])

    resp = _action(owner_client, 'feature_selected', [game_list])

    assert 'cannot be featured' in resp.content.decode()
    game_list.refresh_from_db()
    assert game_list.featured_at is None


def test_the_admin_can_clear_the_spotlight(owner_client):
    """Bulk IS allowed in this direction: clearing is idempotent and there is no choice to lose."""
    rows = [_feature(_list(name=f'Pick {n}', games=1)) for n in range(2)]

    _action(owner_client, 'unfeature_selected', rows)

    for row in rows:
        row.refresh_from_db()
        assert row.featured_at is None


def test_the_admin_cannot_create_or_hard_delete_a_list(owner_client):
    """Lists are made by hunters through the service that maintains their counts, and deletion is
    soft and belongs to the author or to moderation. Both doors are shut, so a curation desk cannot
    become an accidental content editor.

    The status assertion and the POSITIVE anchor are not decoration. Every assertion below is a
    negative, and a permission failure returns a near-empty 302 body in which they are ALL trivially
    true -- so without them this test would keep passing on the day the fixture stopped granting
    admin access, which is exactly when it should scream.
    """
    # No apostrophe: the changelist escapes it, so the anchor would never match its own fixture.
    _list(name='A hunters list')

    resp = owner_client.get(CHANGELIST)

    assert resp.status_code == 200, f'the changelist answered {resp.status_code}'
    body = resp.content.decode()
    assert 'A hunters list' in body, 'the changelist rendered no rows; the checks below prove nothing'

    assert 'Add game list' not in body
    assert '/admin/gamelists/gamelist/add/' not in body
    # The delete half, which the first version of this test claimed in its name and never checked.
    assert 'delete_selected' not in body, 'the bulk delete action is available'


def test_the_admin_form_cannot_set_featured_at_directly(owner_client):
    """The actions are the ONLY writer.

    `feature_selected` refuses a list that is private, deleted or moderated. While `featured_at` was
    editable on the change form, a superuser could type a timestamp straight past all three -- one
    guarded door and one unguarded one, which is not a guard. Three separate comments asserted the
    guarantee the form was quietly breaking.
    """
    game_list = _list(name='Weekend Platinums')

    resp = owner_client.get(f'{CHANGELIST}{game_list.pk}/change/')

    assert resp.status_code == 200
    body = resp.content.decode()
    assert 'Weekend Platinums' in body, 'the change form did not render'
    assert 'name="featured_at_0"' not in body and 'name="featured_at"' not in body, (
        'featured_at is editable on the change form, which bypasses feature_selected entirely')
