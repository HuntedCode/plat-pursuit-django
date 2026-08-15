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

    assert 'pp-gcard' in body, 'the games tab no longer uses the shared game card'
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


def test_the_card_shows_the_per_tier_record(client):
    """Which trophies are actually LEFT, not just how many. Both dicts are denormalized JSON, so four
    tiers cost nothing; the names live on `title` so the tier is never conveyed by colour alone."""
    profile = _profile_with_games(1)

    body = client.get(f'/hunters/{profile.psn_username}/?tab=games', **CF).content.decode()

    assert 'pp-pgcard__tier' in body
    for tier in ('Bronze:', 'Silver:', 'Gold:'):
        assert tier in body, f'{tier} has no accessible name, leaving the tier colour-only'


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


def test_the_trophy_shape_sits_in_the_identity_block(client):
    """Folding the level into the ring made the dial ~121px tall against a ~66px name block, which left a
    void beside the ring that nothing filled. The tier split moved up into it, out of a bordered row at
    the foot of the card.

    Asserted by nesting, because placement IS the change: it has to be inside the name block, above the
    stat grid, not merely somewhere on the page."""
    profile = ProfileFactory(is_linked=True, total_plats=12, total_golds=140,
                             total_silvers=380, total_bronzes=1400)

    html = client.get(f'/hunters/{profile.psn_username}/', **CF).content.decode()

    name_block = html[html.index('min-w-0 flex-1'):html.index('scard__label')]
    assert 'pp-phero__tiers' in name_block, 'the trophy shape left the identity block'
    for tier in ('platinum', 'gold', 'silver', 'bronze'):
        assert f'data-tier="{tier}"' in name_block, f'{tier} is missing from the shape'


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
