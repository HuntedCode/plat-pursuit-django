"""Browse Hunters -- the rebuilt directory at `/hunters/`.

Rebuilt 2026-08 from scratch. The page had been a second scoreboard (eleven sorts, most of them "who is
biggest") sitting next to a Leaderboards hub that already ranks hunters. It is a DISCOVERY surface now:
find people, not positions.

What these pin is the part that is easy to regress silently -- the sort contract shrinking or drifting out
of step with the form, the saved-defaults fallback taking the search down with it, and the query count
quietly starting to scale with the number of cards. The look and the motion were checked in a browser,
since neither is visible from here.
"""
import re
from pathlib import Path

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from tests.factories import ProfileFactory
from trophies.models import Title, UserTitle
from trophies.views.profile_views import ProfilesListView

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[2]
URL = '/hunters/'


def _hunters(n, **kw):
    return [ProfileFactory(is_linked=True, **kw) for _ in range(n)]


# ── The sort contract ─────────────────────────────────────────────────────────────────────────────

def test_the_sorts_are_the_curated_discovery_set():
    """Ranking sorts left when the page stopped being a second scoreboard: badges + badge XP went to
    /leaderboards/ (which already boards hunters by them), and games / completions / avg-progress /
    rarest-avg-platinum were dropped as more "who is biggest" orderings.

    `rarest_avg_plat` is the one worth naming: it was a correlated AVG over every EarnedTrophy row per
    profile, sitewide, and the only sort with no index behind it.
    """
    assert set(ProfilesListView.SORTS) == {
        'recently_active', 'alpha', 'recently_joined', 'trophies', 'plats',
    }
    for gone in ('badges_earned', 'badge_xp', 'rarest_avg_plat', 'games', 'completes', 'avg_progress'):
        assert gone not in ProfilesListView.SORTS, f'{gone} is back on a discovery page'


def test_the_form_offers_exactly_what_the_view_can_order_by():
    """The view resolves the raw `?sort=` against its own map, so a choice the form offers but the map
    does not know silently falls back to the default order -- a control that appears to do nothing."""
    from trophies.forms import ProfileSearchForm

    offered = {value for value, _ in ProfileSearchForm().fields['sort'].choices if value}

    assert offered == set(ProfilesListView.SORTS), 'the form and the view disagree about the sorts'


@pytest.mark.parametrize('sort', ['recently_active', 'alpha', 'recently_joined', 'trophies', 'plats'])
def test_every_offered_sort_actually_renders(client, sort):
    _hunters(3)

    assert client.get(URL, {'sort': sort}).status_code == 200


def test_a_retired_sort_falls_back_without_taking_the_search_with_it(client):
    """Someone whose `browse_defaults` still names a retired sort gets redirected onto it, and that fails
    form validation. The sort is the only thing that may suffer for it: the view guards ALL filtering
    behind one `is_valid()`, so reading the sort inside that branch would drop their search and country
    too -- a far stranger failure than landing on the default order.
    """
    _hunters(2)
    ProfileFactory(is_linked=True, psn_username='findme')

    resp = client.get(URL, {'sort': 'rarest_avg_plat', 'query': 'findme'})

    assert resp.status_code == 200
    names = [p.psn_username for p in resp.context['object_list']]
    assert names == ['findme'], f'the search was dropped along with the retired sort: {names}'


# ── Performance: the wall must not scale ──────────────────────────────────────────────────────────

def test_the_query_count_does_not_grow_with_the_number_of_hunters(client):
    """The guard for the whole page. Two separate N+1s are possible here and both are invisible in a
    small dev database:

    1. `Profile.displayed_title` is a METHOD -- `user_titles.filter(...).first()` plus a `title` FK hop,
       so two queries per card, ~60 on a full page, purely to print a word under each name. The view
       annotates `display_title` with a subquery instead (served by `usertitle_display_idx`).
    2. `.only(...)` trims the row to what a card draws, which turns any field the template touches but
       the list forgot into a per-row deferred fetch -- the same N+1 wearing a different hat.
    """
    title = Title.objects.create(name='Case Hardened')
    for profile in _hunters(3):
        UserTitle.objects.create(profile=profile, title=title, is_displayed=True)

    with CaptureQueriesContext(connection) as few:
        client.get(URL)

    for profile in _hunters(9):
        UserTitle.objects.create(profile=profile, title=title, is_displayed=True)

    with CaptureQueriesContext(connection) as many:
        client.get(URL)

    assert len(many) == len(few), (
        f'{len(few)} queries for 3 hunters, {len(many)} for 12 -- the grid scales with its rows.\n'
        + '\n'.join(q['sql'][:160] for q in many.captured_queries[len(few):])
    )


def test_the_displayed_title_comes_from_the_annotation_not_the_method(client):
    """Positive control for the query test above: cheap is only worth anything if it is also correct."""
    profile = ProfileFactory(is_linked=True)
    UserTitle.objects.create(
        profile=profile, title=Title.objects.create(name='Case Hardened'), is_displayed=True,
    )

    row = client.get(URL).context['object_list'][0]

    assert row.display_title == 'Case Hardened'


def test_an_undisplayed_title_is_not_picked_up(client):
    """`is_displayed` is the whole filter; without it the subquery would print whichever title happened
    to be first."""
    profile = ProfileFactory(is_linked=True)
    UserTitle.objects.create(
        profile=profile, title=Title.objects.create(name='Hidden'), is_displayed=False,
    )

    assert client.get(URL).context['object_list'][0].display_title is None


# ── The rebuild itself ────────────────────────────────────────────────────────────────────────────

def test_the_page_is_built_from_the_rebuild_primitives_not_daisyui():
    """A from-scratch rebuild, not a reskin: the old page was DaisyUI throughout (`card bg-base-200/70`,
    `btn btn-`, `select select-`, `badge badge-`) with none of the shared primitives."""
    html = (ROOT / 'templates' / 'trophies' / 'profile_list.html').read_text(encoding='utf-8')

    for primitive in ('pp-head-cascade', 'pp-tally', 'pp-toolbar-card', 'border-l-primary', 'pp-hbrowse'):
        assert primitive in html, f'{primitive} missing -- the page is not on the rebuild system'
    for legacy in ('select select-', 'btn btn-', 'badge badge-', 'loading loading-'):
        assert legacy not in html, f'legacy DaisyUI survived: {legacy}'


def test_the_grid_is_not_wrapped_in_an_outer_card():
    """Site-wide rule: STACKED chrome, FREE content. This page grows by 30 cards on every scroll, so an
    outer card would grow an ever-expanding border around the whole wall."""
    partial = (ROOT / 'templates' / 'trophies' / 'partials' / 'profile_list' / 'browse_results.html')
    src = partial.read_text(encoding='utf-8')
    src = re.sub(r'{% comment %}.*?{% endcomment %}', '', src, flags=re.S)

    assert 'items-grid' in src
    assert 'card bg-base-200' not in src, 'the results grid is wrapped in a card again'


def test_the_reveal_class_is_baked_in_on_htmx_swaps_only():
    """htmx's settle phase restores the swapped element's SERVER attributes after our afterSwap handler
    runs, so a `pp-reveal` added in JS is stripped -- which left infinite-scroll-appended cards visible in
    final position and then flashed them through the reveal. Baking it in makes settle preserve it, and
    gating on `request.htmx` keeps the JS-off / reduced-motion first paint from hiding everything."""
    src = (ROOT / 'templates' / 'trophies' / 'partials' / 'profile_list' / 'browse_results.html').read_text(encoding='utf-8')

    assert '{% if request.htmx %} pp-reveal{% endif %}' in src


def test_the_htmx_swap_returns_only_the_grid(client):
    """The filter/sort swap targets #browse-results, so the response must be the partial -- a full page
    would nest the whole document inside the results container."""
    _hunters(2)

    resp = client.get(URL, {'sort': 'alpha'}, HTTP_HX_REQUEST='true')
    body = resp.content.decode()

    assert resp.status_code == 200
    assert 'items-grid' in body
    assert '<html' not in body.lower(), 'the swap returned a full page'


def test_the_headline_tally_counts_up_on_arrival_and_on_every_filter():
    """Two separate paths, and having one is not having the other: the arrival roll happens once in `boot`,
    while a filter swap re-runs the count from the PREVIOUS value (`{from: countLast}`) so the number
    travels rather than jumping. `countUp` reads its target from `data-countup`, so the dataset has to be
    updated before the call, not after."""
    html = (ROOT / 'templates' / 'trophies' / 'profile_list.html').read_text(encoding='utf-8')

    assert 'PlatPursuit.countUp(countEl, 900)' in html, 'the Tally no longer rolls up on arrival'
    tick = html[html.index('function tickCount'):html.index('function boot')]
    assert "el.dataset.countup = next" in tick, 'the new total is never handed to countUp'
    assert 'from: countLast' in tick, 'a filtered count jumps instead of travelling from the old value'
    # Seeded at load, or the first filter swap animates from zero rather than from what is on screen.
    assert 'countLast = parseFloat(countEl.dataset.countup)' in html


def test_a_full_length_psn_id_fits_the_card():
    """A PSN id is up to 16 characters and the card exists to say WHO someone is, so a column narrow enough
    to ellipsis the name defeats the card. The grid took a fourth desktop column at a 13.5rem track, which
    left the name 106px; one column wider gives it 188px. Measured in a browser -- this pins the track that
    measurement produced, since the CSS is where it would silently drift back."""
    css = (ROOT / 'static' / 'css' / 'components' / 'profile-browse.css').read_text(encoding='utf-8')
    rule = re.search(r'(?m)^\.pp-hbrowse__grid\s*\{([^}]*)\}', css).group(1)

    track = re.search(r'minmax\(min\(100%,\s*([\d.]+)rem\)', rule)
    assert track, 'the grid no longer sizes its columns from a rem track'
    assert float(track.group(1)) >= 15, (
        f'{track.group(1)}rem tracks take an extra column and ellipsis a full-length PSN id'
    )


def test_the_toolbar_carries_its_own_box_metrics_like_the_other_browse_bars():
    """`.pp-toolbar-card` is SURFACE ONLY -- background, radius, border, shadow. It supplies no padding and
    no margin, so every page using it must set its own, and forgetting produces two symptoms that look
    unrelated: controls flush against the toolbar's border, and the card wall butted up under it.

    Pinned against Browse Games rather than against literals: the point is that the site's browse bars
    agree with each other, so if that one is ever retuned this fails instead of quietly diverging.
    """
    def metrics(css_file, selector):
        css = (ROOT / 'static' / 'css' / 'components' / css_file).read_text(encoding='utf-8')
        # Anchored to line start: both files ALSO carry a `.pp-Xbrowse .pp-Xbrowse__toolbar` rule (the
        # scoped shadow quiet) which appears first and contains the selector as a substring, so an
        # unanchored search reads that rule's body and reports the padding missing.
        rule = re.search(r'(?m)^' + re.escape(selector) + r'\s*\{([^}]*)\}', css)
        assert rule, f'{selector} not found in {css_file}'
        body = rule.group(1)
        pad = re.search(r'padding:\s*([^;]+);', body)
        mar = re.search(r'margin-bottom:\s*([^;]+);', body)
        assert pad, f'{selector} sets no padding -- the shared toolbar card supplies none'
        assert mar, f'{selector} sets no margin-bottom -- the grid will butt up under it'
        return pad.group(1).strip(), mar.group(1).strip()

    assert metrics('profile-browse.css', '.pp-hbrowse__toolbar') == \
           metrics('game-browse.css', '.pp-gbrowse__toolbar'), \
           'the hunter and game browse bars no longer sit the same'


def test_the_filter_dim_skips_text_inputs():
    """`change` on a search box fires on BLUR without submitting anything (only the debounced live-search
    or Enter submits), so dimming there strands the grid faded with no request coming to clear it."""
    html = (ROOT / 'templates' / 'trophies' / 'profile_list.html').read_text(encoding='utf-8')
    # Sliced FORWARD from the listener, not between two landmarks: "htmx:afterSwap" is also named in a
    # body comment further up the page, so anchoring the end on it produced an empty string that passed
    # nothing and failed loudly for the wrong reason.
    # Anchored on the form check, which is unique to the dim handler: the mini-bar's sort proxy also
    # binds a 'change' listener and now sits EARLIER in the file, so a first-occurrence slice reads that
    # one instead. Third time this file has been bitten by index-of-the-wrong-thing.
    start = html.index("t.closest('#hbrowse-form')")
    handler = html[start:start + 600]

    assert "type === 'text'" in handler, 'a text input can dim the grid with no request to undo it'


# ── Premium pass: the wall shows the axis it is ordered by ────────────────────────────────────────

@pytest.mark.parametrize('sort', ['recently_active', 'recently_joined', 'trophies', 'plats', 'alpha'])
def test_every_stat_has_a_permanent_home(client, sort):
    """No cell's CONTENT changes with the sort. An earlier cut made the third slot adaptive -- it became
    whatever you had sorted by -- and a card rearranging its own contents while the wall is also
    rearranging is two things moving at once, which read as jarring rather than as responsive.

    So all five live on the card at all times: achievements in one band, dates in another.
    """
    _hunters(2)

    body = client.get(URL, {'sort': sort}).content.decode()

    for label in ('Level', 'Trophies', 'Platinums', 'Games', 'Last seen', 'Joined'):
        assert label in body, f'{label} vanished when sorting by {sort}'


def test_the_card_does_not_change_height_between_sorts(client):
    """The point of permanence, stated as the property that actually matters: swapping sorts must not
    reflow the wall. Same slot count and same labels in the same order, whatever the ordering."""
    _hunters(1)

    shapes = set()
    for sort in ('recently_active', 'recently_joined', 'trophies', 'plats', 'alpha'):
        body = client.get(URL, {'sort': sort}).content.decode()
        shapes.add((body.count('pp-hcard__stat"'), body.count('pp-hcard__mcell"')))

    assert len(shapes) == 1, f'the card changes shape between sorts: {shapes}'


@pytest.mark.parametrize('sort,expected', [
    ('recently_active', 'Last seen'),
    ('recently_joined', 'Joined'),
    ('trophies', 'Trophies'),
    ('plats', 'Platinums'),
])
def test_the_accent_marks_the_sorted_stat(client, sort, expected):
    """What survives from the adaptive slot, and the only thing that still follows the sort: a hue moving
    between cells is legible where a content swap is jarring, and it keeps the thing that mattered --
    being able to see WHY a hunter sits where they do."""
    _hunters(1)

    body = client.get(URL, {'sort': sort}).content.decode()

    assert body.count('pp-hcard__val--sorted') == 1, 'the accent is spent more than once per card'
    # Windowed BOTH ways around the accent. The two bands order their parts differently on purpose --
    # the stats band leads with the value, the dates band leads with the label (a short phrase reads as a
    # sentence that way) -- so a tail-only search finds the label for one band and misses it for the other.
    at = body.index('pp-hcard__val--sorted')
    assert expected in body[max(0, at - 400):at + 400], f'sorting by {sort} does not accent {expected}'


def test_alphabetical_accents_the_name_and_no_stat(client):
    """Alphabetical sorts by the NAME, so the name carries the accent -- the same "this is why this hunter
    is here" the stat cells carry under every other ordering. It briefly highlighted Level, which has
    nothing to do with it, and then nothing at all; the axis was always on the card, just unmarked.

    No STAT may take it here, or the card would claim a relationship that is not there.
    """
    _hunters(1)

    body = client.get(URL, {'sort': 'alpha'}).content.decode()

    assert 'pp-hcard__name--sorted' in body, 'the name is the sort axis and is not marked'
    assert 'pp-hcard__val--sorted' not in body, 'a stat is accented under an ordering it does not explain'


@pytest.mark.parametrize('sort', ['recently_active', 'recently_joined', 'trophies', 'plats'])
def test_only_alphabetical_accents_the_name(client, sort):
    """Positive control for the pair above: under a stat ordering the name is neutral, so exactly one
    thing on the card is ever coloured for the sort."""
    _hunters(1)

    body = client.get(URL, {'sort': sort}).content.decode()

    assert 'pp-hcard__name--sorted' not in body, f'{sort} accents the name as well as its stat'


def test_the_default_sort_is_alphabetical(client):
    """The form's `selected` fallback hardcodes the default separately from the view's DEFAULT_SORT, so
    the two can drift into showing one ordering while building the wall with another."""
    _hunters(2)

    resp = client.get(URL)
    body = resp.content.decode()

    assert ProfilesListView.DEFAULT_SORT == 'alpha'
    assert 'value="alpha" selected' in body, 'the sort control does not show the default it is using'
    assert 'pp-hcard__name--sorted' in body, 'the default wall does not mark its own axis'


def test_a_supporter_card_is_marked(client):
    """The mark system (2026-08-22) replaced the flat card modifier: a supporter's name renders
    through the name_mark partial with their level's colour and stars, read off display_mark."""
    ProfileFactory(is_linked=True, user_is_premium=True, display_mark='patron')

    body = client.get(URL).content.decode()

    assert 'pp-hcard--supporter' not in body, 'the retired card modifier came back'
    assert 'pp-supname' in body and 'pp-supstar' in body, 'the mark did not reach the card'
    assert 'PlatPursuit Patron' in body


def test_a_plain_card_is_not_marked_as_a_supporter(client):
    ProfileFactory(is_linked=True, user_is_premium=False)

    assert 'pp-hcard--supporter' not in client.get(URL).content.decode()


def test_the_dates_band_costs_no_extra_queries(client):
    """`last_synced` and `created_at` were chosen for it precisely because both are already in the view's
    `.only(...)`, so the second band reads fields the row has already paid for."""
    title = Title.objects.create(name='Case Hardened')
    for profile in _hunters(4):
        UserTitle.objects.create(profile=profile, title=title, is_displayed=True)

    with CaptureQueriesContext(connection) as plain:
        client.get(URL, {'sort': 'plats'})
    with CaptureQueriesContext(connection) as dated:
        client.get(URL, {'sort': 'recently_active'})

    assert len(dated) == len(plain), 'the time-based sort costs extra queries'


def test_the_accent_survives_the_htmx_swap(client):
    """The grid is what re-renders on a sort change, so `active_sort` has to reach the PARTIAL -- without
    it the swapped-in cards come back with nothing marked while the toolbar says Recently Active."""
    _hunters(2)

    body = client.get(URL, {'sort': 'recently_active'}, HTTP_HX_REQUEST='true').content.decode()

    assert 'pp-hcard__val--sorted' in body, 'the swapped grid lost the sorted accent'


# ── Premium pass: mini-bar, skeletons, FLIP, scroll restore ──────────────────────────────────────

def test_the_minibar_controls_are_proxies_not_a_second_set_of_fields(client):
    """The pinned controls sit OUTSIDE the <form>. If they were real fields, a submit would carry two
    name="query" and two name="sort" values and the server would read whichever came last -- so they
    carry data-attributes only and JS mirrors them onto the real controls."""
    _hunters(1)

    body = client.get(URL).content.decode()
    bar = body[body.index('pp-minibar'):body.index('id="hbrowse-form"')]

    assert 'data-minibar-search' in bar and 'data-minibar-sort' in bar
    assert 'name="query"' not in bar, 'the mini-bar search is a real field and will double-submit'
    assert 'name="sort"' not in bar, 'the mini-bar sort is a real field and will double-submit'


def test_the_minibar_has_the_sentinel_it_pins_against(client):
    """StickyReveal needs BOTH halves: a [data-sticky-reveal] target and the [data-sticky-sentinel] it
    points at. The page carried a StickyReveal.init() call with neither for a while -- a dead call that
    looked like a working feature."""
    _hunters(1)

    body = client.get(URL).content.decode()

    assert 'data-sticky-sentinel="#hbrowse-minibar-sentinel"' in body
    assert 'id="hbrowse-minibar-sentinel"' in body, 'the mini-bar pins against a sentinel that is absent'


def test_the_skeleton_container_can_actually_be_hidden():
    """The trap the playbook documents, hit for real here. Tailwind's `.hidden` is in @layer utilities and
    these component files are @imported UNLAYERED, so `.pp-hbrowse__grid { display: grid }` beats it -- and
    the skeleton container wears both classes. Without the restatement the loading skeletons render
    permanently under the wall, while `classList.contains('hidden')` cheerfully reports true.
    """
    css = (ROOT / 'static' / 'css' / 'components' / 'profile-browse.css').read_text(encoding='utf-8')
    rules = re.sub(r'/\*.*?\*/', '', css, flags=re.S)

    assert '.pp-hbrowse__grid.hidden' in rules, 'the skeletons cannot be hidden by the .hidden utility'


def test_the_skeletons_mirror_the_card_and_are_hidden_from_assistive_tech(client):
    """A skeleton is a picture of loading. A screen reader gets the count that follows, not three empty
    articles announcing themselves."""
    _hunters(1)

    body = client.get(URL).content.decode()
    block = body[body.index('id="hbrowse-loading"'):]

    assert 'aria-hidden="true"' in body[body.index('id="hbrowse-loading"') - 200:body.index('id="hbrowse-loading"') + 200]
    assert block.count('pp-hskel"') == 3, 'the skeleton count no longer matches a grid row'
    for part in ('pp-hskel__avatar', 'pp-hskel__bar', 'pp-hskel__stats'):
        assert part in block, f'the skeleton lost its {part} -- it no longer mirrors the card'


def test_the_flip_measures_BEFORE_the_swap():
    """First-Last-Invert-Play only works if First is taken while the old cards are still on screen. Read
    after the swap, the old positions are gone and there is nothing to invert from -- the animation would
    silently become a no-op that still costs a frame.

    The mechanics moved to the shared `flipGrid` primitive in 2026-08 (this page, the Collection gallery
    and the jobs catalogue had three hand-rolled copies), so what this page still owns -- and what this
    test still guards -- is the WIRING: measure on the way out, play on the way back, and play before the
    reveal pass. The survivor-marking that keeps the wall from re-fading is the helper's job now and is
    asserted in `test_utils_flip_grid`.
    """
    html = (ROOT / 'templates' / 'trophies' / 'profile_list.html').read_text(encoding='utf-8')

    # Anchored on the LISTENER, not the event name: both names also appear in prose comments far above
    # the handlers, so slicing from the first occurrence slices from documentation. (Walked into exactly
    # that while moving this page onto the shared helper, which is why the anchor is now the call.)
    before = html[html.index("addEventListener('htmx:beforeSwap'"):]
    before = before[:before.index('});')]
    assert 'flipper.measure()' in before, 'the beforeSwap hook does not capture positions'

    after = html[html.index("addEventListener('htmx:afterSwap'"):]
    after = after[:after.index('});')]
    assert 'flipper.play()' in after, 'the afterSwap hook never plays the flip'
    assert after.index('flipper.play()') < after.index('initReveal()'), (
        'the reveal pass runs before the flip, so survivors are not yet marked and the whole wall re-fades'
    )


def test_the_scroll_restore_only_fires_on_the_same_wall():
    """Coming back to a DIFFERENT query is a new list, and dropping the reader into the middle of one they
    have not scrolled is disorienting rather than helpful. Restoring is also one-shot -- the key is removed
    as it is read, so a later plain visit opens at the top."""
    html = (ROOT / 'templates' / 'trophies' / 'profile_list.html').read_text(encoding='utf-8')
    fn = html[html.index('function restoreScroll'):html.index('function boot')]

    assert 'saved.url !== location.href' in fn, 'a restore can hijack a differently-filtered wall'
    assert 'removeItem' in fn, 'the saved card is never cleared, so it restores again later'
    assert "behavior: 'auto'" in fn, 'a smooth restore scrolls the reader back through content they walked'


def test_the_dates_band_accent_outweighs_the_bands_own_colour():
    """`.pp-hcard__val--sorted` and `.pp-hcard__mval` are BOTH single-class rules, so whichever is declared
    last wins -- and the band is declared after the accent. That silently swallowed it: sorting by Last
    seen or Joined highlighted nothing while the stats band highlighted fine."""
    css = (ROOT / 'static' / 'css' / 'components' / 'profile-browse.css').read_text(encoding='utf-8')
    rules = re.sub(r'/\*.*?\*/', '', css, flags=re.S)

    assert '.pp-hcard__mval.pp-hcard__val--sorted' in rules, (
        "the dates band's own colour outranks the accent again"
    )


def test_the_two_stat_bands_share_a_column_grid():
    """The dates band holds two cells and the stats band three, so the dates band must still be laid out on
    THREE columns or its second cell lands in a gutter of the row above -- which is what it did: stat
    columns at 0/33/66% and a date column at 50%. A near-miss reads worse than either aligning or clearly
    not aligning, and centring it only trades the problem for a third alignment axis on one small card.
    """
    css = (ROOT / 'static' / 'css' / 'components' / 'profile-browse.css').read_text(encoding='utf-8')
    rules = re.sub(r'/\*.*?\*/', '', css, flags=re.S)

    def tracks(selector):
        rule = re.search(r'(?m)^' + re.escape(selector) + r'\s*\{([^}]*)\}', rules)
        assert rule, f'{selector} not found'
        cols = re.search(r'grid-template-columns:\s*repeat\((\d+)', rule.group(1))
        assert cols, f'{selector} no longer lays out on a repeat() grid'
        return int(cols.group(1))

    assert tracks('.pp-hcard__meta') == tracks('.pp-hcard__stats'), (
        'the dates band and the stats band no longer share a column grid, so their cells will not line up'
    )
