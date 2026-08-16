"""Profile detail: what the page COSTS.

Phase 1 of the profile rebuild is a backend audit, and these are its findings turned into guards. The
page is public, anonymous-accessible and crawled, and it has already had a feature deleted for being the
most expensive thing an anonymous visitor could trigger -- so cost here is a correctness property, not a
nice-to-have.
"""
import re
from pathlib import Path

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from tests.engine.test_plat_cards import _completed_game
from tests.factories import ProfileFactory

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[2]
CF = {'HTTP_CF_RAY': '8f0000000000abcd-LHR'}


def _profile_with_games(n):
    profile = ProfileFactory(is_linked=True)
    for _ in range(n):
        _completed_game(profile, with_platinum=True)
    return profile


def test_the_games_tab_does_not_query_per_card(client):
    """`Game.display_image_url` resolves a trusted IGDB cover FIRST, so a grid of games walks
    Game -> Concept -> IGDBMatch for every row. With only `game` selected that was TWO extra queries per
    card: measured at 52 Concept + 52 IGDBMatch fetches on a 26-game page, ~104 of the tab's ~115 queries.

    Asserted as "does not grow with the number of games" rather than against a magic number, so the guard
    survives the rebuild changing what else the page loads.
    """
    small = _profile_with_games(2)
    with CaptureQueriesContext(connection) as few:
        client.get(f'/hunters/{small.psn_username}/?tab=games', **CF)

    big = _profile_with_games(8)
    with CaptureQueriesContext(connection) as many:
        client.get(f'/hunters/{big.psn_username}/?tab=games', **CF)

    assert len(many) <= len(few) + 2, (
        f'{len(few)} queries for 2 games, {len(many)} for 8 -- the grid queries per card.\n'
        + '\n'.join(q['sql'][:140] for q in many.captured_queries[-6:])
    )


def test_the_igdb_raw_response_is_never_hauled_PER_CARD(client):
    """The other half of the games fix, and the one that bites in memory rather than in query count.
    `raw_response` is the ~30 KB IGDB API blob no cover-art template reads; widening the join without
    deferring it trades a query storm for the May 2026 web-server OOM.

    Asserted as "does not scale", not "never happens": a configured showcase can still fetch a bounded row
    or two. What must never come back is the version that pays that cost once per card. The header's four
    platinum-highlight queries were the last unbounded-ish offenders and now defer it -- see below.
    """
    small = _profile_with_games(2)
    with CaptureQueriesContext(connection) as few:
        client.get(f'/hunters/{small.psn_username}/?tab=games', **CF)

    big = _profile_with_games(8)
    with CaptureQueriesContext(connection) as many:
        client.get(f'/hunters/{big.psn_username}/?tab=games', **CF)

    def blobs(ctx):
        return len([q for q in ctx.captured_queries if 'raw_response' in q['sql']])

    assert blobs(many) <= blobs(few), (
        f'{blobs(few)} raw_response queries for 2 games, {blobs(many)} for 8 -- the blob is per-card again'
    )


def test_the_games_queryset_selects_the_cover_chain_and_defers_the_blob():
    """Pinned in the SOURCE as well as by behaviour: the two must travel together, and a future edit that
    keeps the select_related while dropping the defer would pass the query-count test above while
    reintroducing the memory problem."""
    src = (ROOT / 'trophies' / 'views' / 'profile_views.py').read_text(encoding='utf-8')
    src = re.sub(r'#.*', '', src)          # the comment explains the fix and names both halves

    qs = src[src.index('games_qs = profile.played_games'):]
    qs = qs[:qs.index('.annotate(')]

    assert "'game__concept__igdb_match'" in qs, 'the cover chain is not selected, so each card re-queries'
    assert "defer('game__concept__igdb_match__raw_response')" in qs, 'the 30 KB blob is no longer deferred'


def test_the_dead_reviews_tab_is_gone():
    """`_build_reviews_tab_context` was never reachable -- the dispatcher has no `reviews` branch -- while
    the template maps still pointed at a reviews tab, so an HTMX `?tab=reviews` would render a reviews
    template against games context. The Review Hub was archived in 2026-05."""
    src = (ROOT / 'trophies' / 'views' / 'profile_views.py').read_text(encoding='utf-8')

    assert '_build_reviews_tab_context' not in src
    assert 'reviews_tab.html' not in src
    assert not (ROOT / 'templates/trophies/partials/profile_detail/tabs/reviews_tab.html').exists()
    assert not (ROOT / 'templates/trophies/partials/profile_detail/review_list_items.html').exists()


def test_every_mapped_tab_template_can_actually_be_built():
    """The invariant the reviews tab broke: a tab the template map offers must be one the dispatcher can
    build context for, or it renders someone else's data."""
    from trophies.views.profile_views import ProfileDetailView

    src = (ROOT / 'trophies' / 'views' / 'profile_views.py').read_text(encoding='utf-8')
    dispatch = src[src.index("if tab == 'games'"):]
    dispatch = dispatch[:dispatch.index('\n\n')]
    buildable = set(re.findall(r"tab == '(\w+)'", dispatch)) | {'games'}

    for mapped in ProfileDetailView._TAB_TEMPLATES:
        assert mapped in buildable, f"'{mapped}' has a template but no way to build its context"


def test_the_profile_page_never_hauls_the_igdb_blob(client):
    """No render of this page, on any tab, may drag `raw_response` -- the ~30 KB IGDB payload no cover
    chain reads.

    Written for the header's four platinum-highlight cards, three of which joined `concept__igdb_match`
    without the paired defer. Those cards are gone now (and so is the computation behind them), but the
    assertion outlives them: every tab still joins that relation for covers, and the standing rule is
    that the `select_related` and the `defer` travel together.
    """
    profile = ProfileFactory(is_linked=True)
    _completed_game(profile, with_platinum=True)

    with CaptureQueriesContext(connection) as ctx:
        client.get(f'/hunters/{profile.psn_username}/?tab=ratings', **CF)

    offenders = [q['sql'][:160] for q in ctx.captured_queries if 'raw_response' in q['sql']]
    assert not offenders, 'the IGDB blob is back on the profile header:\n  ' + '\n  '.join(offenders)


def test_every_paginated_tab_tells_the_scroller_its_real_page_size(client):
    """The scroller gates its first fetch on the grid holding a FULL page and resumes by counting the cards
    already there, so a size that disagrees with the server's does not render a wrong-sized page -- it
    disables the scroll, or re-fetches a page that is already on screen.

    The Games tab never set one. The template variable was named for the Trophies tab, which was the only
    tab that set it, so Games was measured against the template default of 30 while the server paged by 50.
    """
    from trophies.views.profile_views import ProfileDetailView

    profile = _profile_with_games(2)

    for tab in ProfileDetailView._INFINITE_SCROLL_TEMPLATES:
        response = client.get(f'/hunters/{profile.psn_username}/?tab={tab}', **CF)
        size = response.context.get('scroll_per_page')
        assert size, f"the {tab} tab appends pages but never tells the scroller how big one is"
        assert f'paginateBy: {size}' in response.content.decode()


def test_the_games_tab_uses_the_site_game_card(client):
    """The profile's games are the site's game card, not a profile-only row. The old card tinted its
    border, ring, title and shadow by PLATFORM, so a wall of games lit up in five hues -- decoration
    rather than information. Platform is a footer chip now, exactly as on Browse Games."""
    profile = _profile_with_games(2)

    body = client.get(f'/hunters/{profile.psn_username}/?tab=games', **CF).content.decode()

    # `.pp-gcard` is the Games tab's card selector in THREE inline-JS hooks (the scroller's append unit,
    # the reveal's animated element, and the bar animation), and a cardSelector that matches nothing
    # fails silently as "no more pages". Matched as a class among others, not as the whole attribute:
    # the library variant adds `--lib`, and pinning the exact attribute string would fail on a modifier
    # that is deliberately additive.
    assert 'pp-gcard pp-gcard--lib' in body, 'the games tab no longer uses the shared game card'
    assert 'hover:border-' not in body, 'per-platform hover tinting is back on the game cards'


def test_the_completion_bar_means_the_same_thing_it_does_on_browse(client):
    """The bar's five states encode the relationship with a game, and the profile fills it from the
    OWNER's progress rather than the viewer's. Sharing the classes is what keeps a colour meaning one
    thing across the site."""
    from trophies.models import ProfileGame

    profile = _profile_with_games(1)
    # Progress is set explicitly: the shared fixture builds the trophies but leaves ProfileGame.progress
    # at 0, and the bar only renders above 0 -- so without this the test would assert on a card state the
    # fixture never produces and pass or fail for reasons unrelated to the card.
    ProfileGame.objects.filter(profile=profile).update(progress=100, has_plat=True)

    body = client.get(f'/hunters/{profile.psn_username}/?tab=games', **CF).content.decode()

    assert 'pp-gcard__barfill--plat' in body, 'a finished game does not get the platinum bar state'


def test_the_dlc_count_is_one_grouped_query_not_one_per_card(client):
    """The DLC chip carries a COUNT, which is the kind of per-card fact that quietly becomes an N+1.
    It is built per PAGE in the view, bounded to the games actually shown, exactly as Browse Games does."""
    small = _profile_with_games(2)
    with CaptureQueriesContext(connection) as few:
        client.get(f'/hunters/{small.psn_username}/?tab=games', **CF)

    big = _profile_with_games(8)
    with CaptureQueriesContext(connection) as many:
        client.get(f'/hunters/{big.psn_username}/?tab=games', **CF)

    assert len(many) <= len(few) + 2, (
        f'{len(few)} queries for 2 games, {len(many)} for 8 -- something is counting per card'
    )


def test_the_card_does_not_carry_the_per_tier_breakdown(client):
    """Inverted 2026-08. The four tier counts used to be here on the grounds that they say which trophies
    are actually LEFT rather than just how many, and they cost nothing (both dicts are denormalized JSON).

    Cost was never the problem. They were four of the card's nine fields, all competing inside a ~240px
    column, and the feedback that triggered the reshape was that the record was hard to read. A per-tier
    breakdown is game-page detail; what someone comes to a profile for is how far this hunter got and
    whether they platted it. Cheap is not the same as worth the room.
    """
    profile = _profile_with_games(1)

    body = client.get(f'/hunters/{profile.psn_username}/?tab=games', **CF).content.decode()

    assert 'pp-pgcard__tier' not in body, 'the per-tier chips are back on the card'
    # The headline the card kept instead.
    assert 'pp-pgcard__pct' in body, 'the completion percentage is gone from the card'


# ── The hero's polish pass (2026-08) ──────────────────────────────────────────────────────────────

def test_the_level_and_its_progress_are_one_object(client):
    """The header's structural fix. The level figure sat top-right and its progress bar was a full-width
    band under the whole identity row, with the avatar and username between them -- a value separated
    from its own progress, which nothing that shows both (a battery, a ring, a storage bar) ever does.

    They are now one dial: the ring carries the progress, the level plate sits on the ring, the caption
    sits under both. Asserted by nesting, because the whole point is WHERE they are, not that they
    exist."""
    profile = ProfileFactory(is_linked=True, trophy_level=42, progress=68)

    html = client.get(f'/hunters/{profile.psn_username}/', **CF).content.decode()

    dial = html[html.index('pp-phero__dial'):]
    dial = dial[:dial.index('min-w-0 flex-1')]      # up to the name block, i.e. the dial alone
    for part in ('pp-phero__ring-fill', 'pp-phero__avatar', 'pp-phero__lvl', 'pp-phero__tonext'):
        assert part in dial, f'{part} is no longer part of the identity dial'

    # And the full-width bar it replaced is gone from the header.
    header = html[:html.index('pp-phero__tiers')]
    assert 'pp-horizon' not in header, 'the level progress is a full-width band again'


def test_the_ring_is_driven_by_the_real_progress_figure(client):
    """`pathLength="100"` makes the dash units percentages, so the figure goes straight on as `--lvl`
    with no circumference arithmetic. A wrong or missing value here draws a ring that is confidently
    incorrect, which is worse than no ring."""
    profile = ProfileFactory(is_linked=True, trophy_level=42, progress=68)

    html = client.get(f'/hunters/{profile.psn_username}/', **CF).content.decode()

    assert '--lvl: 68;' in html
    assert html.count('pathLength="100"') >= 2, 'the track and the fill must share the dash scale'


def test_a_never_synced_profile_draws_an_empty_ring_rather_than_no_ring(client):
    """Zero progress must still resolve `--lvl`. An UNDEFINED custom property invalidates the whole
    declaration it appears in rather than falling back, so a missing value would leave the fill with no
    `stroke-dashoffset` at all -- i.e. drawn FULL. A brand-new hunter must not be shown a completed
    level, and "0" and "absent" are the same thing to a template but opposites to the ring."""
    profile = ProfileFactory(is_linked=True, trophy_level=1, progress=0)

    html = client.get(f'/hunters/{profile.psn_username}/', **CF).content.decode()

    assert '--lvl: 0;' in html
    assert 'pp-phero__tonext' not in html, 'a "% to next level" caption with no progress to report'


def test_no_headline_figure_is_stated_twice(client):
    """Platinums used to be a `.scard` AND a cell in the tier row -- the same number in two formats on a
    surface whose whole job is standing. Average completion meanwhile hung off the right end of that tier
    row, which is otherwise entirely about trophy tiers. The duplicate left; the stray took its slot."""
    profile = ProfileFactory(is_linked=True)

    html = client.get(f'/hunters/{profile.psn_username}/', **CF).content.decode()
    header = html[:html.index('id="profile-tab-bar"')]

    assert '>Platinums<' not in header, 'the platinum count is back in the stat grid AND the tier row'
    assert 'Avg. completion' in header, 'average completion has no home again'
    # It still has to BE in the tier row -- that row is read as a shape, and dropping its widest-to-
    # narrowest tip would break the shape rather than tidy it.
    tiers = header[header.index('pp-phero__tiers'):]
    assert 'data-tier="platinum"' in tiers


def test_every_figure_in_the_hero_actually_counts_up(client):
    """The hero marked all of them `data-countup` and none of them ever animated: the page's script only
    drove `#tab-content [data-countup]`, and this header sits outside that panel. So the surface had been
    missing the opening beat every other rebuilt page has, while carrying the markup that claims it."""
    src = (ROOT / 'templates' / 'trophies' / 'profile_detail.html').read_text(encoding='utf-8')

    assert "'#tab-content [data-countup]" not in src, (
        'the count-up pass is scoped to the tab panel again, which excludes the hero'
    )
    assert "querySelectorAll('[data-countup]:not([data-counted])')" in src
    # The marker is what makes the wider scope safe -- without it, every filter swap would replay the
    # animation on figures that did not change.
    assert "setAttribute('data-counted', '1')" in src


def test_the_trophy_shape_sits_across_from_the_identity(client):
    """Folding the level into the ring emptied the identity row's right-hand corner. The tier split moved
    into it -- the row's THIRD COLUMN, across from the name -- out of a bordered row at the foot of the
    card.

    Placement IS the change here, so this asserts the structure rather than mere presence: the tiers must
    be a SIBLING of the name block inside the identity row, not a child of it. Both arrangements put the
    same markup between the sync line and the stat grid, so 'appears somewhere in between' would pass on
    either and prove nothing."""
    profile = ProfileFactory(is_linked=True, total_plats=12, total_golds=140,
                             total_silvers=380, total_bronzes=1400)

    html = client.get(f'/hunters/{profile.psn_username}/', **CF).content.decode()

    between = html[html.index('pp-phero__sync'):html.index('pp-phero__tiers')]
    assert '</div>' in between, (
        'the trophy shape is back INSIDE the name block -- it should be the column beside it'
    )

    row = html[html.index('pp-phero__dial'):html.index('scard__label')]
    for tier in ('platinum', 'gold', 'silver', 'bronze'):
        assert f'data-tier="{tier}"' in row, f'{tier} is missing from the shape'


def test_the_trophy_shape_drops_below_on_a_phone(client):
    """A third column at 375px would squeeze the username to nothing, and the username is the one thing on
    this card that cannot be compromised. The identity row wraps and the block takes a full line of its
    own; it only becomes the right-hand column from `md:` up."""
    css = (ROOT / 'static' / 'css' / 'components' / 'profile-hero.css').read_text(encoding='utf-8')
    src = (ROOT / 'templates' / 'trophies' / 'partials' / 'profile_detail'
           / 'profile_detail_header.html').read_text(encoding='utf-8')

    assert 'flex items-start flex-wrap' in src, 'the identity row cannot wrap, so the tiers cannot drop'

    base = css[css.index('.pp-phero__tiers {'):]
    base = base[:base.index('\n}')]
    assert 'flex-basis: 100%' in base, 'the tiers share a line with the name block on a phone'

    # Sliced to the tiers' OWN md: rule rather than to a byte window after the first 768px block:
    # a fixed window is measured in characters, so adding a line of comment to that block would fail
    # this while the CSS stayed correct -- and a sibling rule in the same block could satisfy it.
    md = css[css.index('@media (min-width: 768px) {', css.index('.pp-phero__tiers {')):]
    rule = md[md.index('.pp-phero__tiers {'):]
    rule = rule[:rule.index('}')]
    assert 'flex-direction: column' in rule, 'the tiers are not a column at md:'


def test_the_trophy_counts_count_up_like_every_other_figure(client):
    """They were the only figures in the hero rendered as static text. Everything else here animates in,
    so four numbers that simply appeared read as a different kind of thing -- and these are the ones a
    hunter is proudest of."""
    profile = ProfileFactory(is_linked=True, total_plats=12, total_golds=140,
                             total_silvers=380, total_bronzes=1400)

    html = client.get(f'/hunters/{profile.psn_username}/', **CF).content.decode()
    tiers = html[html.index('pp-phero__tiers'):html.index('scard__label')]

    for value in (12, 140, 380, 1400):
        assert f'data-countup="{value}"' in tiers, f'{value} does not count up'
    # The rendered fallback is still the formatted figure, so a no-JS reader sees 1,400 rather than 1400.
    assert '>1,400<' in tiers


# ── The Games tab's filter bar, rebuilt to the browse standard ────────────────────────────────────

def test_the_games_filter_bar_uses_the_browse_vocabulary(client):
    """It was the last pre-rebuild filter bar on the site -- DaisyUI throughout, a `<details>` for the
    advanced filters, and an explicit Filter button, next to a Browse Games toolbar that had none of
    those. Matching the vocabulary is what buys the shared BEHAVIOUR, not just the look."""
    profile = _profile_with_games(2)

    html = client.get(f'/hunters/{profile.psn_username}/?tab=games', **CF).content.decode()

    # The drawer classes (`pp-bgal__advbtn`, `pp-bgal__advanced`) are deliberately NOT here any more --
    # see test_the_games_toolbar_has_no_drawer below.
    for cls in ('pp-toolbar-card', 'pp-gbrowse__bar', 'pp-bgal__search', 'pp-bgal__chip'):
        assert cls in html, f'{cls} is missing -- the bar is off the browse vocabulary'

    bar = html[html.index('id="games-form"'):html.index('id="tab-results"')]
    assert '<details' not in bar, 'the advanced filters are back behind a <details>'
    assert 'select-bordered' not in bar and 'peer-checked:btn' not in bar, 'DaisyUI controls are back'
    assert 'id="submit-btn"' not in bar, 'the explicit Filter button is back'


def test_the_games_search_opts_into_the_shared_affordances(client):
    """Live search, the spinner, Escape-to-clear, the clear button and the global `/` focus shortcut are
    all opted into by MARKUP -- the old bar had a hand-rolled field and inherited none of them, which is
    the same trap the Trophies tab's search hit during its rebuild."""
    profile = _profile_with_games(2)

    html = client.get(f'/hunters/{profile.psn_username}/?tab=games', **CF).content.decode()
    bar = html[html.index('id="games-form"'):html.index('id="tab-results"')]

    assert 'data-live-search' in bar, 'the search does not live-filter'
    assert 'data-page-search' in bar, 'the / and Cmd-K shortcuts skip this field'
    assert 'data-search-wrap' in bar and 'data-search-clear' in bar, 'no clear affordance'
    assert 'pp-search-spin' in bar, 'no in-field busy indicator'


def test_the_games_toolbar_has_no_drawer():
    """The Games tab came OFF the shared filter panel in 2026-08, which is a deliberate partial reversal
    of the consolidation below and not an oversight.

    The tab had inherited Browse Games' discovery apparatus wholesale -- genre and theme pickers,
    community rating / difficulty / fun ranges, a time-to-beat range, shovelware, and eight unrendered
    `show_*`/`hide_*` flags -- on a surface that is a record of what somebody played. Those answer "what
    should I play next", which is a question about a catalogue. Four controls remain, and four controls
    are a toolbar, not a collapse. `filterPanel` still serves the five genuine browse surfaces.
    """
    src = (ROOT / 'templates' / 'trophies' / 'profile_detail.html').read_text(encoding='utf-8')
    filters = (ROOT / 'templates' / 'trophies' / 'partials' / 'profile_detail'
               / 'game_filters.html').read_text(encoding='utf-8')

    assert 'PlatPursuit.filterPanel({' not in src, 'the Games tab wired a filter drawer again'
    for hook in ('pgames-filters-toggle', 'pgames-advanced', 'pgames-filter-count'):
        assert f'id="{hook}"' not in filters, f'the drawer part {hook} is back in the markup'

    # The teardown has to survive the removal: a hunter can reach Games carrying a handle built by an
    # earlier tab or a cached page, and an orphan keeps listeners bound to a form node that is gone.
    assert 'panelHandle.destroy()' in src


def test_the_games_settle_survived_losing_the_drawer():
    """The trap in dropping `filterPanel`: its `dimTarget: '#tab-results'` was the ONLY thing dimming this
    tab's results. browse-filters.js hard-codes `#browse-results` on both the add and the remove, so
    removing the drawer would have silently retired the filter/sort settle on Games alone and left the
    `#tab-results.is-swapping` rule in browse-gallery.css with nothing to apply it.

    Only the change-time half needs JS -- the request-time dim comes from `hx-indicator="#tab-results"`,
    which htmx answers with `.htmx-request`, styled next to `.is-swapping`. What a request cannot cover is
    the live-search debounce BEFORE it fires, which is most of the felt latency.
    """
    src = (ROOT / 'templates' / 'trophies' / 'profile_detail.html').read_text(encoding='utf-8')
    css = (ROOT / 'static' / 'css' / 'components' / 'browse-gallery.css').read_text(encoding='utf-8')
    filters = (ROOT / 'templates' / 'trophies' / 'partials' / 'profile_detail'
               / 'game_filters.html').read_text(encoding='utf-8')

    assert "getElementById('tab-results')" in src and "add('is-swapping')" in src, (
        'nothing adds the settle class to the Games results any more'
    )
    assert '#tab-results.is-swapping' in css, 'the settle rule the JS depends on is gone'
    assert 'hx-indicator="#tab-results"' in filters, 'the request-time half of the settle is gone'


def test_no_browse_surface_rolls_its_own_filter_panel():
    """The consolidation, pinned. Six surfaces had grown five copies of the same ~80 lines and they had
    already drifted -- different SKIP sets, only some wiring the chip-list scroll fades. A sixth copy is
    how that starts again.

    `profile_detail.html` is checked but NOT required to use the helper: the Games tab left the drawer
    pattern in 2026-08 (see above). What still must not happen anywhere is a surface re-implementing it.
    """
    on_the_helper = [
        ROOT / 'templates' / 'trophies' / 'game_list.html',
        ROOT / 'templates' / 'trophies' / 'badge_list.html',
        ROOT / 'static' / 'js' / 'company-list.js',
        ROOT / 'static' / 'js' / 'recently-added.js',
        ROOT / 'static' / 'js' / 'tag-detail.js',
    ]
    for path in on_the_helper:
        src = path.read_text(encoding='utf-8')
        assert 'function setPanel' not in src, f'{path.name} has its own panel implementation again'
        assert 'PlatPursuit.filterPanel' in src, f'{path.name} is not on the shared controller'

    profile = (ROOT / 'templates' / 'trophies' / 'profile_detail.html').read_text(encoding='utf-8')
    assert 'function setPanel' not in profile, 'profile_detail.html hand-rolled a panel instead'


def test_every_tab_wall_lands_rather_than_appearing(client):
    """`staggerReveal` drives 17 card walls across the site -- including the hunter wall this page is
    reached FROM. All four profile tabs were the only ones that just appeared, so clicking through to a
    hunter made the cards stop landing.

    The reveal class is baked into each grid's own partial gated on `request.htmx`, NOT added from JS
    after the swap: htmx settle restores server attributes on id'd elements, so a class added in
    afterSwap is wiped -- which un-hides the cards for a frame and flashes."""
    from pathlib import Path

    # DISCOVERED, not enumerated. The hand-written list of five missed `games_results.html` -- the swap
    # partial behind every Games filter, sort and search, i.e. the most-exercised path on the page -- and
    # a list is exactly the thing that goes stale when a grid moves.
    base = ROOT / 'templates' / 'trophies' / 'partials' / 'profile_detail'
    grids = [p for p in base.rglob('*.html')
             if re.search(r'id="(games|ratings|trophies)-grid"|data-badge-wall', p.read_text(encoding='utf-8'))]

    # Guarded by IDENTITY, not by a count. The count used to be 6 because Games rendered its wall twice
    # (tab + results partial); consolidating onto the include made it 5, and a raw floor would have read
    # that improvement as a missing wall. Naming the walls survives that, and still fails if the glob
    # silently stops matching one.
    found = {p.name for p in grids}
    for wall in ('games_results.html', 'ratings_results.html', 'trophies_results.html'):
        assert wall in found, f'{wall} no longer renders a wall -- the reveal check would skip it'
    assert any('badge' in name for name in found), 'the badge wall was not discovered'
    for path in grids:
        src = path.read_text(encoding='utf-8')
        assert '{% if request.htmx %} pp-reveal{% endif %}' in src, (
            f'{path.name} does not bake in the reveal class, so its wall will flash on a swap'
        )

    page = (ROOT / 'templates' / 'trophies' / 'profile_detail.html').read_text(encoding='utf-8')
    assert 'PlatPursuit.staggerReveal({' in page, 'the profile walls no longer land'
    # Appended pages reveal on scroll rather than all at once. Asserted on the CALL, not its argument:
    # what gets handed over is resolved per tab (see the activity-wall test below).
    assert 'revealHandle.observe(' in page


def test_the_activity_wall_reveals_the_tile_not_its_contents_wrapper():
    """The scroller's append unit and the reveal's animated element are DIFFERENT questions, and on the
    Activity day wall they have different answers.

    `.pp-act__cell` is `display: contents` -- it exists so a month header travels with its tile as one
    appendable thing, and it generates no box. Pointed at it, the reveal animated nothing and put
    `.is-revealed` on an element no CSS reads, so every `.pp-gtile` stayed at `opacity: 0` from
    `.pp-reveal .pp-gtile` with its layout space still reserved: an invisible wall of correctly-sized
    holes. Collapsing the two selectors into one is exactly that bug, so this pins them apart."""
    src = (ROOT / 'templates' / 'trophies' / 'profile_detail.html').read_text(encoding='utf-8')

    sel = src[src.index("var cardSel = '.card', revealSel = '.card';"):]
    sel = sel[:sel.index('var gridEl')]
    assert "cardSel = isActivity ? '.pp-act__cell'" in sel, 'the scroller lost its append unit'
    assert "revealSel = isActivity ? '.pp-gtile'" in sel, (
        'the reveal is pointed at the display:contents wrapper again -- it cannot animate a boxless element'
    )

    # The scroller takes the append unit; the reveal takes the element with a box.
    assert 'cardSelector: cardSel' in src and 'cardSelector: revealSel' in src

    # And the same distinction has to hold on the APPEND path: the nodes handed back are cells, so the
    # reveal is handed what it animates rather than what was appended.
    append = src[src.index('onAppend: function (nodes)'):]
    append = append[:append.index('formSelector')]
    assert 'nd.querySelectorAll(revealSel)' in append, (
        'appended activity days are observed as cells, so they will never reveal'
    )


def test_each_card_measure_fills_as_it_lands():
    """The reveal's point is not the fade -- it is that the thing which animates is the thing the card is
    ABOUT. Games fills its completion bar (a solid bar, so it scales); Ratings sweeps its star bar (five
    glyphs, so it cannot scale without stretching them into non-stars, and animates width instead -- safe
    because the overlay is absolutely positioned and lays out only itself)."""
    src = (ROOT / 'templates' / 'trophies' / 'profile_detail.html').read_text(encoding='utf-8')
    reveal = src[src.index('PlatPursuit.staggerReveal({'):]
    # Bounded by the end of the staggerReveal call itself rather than by whatever comment happens to
    # follow it. The previous boundary was `// Dual-range sliders`, which vanished when the Games filter
    # reduction removed the last dual-range on the page -- a slice anchored on unrelated neighbouring code
    # breaks on edits that have nothing to do with what it is testing.
    reveal = reveal[:reveal.index('\n            }\n')]

    assert '.pp-gcard__barfill' in reveal and 'scaleX(0)' in reveal, 'the completion bar no longer fills'
    assert '.pp-stars__on' in reveal and "width: '0%'" in reveal, 'the star bar no longer sweeps'
    # Both trail the card rather than firing with it, so each reads as a consequence of the card landing.
    assert reveal.count('delayMs + 150') == 2


# ── The 2026-08 reduction: the tab is a record, not a browse page ─────────────────────────────────

def _library(profile, **kwargs):
    """One game on a profile, with its ProfileGame row forced to a specific state.

    `_completed_game` leaves `ProfileGame.progress` at 0 whatever the group standing says, so a status
    test built on it alone would assert against rows that never reach the state being filtered for -- and
    pass or fail for reasons unrelated to the filter.
    """
    from trophies.models import ProfileGame
    game, _, _ = _completed_game(profile, with_platinum=kwargs.pop('with_platinum', True),
                                 name=kwargs.pop('name', None))
    ProfileGame.objects.filter(profile=profile, game=game).update(**kwargs)
    return game


def _titles_on(client, profile, qs=''):
    body = client.get(f'/hunters/{profile.psn_username}/?tab=games{qs}', **CF).content.decode()
    return set(re.findall(r'class="pp-gcard pp-gcard--lib" title="([^"]+)"', body))


@pytest.mark.parametrize('status,expected', [
    ('',           {'Platted', 'Chasing', 'Started'}),
    ('plat',       {'Platted'}),
    ('full',       {'Platted'}),
    ('chase',      {'Chasing'}),
    ('unfinished', {'Chasing', 'Started'}),
])
def test_the_status_filter_speaks_the_completion_bars_states(client, status, expected):
    """One control replacing four. `game_has_plat`, `plat_earned`, `is_100` and the completion range were
    all asking how far somebody got, and the card already answered that with a five-state bar -- so these
    options ARE those states, and picking one fills the wall with a single bar colour.

    Note "chase" excludes Started: the game must HAVE a platinum to still be chased. That is the
    distinction the old `game_has_plat` select carried, folded in here rather than dropped.
    """
    profile = ProfileFactory(is_linked=True)
    _library(profile, name='Platted', with_platinum=True, progress=100, has_plat=True)
    _library(profile, name='Chasing', with_platinum=True, progress=40, has_plat=False)
    _library(profile, name='Started', with_platinum=False, progress=30, has_plat=False)

    assert _titles_on(client, profile, f'&status={status}') == expected


def test_a_bookmarked_removed_sort_still_renders_the_wall(client):
    """The silent trap in the reduction, and the one that would have hit the tab's heaviest users first.

    Eleven sorts were removed. `sort` had been a ChoiceField, which rejects anything outside `choices` in
    `validate()` -- and the view answers an invalid form with an EMPTY game list. So a bookmarked
    `?sort=rating` would have rendered an empty Games tab rather than a default-sorted one, with no error
    anywhere. Both fields are CharFields now so the value can be coerced instead.
    """
    profile = ProfileFactory(is_linked=True)
    _library(profile, name='Kept', progress=50)

    for gone in ('rating', 'time_to_beat', 'plat_rarest', 'latest_trophy', 'unearned', 'nonsense'):
        assert _titles_on(client, profile, f'&sort={gone}') == {'Kept'}, (
            f'?sort={gone} empties the wall instead of falling back to the default'
        )
    # Same for the new field: no link in the wild can carry a valid value for it.
    assert _titles_on(client, profile, '&status=nonsense') == {'Kept'}


def test_removed_filter_params_are_ignored_not_fatal(client):
    """Unknown GET keys are simply not read, so old links keep working -- asserted rather than assumed,
    because it is the difference between a stale bookmark degrading and breaking."""
    profile = ProfileFactory(is_linked=True)
    _library(profile, name='Kept', progress=50)

    stale = '&rating_min=3&genres=1&themes=2&igdb_time_max=40&filter_shovelware=on&is_100=yes'
    assert _titles_on(client, profile, stale) == {'Kept'}


def test_the_discovery_controls_are_gone_from_the_form():
    """Reduced at the FORM, not just hidden in the template -- a field left declared is a filter that
    still runs for anyone who guesses the querystring, which is exactly what the eight unrendered
    `show_*`/`hide_*` community flags had become."""
    from trophies.forms import ProfileGamesForm

    fields = set(ProfileGamesForm().fields)
    assert fields == {'query', 'platform', 'status', 'sort'}, f'unexpected fields: {sorted(fields)}'
    assert len(ProfileGamesForm.SORT_CHOICES) == 6, 'the sort list grew back'


def test_the_form_no_longer_queries_for_genre_and_theme_choices():
    """The removed `__init__` ran a Genre AND a Theme `values_list` on EVERY instantiation -- two queries
    per render of this tab, for two pickers that had no business on a profile."""
    from trophies.forms import ProfileGamesForm

    with CaptureQueriesContext(connection) as q:
        ProfileGamesForm({})
    assert len(q) == 0, f'building the form costs {len(q)} queries'


def test_the_card_carries_both_a_backdrop_band_and_the_cover(client):
    """Two images doing two different jobs. The band is atmosphere; the COVER is how you recognise a game
    at a glance -- two shooters' screenshots look alike in a way their box art never does, so a landscape
    strip alone answers "which game is this" worse than the poster it replaced.

    A concept with no landscape art gets NO band rather than a fallback: with the cover on the card,
    falling the band back to that same cover would print one image twice.
    """
    profile = ProfileFactory(is_linked=True)
    _library(profile, name='NoLandscape', progress=50)

    body = client.get(f'/hunters/{profile.psn_username}/?tab=games', **CF).content.decode()

    # This fixture's concept has no IGDB imagery, so it exercises the bandless path.
    assert 'pp-pgcard__cover' in body, 'the card lost the cover art'
    assert 'pp-gcard__cover' not in body, 'a band rendered with no landscape art to put in it'


def test_the_band_is_a_fixed_height_strip_not_an_aspect_ratio():
    """The first cut of this card set the band to `aspect-ratio: 16/9`, which SCALES with the card -- at
    four columns (~367px) that is a 206px band, as dominant as the poster it was meant to replace, and it
    got taller the wider the wall. A height in px stays a strip at every column count.

    Pinned because the mistake is invisible in review: `16/9` reads like "a wide strip" right up until you
    multiply it by the column width.
    """
    css = (ROOT / 'static' / 'css' / 'components' / 'profile-hero.css').read_text(encoding='utf-8')
    band = css[css.index('.pp-gcard--lib .pp-gcard__cover'):]
    band = band[:band.index('\n')]

    assert 'height:' in band and 'px' in band, 'the band has no fixed height'
    assert '16 / 9' not in band and 'aspect-ratio: 1' not in band, (
        'the band is back on an aspect ratio, so it grows with the card width again'
    )


def test_the_library_wall_stays_far_narrower_than_the_browse_wall():
    """Column count and art shape are ONE decision. Fewer, wider columns is only a fix if the art stops
    being a poster -- otherwise the cover grows with the card and the complaint gets worse.

    Asserted as a RATIO against the browse ladder rather than against a literal count, because the exact
    steps are a tuning dial (they were trimmed by one in 2026-08 when `lg` turned out to be the pinch: 3
    columns of a 992px well is a 322px card, narrower than the same card gets at tablet). What must not
    drift is the relationship -- a catalogue is scanned by cover art and packs in; a library is read.
    """
    css = (ROOT / 'static' / 'css' / 'components' / 'game-browse.css').read_text(encoding='utf-8')
    lib = css[css.index('.pp-gbrowse__grid--lib'):]
    browse = css[css.index('.pp-gbrowse__grid {'):css.index('.pp-gbrowse__grid--lib')]

    def widest(block):
        counts = [int(n) for n in re.findall(r'repeat\((\d+), minmax', block)]
        return max(counts + [1])

    assert 'grid-template-columns: minmax(0, 1fr);' in lib.split('@media')[0], (
        'the base is not a single column, so mobile is a narrow grid rather than a list'
    )
    # At least two steps less dense than browse. Deliberately a margin rather than a ratio: the exact
    # count is the product's call (it was tuned from 4 to 3 to 5 to 4 in one sitting), and encoding
    # "half" would have been the test asserting a preference nobody chose.
    assert widest(lib) + 2 <= widest(browse), (
        f'the library wall runs {widest(lib)} columns against browse\'s {widest(browse)} -- it is drifting '
        'back toward a catalogue wall'
    )


def test_the_games_card_keeps_the_three_js_hooks_it_is_selected_by():
    """The failure mode here is SILENT, which is why it is pinned. `profile_detail.html` names `.pp-gcard`
    as the scroller's append unit and the reveal's animated element, and `.pp-gcard__barfill` as the thing
    the reveal fills -- and a `cardSelector` matching nothing reads as "no more pages" rather than as an
    error. This page has already lost its Games grid and its whole activity wall to exactly that."""
    card = (ROOT / 'templates' / 'trophies' / 'partials' / 'profile_detail'
            / 'game_list_items.html').read_text(encoding='utf-8')
    src = (ROOT / 'templates' / 'trophies' / 'profile_detail.html').read_text(encoding='utf-8')

    assert 'pp-gcard pp-gcard--lib' in card
    assert 'pp-gcard__barfill' in card
    assert "cardSel = revealSel = '.pp-gcard'" in src, (
        'the games tab selectors moved off .pp-gcard -- the card class must move with them'
    )


def test_the_two_library_card_layouts_differ_only_in_placement():
    """The desktop card and the mobile row are ONE grid re-placed, not two layouts.

    Both arrange the same four areas (title / foot / stats / bar); the mobile block overrides only
    `grid-template-columns`, `grid-template-areas` and the alignment. Everything else -- which element
    owns which area, the one-line title clamp, the dropped footer divider -- is declared once on the base
    rule, so the two cannot drift apart on anything except where the parts land.

    Pinned because the drift is silent and one-directional: a fix applied to the block you happen to be
    looking at leaves the other layout quietly wrong, and only one of the two is on screen at a time.
    """
    import re
    from pathlib import Path

    css = (Path(__file__).resolve().parents[2] / 'static' / 'css' / 'components'
           / 'profile-hero.css').read_text(encoding='utf-8')

    mobile = css[css.index('/* Mobile: the card turns into a list row.'):]
    mobile = mobile[:mobile.index('\n}\n', mobile.index('@media (max-width: 767px)')) + 3]
    base = css.replace(mobile, '')

    # Every area assignment lives on the base rule, none inside the mobile block.
    assigned = set(re.findall(r'grid-area:\s*(\w+)', base))
    assert {'title', 'foot', 'stats', 'bar'} <= assigned, (
        f'the base card does not place all four areas: missing {sorted({"title", "foot", "stats", "bar"} - assigned)}'
    )
    assert 'grid-area:' not in mobile, (
        're-placing areas inside the mobile block duplicates the base assignments, which is how the two '
        'layouts start drifting'
    )

    # The one-line title and the dropped divider are shared decisions, not per-breakpoint ones.
    title_rule = base[base.index('.pp-gcard--lib .pp-gcard__title'):]
    title_rule = title_rule[:title_rule.index('}')]
    assert 'line-clamp: 1' in title_rule and 'min-height: 0' in title_rule, (
        'the library title is back on the browse card two-line reserve'
    )
    assert 'border-top: none' in base, 'the footer divider is back on the desktop card'

    # And the two arrangements are genuinely different -- otherwise the override is dead weight.
    assert 'grid-template-areas' in mobile, 'the mobile row no longer re-places anything'


def test_the_desktop_library_card_leads_with_identity_then_progress():
    """Order is the point of the arrangement, not just compactness: the name and the platform/plat chips
    are what the game IS, the percentage and bar are how far this hunter got. Chips used to sit last,
    under a divider, while the title held an empty second line above them."""
    import re
    from pathlib import Path

    css = (Path(__file__).resolve().parents[2] / 'static' / 'css' / 'components'
           / 'profile-hero.css').read_text(encoding='utf-8')

    areas = re.search(r'\.pp-pgcard__meta\s*\{[^}]*grid-template-areas:\s*([^;]+);', css).group(1)
    order = re.findall(r'"([^"]+)"', areas)
    assert order == ['title', 'foot', 'stats', 'bar'], f'desktop row order is {order}'


def _hero_phone_block():
    """The header's `max-width: 767px` block, parsed into (selectors, body) pairs."""
    css = (ROOT / 'static' / 'css' / 'components' / 'profile-hero.css').read_text(encoding='utf-8')
    block = css[css.index('---- Phone fit'):]
    block = block[:block.index('\n}\n', block.index('@media (max-width: 767px)')) + 3]
    inner = block[block.index('{', block.index('@media')) + 1: block.rindex('}')]
    inner = re.sub(r'/\*.*?\*/', '', inner, flags=re.S)
    return block, [
        ([s.strip() for s in sel.split(',')], body)
        for sel, body in re.findall(r'([^{}]+)\{([^{}]*)\}', inner)
    ]


def test_the_hero_phone_block_parses():
    """Guards the guards: an empty parse would let both assertions below pass over nothing."""
    block, rules = _hero_phone_block()
    assert '@media (max-width: 767px)' in block
    assert len(rules) >= 8, f'only {len(rules)} rules parsed out of the hero phone block'


def test_the_phone_hero_fills_the_corner_beside_the_dial(client):
    """The header stood ~365px at 375px -- over half an iPhone SE before the tab bar, on a page whose
    content IS the tabs. The dial column runs ~110px (ring + level plate + caption) against a ~64px name
    column, and the tier block -- almost exactly the size of that dead corner -- was stacking BELOW both
    rather than filling it.

    So the identity row is a grid on a phone with the dial spanning both rows. Asserted on the AREAS
    rather than on a height, because height is what a browser computes and this is the arrangement that
    produces it.
    """
    _block, rules = _hero_phone_block()
    placed = {}
    for sels, body in rules:
        if 'grid-area:' in body:
            area = body.split('grid-area:')[1].split(';')[0].strip()
            for s in sels:
                placed[s] = area

    assert placed.get('.pp-phero__dial') == 'dial'
    assert placed.get('.pp-phero__who') == 'name'
    assert placed.get('.pp-phero__tiers') == 'tiers'

    areas = next(b for _s, b in rules if 'grid-template-areas' in b)
    assert '"dial name" "dial tiers"' in areas.replace('\n', ' '), (
        f'the dial no longer spans both rows, so the corner beside it goes empty again: {areas}'
    )

    # Every element the grid places must actually exist in the rendered header.
    profile = ProfileFactory(is_linked=True)
    body = client.get(f'/hunters/{profile.psn_username}/', **CF).content.decode()
    for cls in ('pp-phero__id', 'pp-phero__dial', 'pp-phero__who', 'pp-phero__tiers', 'pp-phero__stats'):
        assert cls in body, f'{cls} is not rendered, so the phone grid places a element that is not there'


def test_the_phone_hero_places_areas_by_class_not_by_position():
    """A structural selector here is a live trap. `.pp-phero__id > div:not(.pp-phero__dial)` reads as "the
    name block", but it also matches `.pp-phero__tiers` -- and at HIGHER specificity than
    `.pp-phero__tiers` itself, so the tiers would take the name area and stack on top of the username.

    Pinned as "no structural child selectors in this block" because the bug is invisible in review: the
    selector describes the intent correctly and matches one element too many.
    """
    _block, rules = _hero_phone_block()
    for sels, _body in rules:
        for sel in sels:
            assert '>' not in sel, (
                f'{sel!r} places a grid area by position. Name the element instead -- a child selector '
                f'here matched the tier block as well as the name block, at higher specificity than the '
                f'tier block own rule.'
            )


def test_the_stat_card_squeeze_lives_in_the_primitive_not_per_surface():
    """The phone squeeze on `.scard` started here, scoped, because reshaping a primitive on 22 surfaces
    is its own decision. Browse Games then wanted the identical rule, so it moved into elements.css
    instead of becoming a second copy.

    Asserted from BOTH ends. Checking only that the hero no longer overrides `.scard` would pass just as
    happily if the squeeze had been deleted outright -- an assertion about an absence needs the presence
    it implies, or it is a test that nothing happened.
    """
    _block, rules = _hero_phone_block()
    for sels, _body in rules:
        for sel in sels:
            assert 'scard' not in sel, (
                f'{sel!r} is a second copy of the stat-card squeeze; it belongs in elements.css'
            )

    elements = (ROOT / 'static' / 'css' / 'components' / 'elements.css').read_text(encoding='utf-8')
    phone = elements[elements.index('@media (max-width: 767px)'):]
    phone = phone[:phone.index('\n}\n') + 3]
    assert '.scard { padding' in phone, 'the primitive lost the phone padding'
    assert '.scard__label { margin-bottom: 5px' in phone, 'the label/figure gap is back to 12px'
    assert '.scard__sub { display: none' in phone, 'the sub-line is back on phones'
    # It must be a PHONE rule. Hiding the sub-line unconditionally would not shorten a header, it would
    # delete a line of real content from sixteen surfaces at every width.
    base = elements[:elements.index('@media (max-width: 767px)')]
    sub_rule = base[base.index('.scard__sub'):]
    assert 'display: none' not in sub_rule[:sub_rule.index('}')], (
        'the sub-line is hidden outside the phone block, so it is gone at every width'
    )


def test_the_notched_ring_geometry_agrees_with_itself():
    """The ring draws an arc with a notch at six o'clock so the level plate can sit ON it without hiding
    it. FOUR numbers have to agree for that, and none of them is checkable by eye:

      * the track's `stroke-dasharray: A G`   -- A units drawn, G units of gap
      * the fill's  `stroke-dasharray: A 100` -- the same arc length
      * the fill's  `* A/100` scale on --lvl  -- so 100% fills the arc EXACTLY, not past it
      * the svg's   `rotate(R)`               -- so the gap lands centred on six o'clock

    Get the scale wrong and 100% progress overruns into the gap, drawing under the plate -- which is the
    occlusion this shape exists to remove, reintroduced only for the hunters closest to levelling.
    Get the rotation wrong and the notch drifts off the bottom, leaving the plate over live arc.

    Recomputed from the dasharray rather than compared against literals, so retuning the gap is a
    one-number change that this test either confirms or rejects.
    """
    css = (ROOT / 'static' / 'css' / 'components' / 'profile-hero.css').read_text(encoding='utf-8')

    track = re.search(r'\.pp-phero__ring-track\s*\{[^}]*stroke-dasharray:\s*(\d+)\s+(\d+)', css)
    assert track, 'the ring track no longer declares a dash pattern, so it is a full circle again'
    arc, gap = int(track.group(1)), int(track.group(2))
    assert arc + gap == 100, f'the arc ({arc}) and gap ({gap}) must span the whole pathLength'

    fill = re.search(r'\.pp-phero__ring-fill\s*\{(.*?)\}', css, re.S).group(1)
    assert f'stroke-dasharray: {arc} 100' in fill, f'the fill draws a different arc than the track ({arc})'

    scale = re.search(r'var\(--lvl,\s*0\)\s*\*\s*([\d.]+)', fill)
    assert scale, 'the fill no longer scales --lvl to the arc, so 100% will not land on the arc end'
    assert abs(float(scale.group(1)) - arc / 100) < 1e-9, (
        f'--lvl is scaled by {scale.group(1)} but the arc is {arc} units: at 100% the fill '
        f'{"overruns into the gap and draws under the plate" if float(scale.group(1)) > arc / 100 else "stops short of the arc end"}'
    )

    offset = re.search(r'stroke-dashoffset:\s*calc\(\((\d+)\s*-', fill)
    assert offset and int(offset.group(1)) == arc, 'the fill offset does not start from the arc length'

    frame = re.search(r'@keyframes ppLevelRing\s*\{\s*from\s*\{\s*stroke-dashoffset:\s*(\d+)px', css)
    assert frame and int(frame.group(1)) == arc, (
        'the draw-in keyframe starts from a different length than the arc, so the ring jumps on load'
    )

    # Rotation: a unit is 3.6deg; the arc must start where the gap ends, with the gap centred on unit 50.
    rot = re.search(r'\.pp-phero__ring\s*\{[^}]*rotate\((-?[\d.]+)deg\)', css)
    assert rot, 'the ring has no rotation, so the arc starts at three o\'clock'
    expected = -90 + (50 + gap / 2) * 3.6
    assert abs(float(rot.group(1)) - expected) < 0.05, (
        f'rotate({rot.group(1)}deg) puts the notch off six o\'clock; a {gap}-unit gap needs '
        f'rotate({expected:.1f}deg)'
    )


def test_the_level_plate_is_inside_the_ring_box(client):
    """It is absolutely positioned against `.pp-phero__ringwrap`. As a SIBLING it would resolve against
    `.pp-phero__dial` instead -- which also contains the caption and the pill row -- and `bottom` would
    measure from the bottom of all of that, parking the plate somewhere below the ring entirely."""
    profile = ProfileFactory(is_linked=True, trophy_level=42, progress=68)
    html = client.get(f'/hunters/{profile.psn_username}/', **CF).content.decode()

    wrap = html[html.index('pp-phero__ringwrap'):]
    wrap = wrap[:wrap.index('pp-phero__tonext')]
    assert 'pp-phero__lvl' in wrap, 'the level plate is no longer inside the ring box'

    css = (ROOT / 'static' / 'css' / 'components' / 'profile-hero.css').read_text(encoding='utf-8')
    rule = re.search(r'\.pp-phero__lvl\s*\{(.*?)\}', css, re.S).group(1)
    assert 'position: absolute' in rule, 'the plate is back in flow, so it no longer sits on the ring'


def _lib_toolbar_rules():
    """The `--lib` toolbar's phone block, parsed into (selectors, body) pairs."""
    css = (ROOT / 'static' / 'css' / 'components' / 'game-browse.css').read_text(encoding='utf-8')
    block = css[css.index('`--lib` toolbar'):]
    block = block[:block.index('\n}\n', block.index('@media (max-width: 767px)')) + 3]
    inner = block[block.index('{', block.index('@media')) + 1: block.rindex('}')]
    inner = re.sub(r'/\*.*?\*/', '', inner, flags=re.S)
    return [([s.strip() for s in sel.split(',')], body)
            for sel, body in re.findall(r'([^{}]+)\{([^{}]*)\}', inner)]


def test_the_games_toolbar_phone_block_is_scoped_to_this_bar():
    """`.pp-gbrowse__toolbar` and its parts are shared with Browse Games, companies, recently-added and
    tag detail. Their control sets are different -- theirs live in a drawer -- so their bars are a
    separate measurement and a separate decision. Every rule here must carry `--lib` or it reshapes four
    other surfaces as a side effect of fitting this one.
    """
    rules = _lib_toolbar_rules()
    assert len(rules) >= 6, f'only {len(rules)} rules parsed -- the block boundary is wrong'
    for sels, _body in rules:
        for sel in sels:
            assert 'pp-gbrowse__toolbar--lib' in sel, (
                f'{sel!r} is unscoped, so it restyles every browse toolbar on the site'
            )


def test_the_hidden_platform_label_stays_in_the_accessibility_tree(client):
    """It is dropped for PIXELS, not from the page. `.pp-gbrowse__flabel` is a plain div rather than a
    <legend>, so it groups nothing programmatically -- but it is still the word a screen reader meets
    before six checkboxes whose labels ("PS5", "PSVR2") only make sense once you know they are platforms.
    `display: none` would take that away to save nothing.
    """
    rules = _lib_toolbar_rules()
    label = next(b for sels, b in rules if any('flabel' in s for s in sels))

    assert 'display: none' not in label, 'the platform heading was removed from the accessibility tree'
    assert 'clip-path' in label and 'position: absolute' in label, (
        'the heading is not visually hidden in the standard way, so it may still take layout space'
    )

    profile = ProfileFactory(is_linked=True)
    html = client.get(f'/hunters/{profile.psn_username}/?tab=games', **CF).content.decode()
    assert 'Platforms' in html, 'the heading is gone from the markup entirely'


def test_the_games_toolbar_search_breaks_its_own_line_on_a_phone():
    """The non-obvious half of the two-row bar. Flex breaks lines on the flex BASE size, and the search's
    inherited `flex: 1 1 200px` is small enough that search + both selects fit one line -- at which point
    the selects, sized from a 0 basis, settle at ~40px each, which is not a control.

    A 100% basis on the search is what forces the break, and only then do the selects share the row.
    Both halves are load-bearing; either one alone is worse than neither.
    """
    rules = _lib_toolbar_rules()
    search = next(b for sels, b in rules if any('__search' in s for s in sels))
    sort = next(b for sels, b in rules if any('__sort' in s for s in sels))

    assert 'flex: 1 1 100%' in search, 'the search no longer takes its own line, so the selects collapse'
    assert 'flex: 1 1 0' in sort and 'min-width: 0' in sort, 'the two selects no longer share the row'


def test_the_platform_chips_still_wrap_if_they_have_to():
    """They are tuned to hold one row at 360px, but the widths come from ESTIMATED font metrics. The
    honest failure mode for an estimate a few pixels out is a second row, not a bar that overflows its
    card -- so nothing here may set `flex-wrap: nowrap`."""
    rules = _lib_toolbar_rules()
    chips = next(b for sels, b in rules if any('__fchips' in s for s in sels))
    assert 'nowrap' not in chips, 'the chip row cannot wrap, so a mis-estimate overflows the toolbar'


def _facts_phone_rules():
    """The browse card's facts-row phone block, as (selectors, body) pairs."""
    import re
    from pathlib import Path
    css = (Path(__file__).resolve().parents[2] / 'static' / 'css' / 'components'
           / 'game-card.css').read_text(encoding='utf-8')
    block = css[css.index('Phone + small tablet: the facts row'):]
    block = block[:block.index('\n}\n', block.index('@media (max-width: 767px)')) + 3]
    inner = block[block.index('{', block.index('@media')) + 1: block.rindex('}')]
    inner = re.sub(r'/\*.*?\*/', '', inner, flags=re.S)
    return css, [([s.strip() for s in sel.split(',')], body)
                 for sel, body in re.findall(r'([^{}]+)\{([^{}]*)\}', inner)]


def test_the_browse_card_facts_row_does_not_clip_the_platinum_flag():
    """`grid-template-columns: 1fr auto 1fr` cannot shrink to a narrow card. `1fr` is
    `minmax(auto, 1fr)`, so every track has a min-content floor, and both inner groups are `inline-flex`
    (nowrap), so their min-content IS their full width. The row's floor is ~182px against 148px of body at
    2 columns and 178px at 3 -- and `.pp-gcard` sets `overflow: hidden`, so the excess does not wrap or
    scroll, it CLIPS the last item. That item is the platinum indicator, which is the one fact a trophy
    hunter scans a browse wall for.

    Invisible above 768px, where the body reaches 221px -- i.e. invisible on every desktop.
    """
    _css, rules = _facts_phone_rules()
    assert len(rules) >= 4, f'only {len(rules)} rules parsed -- the block boundary is wrong'

    # Matched on the track declaration, not on a selector spelling: the container selector carries a
    # `:has()` gate (see the test below) and pinning its exact text would fail on any re-scope.
    areas = next(b for _sels, b in rules if 'grid-template-areas' in b)
    assert 'grid-template-areas' in areas, 'the facts row is back on a single line'
    assert '1fr' not in areas, (
        'the facts row is back on fr tracks, which have a min-content floor the card cannot meet'
    )

    placed = {}
    for sels, body in rules:
        if 'grid-area:' in body:
            for s in sels:
                placed[s] = body.split('grid-area:')[1].split(';')[0].strip()
    assert set(placed.values()) == {'rating', 'mid', 'plat'}, f'unplaced facts: {placed}'


def test_the_facts_row_scopes_around_the_duplicated_plat_class():
    """`.pp-gcard__plat` means TWO things on this card: the platinum indicator inside `.pp-gcard__facts`,
    and a platform chip inside `.pp-gcard__plats` in the footer. They are told apart only by position.

    So every rule here goes through `.pp-gcard__facts >`. Unscoped, `grid-area: plat` would land on each
    PS5/PS4 chip in the footer -- a grid area that does not exist in that container.
    """
    _css, rules = _facts_phone_rules()
    for sels, _body in rules:
        for sel in sels:
            assert sel.startswith('.pp-gcard__facts'), (
                f'{sel!r} is not scoped to the facts row; `.pp-gcard__plat` is also the footer platform chip'
            )


def test_the_facts_phone_grid_does_not_leak_onto_the_dlc_card():
    """`.pp-gcard__facts` has a SECOND consumer with a different shape. Recently Added's DLC card puts
    only `__tro` + `__count` in it -- no `__fact`, no `__mid`, no `__plat` -- so none of the grid-area
    rules reach those two, and an ungated container rule simply auto-placed them into this two-column
    grid: packed left instead of spanning the row, plus a row-gap for a declared-but-empty second row.

    The gate is `:has(> .pp-gcard__mid)`, which names the three-part SHAPE rather than the page, so a
    fourth surface reusing the full facts row is covered without being listed here.
    """
    from pathlib import Path
    _css, rules = _facts_phone_rules()

    container = next(sels for sels, b in rules if 'grid-template-areas' in b)
    assert all(':has(' in s for s in container), (
        f'{container} applies to every .pp-gcard__facts, including the two-child DLC card'
    )

    # The gate only means anything while the DLC card really lacks `__mid`. If that card ever grows one,
    # this fails here rather than silently re-opening the leak.
    dlc = (Path(__file__).resolve().parents[2] / 'templates' / 'trophies' / 'partials'
           / 'recently_added' / 'dlc_card.html').read_text(encoding='utf-8')
    assert 'pp-gcard__facts' in dlc, 'the DLC card no longer shares the facts row -- retire this gate'
    assert 'pp-gcard__mid' not in dlc, (
        'the DLC card now has a `__mid`, so `:has(> .pp-gcard__mid)` no longer excludes it'
    )


def test_the_browse_card_still_shows_every_fact_on_a_phone(client):
    """The row was split, not trimmed. Two lines cost ~18px of card height and lose nothing -- hiding the
    DLC chip would have fixed the overflow for free, and is the trade to make later if that height ever
    matters more than the chip. Pinned so the fix is not quietly turned into a deletion."""
    from pathlib import Path
    card = (Path(__file__).resolve().parents[2]
            / 'templates/trophies/partials/game_list/game_cards.html').read_text(encoding='utf-8')

    for part in ('pp-gcard__fact', 'pp-gcard__tro', 'pp-gcard__dlc', 'pp-gcard__plat'):
        assert part in card, f'{part} was removed from the browse card rather than re-laid out'

    _css, rules = _facts_phone_rules()
    for _sels, body in rules:
        assert 'display: none' not in body, 'a fact is hidden on phones instead of wrapped'
