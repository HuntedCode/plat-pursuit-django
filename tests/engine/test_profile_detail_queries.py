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


def test_the_platinum_highlights_do_not_haul_the_igdb_blob(client):
    """The header's four highlight cards each join `concept__igdb_match` for the cover chain, and three of
    them did it without deferring `raw_response` -- so every profile render, on every tab, dragged three
    copies of a ~30 KB blob nothing reads. Bounded (one row each) rather than per-card, which is why it
    survived the games-grid fix, but the standing rule is that the `select_related` and the `defer` travel
    together.
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
