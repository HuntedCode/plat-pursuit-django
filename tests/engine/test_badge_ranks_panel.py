"""The series board moved onto badge detail (leaderboards rebuild, step 5).

`/leaderboards/badges/<slug>/` was a whole PAGE for what is a section of the page about the badge. Boards
live on the thing they rank, which is also what keeps a single canonical location per board -- two pages
showing one board is the drift this rebuild exists to remove.

The panel is fetched on first scroll-in rather than server-rendered, copying game detail's Ranks panel:
the cost scales with a series' popularity and most visitors come for the badge, not the board.
"""
import datetime as dt
from pathlib import Path

import pytest
from django.urls import reverse
from django.utils import timezone

from trophies.models import SeriesBadgeStanding
from tests.factories import (
    ProfileFactory, BadgeSeriesFactory, StageFactory, ConceptFactory, GameFactory,
    PlatformGroupFactory, GroupBadgeFactory,
)


def _renderable(slug, name):
    """A series badge detail will actually render: a live GroupBadge over a gating stage. A bare
    BadgeSeries 404s, which is correct behaviour and a misleading test failure."""
    series = BadgeSeriesFactory(series_slug=slug, name=name)
    st = StageFactory(series_slug=slug, stage_number=1)
    concept = ConceptFactory()
    st.concepts.add(concept)
    GameFactory(concept=concept, title_platform=['PS5'])
    pg = PlatformGroupFactory(key=f'{slug}-ultra', name='Ultra HD', platforms=['PS4', 'PS5'])
    GroupBadgeFactory(series=series, platform_group=pg, is_live=True)
    return series

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[2]


def _standing(slug, name, *, bp, on, country=''):
    p = ProfileFactory(display_psn_username=name, country_code=country)
    SeriesBadgeStanding.objects.create(
        profile=p, series_slug=slug, xp=100, progress_bp=bp,
        stages_cleared=bp // 2500, stages_total=4, advanced_at=on, country_code=country,
        is_linked=True,
    )
    return p


def test_the_retired_page_redirects_to_the_badge_it_was_about(client):
    """Permanently, and KEEPING the slug -- so an existing link lands on the badge whose board it wanted
    rather than on a generic index. A 404 would have been the lazy option and throws away the intent."""
    _renderable('gow', 'God of War')
    resp = client.get('/leaderboards/badges/gow/')
    assert resp.status_code == 301
    assert resp['Location'].rstrip('/').endswith('/badges/gow')


@pytest.mark.parametrize('path', [
    '/community/leaderboards/badges/gow/',
    '/leaderboard/badges/gow/',
])
def test_the_legacy_redirects_still_resolve(client, path):
    """These two named the retired URL. Retiring a url NAME makes its RedirectView raise
    NoReverseMatch -- a 500, not a 404 -- so they had to be repointed in the same change or every old
    inbound link would have started erroring."""
    _renderable('gow', 'God of War')
    resp = client.get(path)
    assert resp.status_code == 301, f'{path} did not redirect (a 500 here means an unrepointed name)'


def test_the_panel_endpoint_serves_the_merged_board(client):
    """Earners above chasers, ties broken by who got there first -- the merged board, not two lists."""
    _renderable('mix', 'Mix')
    # Built in REVERSE date order so profile ids run opposite to the dates: only the tiebreak can
    # produce the expected ordering.
    later = _standing('mix', 'SecondThere', bp=5000, on=dt.date(2024, 6, 1))
    earlier = _standing('mix', 'FirstThere', bp=5000, on=dt.date(2021, 1, 1))
    done = _standing('mix', 'Finisher', bp=10000, on=dt.date(2025, 1, 1))

    body = client.get(reverse('badge_ranks_panel', args=['mix'])).content.decode()
    order = [body.index(n) for n in ('Finisher', 'FirstThere', 'SecondThere')]
    assert order == sorted(order), 'the panel is not (finished first, then earliest-there)'
    assert 'lb-row' in body, 'the panel is not reusing the shared leaderboard row'


def test_the_panel_is_public(client):
    """The board is identical for every viewer, which is what keeps it cacheable. Gating it behind login
    would forfeit that AND hide the section from the visitors most likely to be persuaded by it."""
    _renderable('pub', 'Public')
    _standing('pub', 'Anyone', bp=2500, on=dt.date(2024, 1, 1))
    resp = client.get(reverse('badge_ranks_panel', args=['pub']))
    assert resp.status_code == 200 and 'Anyone' in resp.content.decode()


def test_an_unknown_series_is_a_404_not_an_empty_board(client):
    """An empty board for a series that does not exist reads as "nobody is chasing it" -- a plausible,
    wrong answer. The endpoint is reachable by URL, so it has to tell the two apart."""
    assert client.get(reverse('badge_ranks_panel', args=['no-such-series'])).status_code == 404


def test_badge_detail_carries_the_ranks_section_and_fetches_it_lazily(client):
    """Server-rendering it would put the board's cost on every badge-page view, including the majority
    who never open it.

    As of 2026-08 the trigger is the Ranks TAB being activated, not the section scrolling into view --
    on the main scroll every reader who reached the bottom paid for a board they may not have wanted.
    The assertions below hold for either, which is the point: they pin that the rows are not in the
    initial document, not how the fetch is triggered."""
    _renderable('lazy', 'Lazy')
    body = client.get(reverse('badge_detail', args=['lazy'])).content.decode()

    assert 'data-ranks-src' in body, 'badge detail has no Ranks section'
    assert 'id="ranks"' in body, 'the section has no anchor for the links that point at it'
    assert reverse('badge_ranks_panel', args=['lazy']) in body
    # The rows themselves must NOT be in the initial document -- that is the whole point of lazy loading.
    # Matched on the RENDERED opening tag, not the bare class name: the lazy-fetch script in this same
    # page passes '.lb-row' as a cardSelector, so a substring check finds the JS and fails on correct
    # code. Had the string been slightly different it would have PASSED on a server-rendered board.
    assert '<li class="lb-row' not in body, 'the board was server-rendered into badge detail'


def test_the_ranks_section_renders_once_for_a_multi_edition_series(client):
    """Series-level, proven BEHAVIOURALLY: a series with two editions must still render exactly one Ranks
    section. Nested inside a `.bd2-panel` it would render once per edition, with an edition switcher
    appearing to change a board that is the same either way (the board is per series -- earned any edition
    counts, matching progress_bp already being the max across them).

    This replaces a source-position check whose first assertion was literally `x == x` -- it read like a
    sanity guard and tested nothing. Counting rendered occurrences is both stronger and simpler.
    """
    from tests.factories import (
        BadgeSeriesFactory, StageFactory, ConceptFactory, GameFactory,
        PlatformGroupFactory, GroupBadgeFactory,
    )

    series = BadgeSeriesFactory(series_slug='two', name='Two Editions')
    st = StageFactory(series_slug='two', stage_number=1)
    concept = ConceptFactory()
    st.concepts.add(concept)
    GameFactory(concept=concept, title_platform=['PS4', 'PS5'])
    for key, name, plats in (('two-ultra', 'Ultra HD', ['PS5']), ('two-legacy', 'Legacy HD', ['PS4'])):
        pg = PlatformGroupFactory(key=key, name=name, platforms=plats)
        GroupBadgeFactory(series=series, platform_group=pg, is_live=True)

    body = client.get(reverse('badge_detail', args=['two'])).content.decode()

    # Counted on the rendered OPENING TAG, not the attribute name: the lazy-fetch script on this same
    # page queries `[data-ranks-src]`, so counting the bare attribute finds the JS too and reports 2 on
    # correct markup. That is the third time this session an assertion has matched a page's own script --
    # the rule is to match what the browser renders, never a token a selector can also contain.
    sections = body.count('<section class="bd2-ranks"')
    assert sections == 1, (
        f'the Ranks section rendered {sections} times -- it is inside a per-edition panel rather than at '
        f'series level'
    )
    assert body.count('id="ranks"') == 1
    # Sanity that the fixture really is multi-edition, so the assertion above means something.
    assert 'Ultra HD' in body and 'Legacy HD' in body


def test_the_panel_hides_a_dormant_series_from_the_public(client):
    """The fragment must apply the SAME gate as the page it belongs to.

    `BadgeDetailView.get_object` 404s a series with no live edition for non-staff. This view did a bare
    slug lookup, so `/badges/<unreleased-slug>/ranks/` answered for a series whose own page 404s --
    confirming the series exists and serving its board to anyone who guessed the slug. A curator's
    unreleased work is exactly what that gate is protecting.
    """
    from tests.factories import BadgeSeriesFactory, GroupBadgeFactory, PlatformGroupFactory

    series = BadgeSeriesFactory(series_slug='unreleased', name='Unreleased')
    GroupBadgeFactory(series=series, platform_group=PlatformGroupFactory(key='dg'), is_live=False)

    # BOTH response modes. The gate runs before the `?range=` branch, and that ordering is the entire
    # protection: hoisting the window branch above it -- a plausible "make the hot path cheap" refactor
    # -- would serve an unreleased board to anyone who guessed the slug, with every other test green.
    # Only the full-panel path was pinned when the second mode was added.
    for params in ({}, {'range': 1}, {'range': 51, 'count': 50}):
        assert client.get(reverse('badge_ranks_panel', args=[series.series_slug]),
                          params).status_code == 404, f'a dormant series served its board for {params!r}'


def test_staff_can_still_preview_a_dormant_series_panel(client):
    """The gate is a staff PREVIEW gate, not a wall -- curators check the board before releasing."""
    from tests.factories import (
        BadgeSeriesFactory, GroupBadgeFactory, PlatformGroupFactory, ProfileFactory,
    )

    series = BadgeSeriesFactory(series_slug='unreleased2', name='Unreleased Two')
    GroupBadgeFactory(series=series, platform_group=PlatformGroupFactory(key='dg2'), is_live=False)

    staff = ProfileFactory(is_linked=True)
    staff.user.is_staff = True
    staff.user.save(update_fields=['is_staff'])
    client.force_login(staff.user)

    assert client.get(reverse('badge_ranks_panel', args=[series.series_slug])).status_code == 200


def test_a_signed_in_hunter_is_told_when_they_are_NOT_on_the_board(client):
    """The line used to be gated on `my_rank`, so a hunter with no standing in this series got silence
    where the answer belonged -- on a page whose whole job is inviting them to chase the badge."""
    series = _renderable('chase', 'Chase')
    _standing('chase', 'Ahead', bp=5000, on=dt.date(2025, 1, 1))
    viewer = ProfileFactory(display_psn_username='Newcomer')
    client.force_login(viewer.user)

    body = client.get(reverse('badge_ranks_panel', args=['chase'])).content.decode()
    assert 'Not on this board yet' in body

    SeriesBadgeStanding.objects.create(profile=viewer, series_slug='chase', xp=100, progress_bp=7500,
                                       stages_cleared=3, stages_total=4, advanced_at=dt.date(2025, 2, 1), is_linked=True)
    body = client.get(reverse('badge_ranks_panel', args=['chase'])).content.decode()
    # Once ranked, the answer moves from the board card's standing slot to the JUMP CHIP, which says
    # "You're #N" and is also the control that takes you there. Saying it in both places on one card was
    # the duplication the shared board card removed, so this asserts the chip rather than the old line.
    assert 'Not on this board yet' not in body
    assert 'data-lb-jump' in body, 'a ranked hunter is told their rank nowhere at all'


def test_a_signed_out_visitor_is_told_nothing_about_a_standing_they_cannot_have(client):
    series = _renderable('chase', 'Chase')
    _standing('chase', 'Ahead', bp=5000, on=dt.date(2025, 1, 1))
    body = client.get(reverse('badge_ranks_panel', args=['chase'])).content.decode()
    assert 'Not on this board yet' not in body and 'You are' not in body


def test_the_board_can_be_read_past_the_first_window(client):
    """It stopped dead at 25 under a comment promising a link to a full board -- a page that does not
    exist and deliberately does not, since this panel REPLACED it. So the panel told a hunter their rank
    and in the same breath made the row it points at unreachable.

    The "show more" button that first fixed it is gone too: 25 rows a click cannot reach row 3,000 of a
    popular series in any reasonable number of clicks. The board is virtualized, and this endpoint serves
    each window of it.
    """
    _renderable('deep', 'Deep')
    for i in range(60):
        _standing('deep', f'Hunter{i:02d}', bp=9900 - i, on=dt.date(2025, 1, 1))

    first = client.get(reverse('badge_ranks_panel', args=['deep']))
    body = first.content.decode()
    assert len(first.context['rows']) == 50, 'the first window is not a full page'
    assert first.context['total'] == 60, 'the spacer would be sized to the window, not the board'
    assert 'data-lb-total="60"' in body
    assert 'Hunter00' in body and 'Hunter59' not in body

    window = client.get(reverse('badge_ranks_panel', args=['deep']), {'range': 51, 'count': 50})
    tail = window.content.decode()
    # ROWS ONLY -- the virtualizer splices these into its own spacer, so a wrapper or any chrome here
    # would be parsed and discarded.
    assert tail.count('<li class="lb-row') == 10
    assert 'lb-boardcard' not in tail and '<ol' not in tail and 'lb-jumpbar' not in tail
    assert 'Hunter50' in tail and 'Hunter00' not in tail
    # `page()` numbers by SLOT, so the continuation must not restart at #1.
    assert window.context['entries'][0]['rank'] == 51


def test_a_junk_range_falls_back_to_the_first_window(client):
    """A junk `range` is still a window request -- the param is PRESENT, so the caller wanted rows, and
    handing back the full panel would splice a meta line and a jump bar into the middle of the wall."""
    _renderable('deep', 'Deep')
    _standing('deep', 'Only', bp=5000, on=dt.date(2025, 1, 1))
    for raw in ('abc', '-5', ''):
        resp = client.get(reverse('badge_ranks_panel', args=['deep']), {'range': raw})
        assert resp.status_code == 200, f'range={raw!r} was not handled'
        assert resp.context['entries'][0]['rank'] == 1, f'range={raw!r} did not fall back'
        assert 'lb-boardcard' not in resp.content.decode(), f'range={raw!r} returned panel chrome'


def test_no_range_at_all_serves_the_full_panel(client):
    """The two responses are told apart by the PRESENCE of `range`, not its value, so the tab's own
    first fetch (which sends nothing) must still get chrome."""
    _renderable('deep', 'Deep')
    _standing('deep', 'Only', bp=5000, on=dt.date(2025, 1, 1))
    body = client.get(reverse('badge_ranks_panel', args=['deep'])).content.decode()
    assert 'lb-boardcard' in body and 'lb-jumpbar' in body


def test_a_window_past_the_end_of_the_board_is_empty_not_an_error(client):
    """The `X-Has-Next` header this replaces existed so the client could stop clicking "show more". A
    virtualized wall never asks past its own `total`, so there is nothing to signal -- but a crafted URL
    can, and it must come back empty rather than 500."""
    _renderable('exact', 'Exact')
    for i in range(25):
        _standing('exact', f'Hunter{i:02d}', bp=9900 - i, on=dt.date(2025, 1, 1))

    past = client.get(reverse('badge_ranks_panel', args=['exact']), {'range': 500, 'count': 50})
    assert past.status_code == 200
    assert past.content.decode().count('<li class="lb-row') == 0


def test_a_window_emits_bare_rows_with_the_rank_the_virtualizer_places_by(client):
    """`virtualBoard` reads `data-lb-rank` off each row to place it in the spacer, so a row that renders
    without it is INVISIBLE to a virtualized wall -- spliced in, then positioned nowhere.

    This is the successor to a test that pinned `.lb-row` for `staggerReveal`'s observer. That engine is
    gone from the boards (it hides rows a virtual wall never un-hides); the selector still matters, for a
    different reason.
    """
    _renderable('deep2', 'Deep2')
    for i in range(60):
        _standing('deep2', f'H{i:02d}', bp=9900 - i, on=dt.date(2025, 1, 1))

    tail = client.get(reverse('badge_ranks_panel', args=['deep2']), {'range': 51}).content.decode()
    assert tail.count('<li class="lb-row') == 10   # quote-free prefix; `lb-row__rank` etc. must not match
    assert 'data-lb-rank="51"' in tail and 'data-lb-rank="60"' in tail
    assert 'pp-reveal' not in tail, 'the reveal engine is back on a wall that never un-hides its rows'


def test_an_unverified_account_is_not_promised_a_board_it_cannot_enter(client):
    """The THIRD viewer state, and the one neither the signed-out nor the ranked test reaches.

    Every board population is `is_linked`-gated (`badge_leaderboards._linked`), so an unverified account
    told "Not on this board yet" is being offered a board it cannot join until it verifies. Game detail
    already resolved its viewer this way; the other two panels said "signed in" and meant it.
    """
    _renderable('chase', 'Chase')
    _standing('chase', 'Ahead', bp=5000, on=dt.date(2025, 1, 1))
    unlinked = ProfileFactory(is_linked=False, display_psn_username='Unverified')
    client.force_login(unlinked.user)

    body = client.get(reverse('badge_ranks_panel', args=['chase'])).content.decode()
    assert 'Not on this board yet' not in body and 'You are' not in body


def test_a_crafted_window_cannot_ask_for_the_whole_board(client):
    """Same reasoning as every other board: this is a PUBLIC fragment, `range` becomes a SQL OFFSET that
    Postgres honours by walking every skipped row, and an unbounded `count` hydrates the whole board in
    one read.

    Built on a board LARGER than `MAX_COUNT` and a `range` INSIDE it, so the clamps are actually
    observed. The first version asked for `range=10**12` on a one-row board: the window came back empty
    whether or not any clamping happened, so `0 <= MAX_COUNT` held with the parser deleted entirely.
    """
    from trophies.views.board_helpers import MAX_COUNT, MAX_START

    _renderable('wide', 'Wide')
    for i in range(MAX_COUNT + 20):
        _standing('wide', f'H{i:03d}', bp=9900 - i, on=dt.date(2025, 1, 1))

    # COUNT: asked for the lot, from the top of a board that has more than the ceiling.
    greedy = client.get(reverse('badge_ranks_panel', args=['wide']), {'range': 1, 'count': 10 ** 6})
    assert greedy.status_code == 200
    assert greedy.content.decode().count('<li class="lb-row') == MAX_COUNT, (
        'the count ceiling was not applied -- a crafted URL hydrated more than 200 profiles in one read'
    )

    # RANGE: past the end of the board, so the OFFSET actually run is the clamp rather than the URL.
    far = client.get(reverse('badge_ranks_panel', args=['wide']), {'range': 10 ** 12})
    assert far.status_code == 200
    assert far.context['entries'] == []
    assert MAX_START < 10 ** 12, 'the start clamp no longer bounds what this asks for'


def test_a_row_shows_its_denominator_and_its_tiebreak_date(client):
    """Both were fetched on every row and thrown away.

    Without the denominator, a hunter who finished a 5-stage series rendered "5 stages" and one sitting on
    5 of 8 rendered "5 stages" -- the top of the board and the middle of it, identical. And `advanced_at`
    is what ORDERS rows sharing a rung (whoever got there first ranks higher), so leaving it off screen
    made the ordering look arbitrary to the person reading it.
    """
    _renderable('shown', 'Shown')
    _standing('shown', 'Finisher', bp=10000, on=dt.date(2024, 3, 1))
    _standing('shown', 'Chaser', bp=5000, on=dt.date(2025, 7, 1))

    resp = client.get(reverse('badge_ranks_panel', args=['shown']))
    rows = resp.context['rows']
    body = resp.content.decode()

    # `_standing` builds stages_cleared from bp // 2500 against a stages_total of 4.
    assert rows[0]['primary'] == 4 and rows[0]['primary_of'] == 4, 'the denominator never reached the row'
    assert rows[1]['primary'] == 2 and rows[1]['primary_of'] == 4
    assert rows[0]['when'] is not None, 'the tiebreak date never reached the row'

    assert '/ 4' in body, 'the denominator is not rendered'
    assert 'Mar 2024' in body and 'Jul 2025' in body, 'the tiebreak date is not rendered'


def test_the_date_orders_rows_that_share_a_rung(client):
    """The reason the date is worth showing: it is the sort key, not decoration. Two hunters on the same
    number of stages are separated by who got there first."""
    _renderable('rung', 'Rung')
    later = _standing('rung', 'Later', bp=5000, on=dt.date(2025, 1, 1))
    earlier = _standing('rung', 'Earlier', bp=5000, on=dt.date(2021, 1, 1))

    rows = client.get(reverse('badge_ranks_panel', args=['rung'])).context['rows']

    assert [r['profile_id'] for r in rows] == [earlier.id, later.id]
    assert rows[0]['primary'] == rows[1]['primary'], 'the fixture no longer tests a shared rung'


def test_the_board_lives_behind_its_own_tab(client):
    """Badge detail was the only detail page rendering its board on the main scroll -- game detail and
    job detail both tab it. Two panels, real tablist semantics, and Overview open by default.

    The badge HERO stays outside both panels: it is the page header and has to remain visible whichever
    tab is open, or a reader on Ranks has lost track of which badge they are looking at.
    """
    _renderable('tabbed', 'Tabbed')
    body = client.get(reverse('badge_detail', args=['tabbed'])).content.decode()

    assert 'id="bd-switch"' in body and 'role="tablist"' in body
    assert 'id="bd-view-overview"' in body and 'id="bd-view-ranks"' in body
    # Overview open, Ranks closed, and the closed one really is closed rather than merely unstyled.
    assert 'data-view="ranks" hidden' in body, 'the Ranks panel is not hidden on arrival'
    assert 'id="bd-tab-overview" aria-controls="bd-view-overview" role="tab"' in body

    # The board markup is INSIDE the Ranks panel, not loose on the page.
    ranks_panel = body.index('id="bd-view-ranks"')
    assert body.index('data-ranks-src') > ranks_panel, 'the board is not inside the Ranks panel'

    # ...and the hero card is above both panels, so it survives a tab switch.
    assert body.index('bd2-med') < body.index('id="bd-switch"'), (
        'the hero fell inside the content tabs -- it is the page header and must survive a tab switch'
    )


def test_the_ranks_tab_carries_no_edition_control(client):
    """The clean half of the split: the board is per SERIES (earning any edition counts), so an edition
    switcher over it would be pretending to change it. Overview owns the editions; Ranks does not.

    Uses a TWO-edition series, because a single-edition one hides the switcher entirely
    (`detail.has_multiple_groups`) and would pass without ever rendering the thing being excluded.
    Sliced at the first `<script`, since the switcher's classes legitimately appear in the page's JS.
    """
    series = _renderable('noedition', 'No Edition')
    second = PlatformGroupFactory(key='noedition-legacy', name='Legacy HD', platforms=['PS3'])
    GroupBadgeFactory(series=series, platform_group=second, is_live=True)

    body = client.get(reverse('badge_detail', args=['noedition'])).content.decode()
    # The opening TAG, not the bare class: the page's own JS selects `.bd2-groupswitch`, so a class-name
    # search finds the script and passes on correct code.
    assert '<div class="bd2-groupswitch"' in body, 'the fixture did not render an edition switcher'

    # From the panel to the next <script> after it -- the page's trailing scripts mention these classes.
    start = body.index('id="bd-view-ranks"')
    ranks_panel = body[start:body.index('<script', start)]
    assert 'bd2-groupswitch' not in ranks_panel
    assert 'bd2-gbtn' not in ranks_panel


def test_the_in_page_links_to_the_board_are_wired_to_the_tab(client):
    """The board moved behind a tab, and a browser cannot scroll to a target inside a `hidden` panel --
    so every `href="#ranks"` on the page became a dead click the moment it moved. Silently, too: a
    fragment link that resolves to nothing raises nothing and logs nothing.

    Pins BOTH halves: that the links still exist (the hero's Earners figure and the community band's CTA
    are how most readers reach the board), and that the page carries a handler for them.
    """
    _renderable('wired', 'Wired')
    body = client.get(reverse('badge_detail', args=['wired'])).content.decode()

    assert body.count('href="#ranks"') >= 1, 'nothing links to the board any more'
    assert 'a[href="#ranks"]' in body, (
        'the #ranks links have no handler -- they cannot scroll into a hidden panel on their own'
    )
    assert "showView('ranks')" in body


def test_the_content_tabs_slide_like_every_other_switcher(client):
    """`slideViewIn` is the house standard for a segmented switcher -- nine surfaces use it. This one was
    built without it and swapped instantly, which reads as a jump beside the edition switcher directly
    above it, which does slide."""
    _renderable('slide', 'Slide')
    body = client.get(reverse('badge_detail', args=['slide'])).content.decode()

    assert body.count('PlatPursuit.slideViewIn') >= 2, (
        'the content tabs do not slide (the edition switcher above them does)'
    )
