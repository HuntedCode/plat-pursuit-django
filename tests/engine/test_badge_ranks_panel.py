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
    """A series standing whose POINTS move with its progress.

    `xp` was a flat 100 for everybody, which was harmless while the board ranked on `progress_bp` and is
    not now: on a points board a constant makes every hunter tie, so the date does all the ordering and
    "earners above chasers" stops being a thing the fixture can express. `bp // 100` keeps it simple and
    truthful in shape -- a finisher holds more points than somebody halfway.
    """
    p = ProfileFactory(display_psn_username=name, country_code=country)
    SeriesBadgeStanding.objects.create(
        profile=p, series_slug=slug, xp=bp // 100, progress_bp=bp,
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


def test_a_row_shows_its_points_and_its_tiebreak_date(client):
    """The row carries ONE figure and a date.

    It used to carry a stage tally with a denominator, which was the right fix for the board as it stood
    -- without it, a hunter who finished a 5-stage series and one sitting on 5 of 8 both rendered "5
    stages". The tally is gone with the ordering it described: points already count what was cleared and
    weigh what it was worth, and the old tally was the FURTHEST-ALONG EDITION's, which made it doubly
    wrong on a board that sums editions. Points have no denominator, so the row must not invent one.

    `advanced_at` still matters, and more than before: everyone who has cleared the same stages holds the
    same points, so ties are the common case and the date is what turns a rung of them into an order.
    """
    _renderable('shown', 'Shown')
    _standing('shown', 'Finisher', bp=10000, on=dt.date(2024, 3, 1))
    _standing('shown', 'Chaser', bp=5000, on=dt.date(2025, 7, 1))

    resp = client.get(reverse('badge_ranks_panel', args=['shown']))
    rows = resp.context['rows']
    body = resp.content.decode()

    # `_standing` derives xp as bp // 100.
    assert rows[0]['primary'] == 100 and rows[0]['primary_label'] == 'points'
    assert rows[1]['primary'] == 50
    assert rows[0].get('primary_of') is None, 'points were given a denominator they do not have'
    assert rows[0]['when'] is not None, 'the tiebreak date never reached the row'

    assert '/ ' not in body.split('lb-row__figs')[1][:400], 'a denominator is being rendered'
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


def test_the_ranks_tab_does_not_duplicate_the_MEDALLION_switcher(client):
    """Ranks has its own edition control now, and this is about the OTHER one.

    The original reasoning here was that the board is per SERIES, so any edition switcher over it would
    be pretending to change it. That was true of the board as it stood and stopped being true when
    picking an edition started switching the STORE (`SeriesBadgeStanding` -> `UserGroupBadge`, earners of
    that edition) -- the same move the Global Boards landing makes for Badge Points.

    What must still not appear is `bd2-groupswitch`: Overview's medallion switcher, which changes which
    ARTWORK you are looking at. Two controls with the same name doing different things on one page is
    worse than one, so Ranks filters with the shared `.lb-filters` select and leaves the medallion
    switcher to the tab that owns the medallion.

    Uses a TWO-edition series, because a single-edition one hides both controls entirely and would pass
    without ever rendering the thing being excluded. Sliced at the first `<script`, since the switcher's
    classes legitimately appear in the page's JS.
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


# ---------------------------------------------------------------------------------------------------
# The country filter (2026-08). The Global Boards landing has offered one since it was rebuilt; this
# board did not, so the same question ("who from my country is chasing this?") was answerable on one
# board and not on the badge's own.
# ---------------------------------------------------------------------------------------------------

def _in(slug, name, *, code, country, bp, on=dt.date(2025, 1, 1)):
    """A standing sliceable by country.

    `country_code` is set on the STANDING as well as the profile, because that is where the board reads
    it (`_slice` filters the store's own mirrored column, not `profile__country_code` -- the whole point
    of the denorm is that the board never joins Profile to filter). In production the mirror is
    maintained by `signals.profile_mirrored_standings`; a test that creates the row directly has to set
    it, exactly as the Global Boards fixtures do.
    """
    p = ProfileFactory(display_psn_username=name, country_code=code, country=country, is_linked=True)
    SeriesBadgeStanding.objects.create(profile=p, series_slug=slug, country_code=code, progress_bp=bp,
                                       advanced_at=on, is_linked=True)
    return p


def test_the_board_can_be_sliced_by_country(client):
    _renderable('slice', 'Slice')
    _in('slice', 'Brit', code='GB', country='United Kingdom', bp=9000)
    _in('slice', 'Yank', code='US', country='United States', bp=8000)
    _in('slice', 'Brit2', code='GB', country='United Kingdom', bp=7000)

    whole = client.get(reverse('badge_ranks_panel', args=['slice']))
    assert whole.context['total'] == 3

    sliced = client.get(reverse('badge_ranks_panel', args=['slice']), {'country': 'GB'})
    body = sliced.content.decode()
    assert sliced.context['total'] == 2, 'the tally counted the whole board under a slice'
    assert 'Brit' in body and 'Yank' not in body
    # Ranks are renumbered WITHIN the slice, or the second GB hunter would render as #3 on a two-row
    # board -- a rank that points at nothing the reader can see.
    assert [e['rank'] for e in sliced.context['rows']] == [1, 2]


def test_the_slice_is_carried_onto_every_later_window(client):
    """The failure this prevents is not an error. A window that drops the filter returns hunters from
    everywhere with rank numbers that keep counting up, so it reads as the board rather than as a bug --
    the reader has no way to tell that row 51 is answering a different question from row 50."""
    _renderable('deepslice', 'Deep Slice')
    for i in range(60):
        _in('deepslice', f'GB{i:02d}', code='GB', country='United Kingdom', bp=9900 - i)
    for i in range(60):
        _in('deepslice', f'US{i:02d}', code='US', country='United States', bp=9800 - i)

    panel = client.get(reverse('badge_ranks_panel', args=['deepslice']), {'country': 'GB'})
    assert 'data-lb-params="country=GB"' in panel.content.decode(), (
        'the board root does not carry the slice, so the engine will fetch unfiltered windows'
    )

    window = client.get(reverse('badge_ranks_panel', args=['deepslice']), {'range': 51, 'country': 'GB'})
    body = window.content.decode()
    assert body.count('<li class="lb-row') == 10
    assert 'GB50' in body and 'US' not in body, 'the second window ignored the filter'


def test_the_picker_offers_only_countries_on_THIS_board(client):
    """`active_countries()` is every country on ANY board. Reusing it here would offer a reader dozens of
    options that each answer "nobody from this country is chasing this badge", which is a filter able to
    empty the thing it filters with no warning."""
    _renderable('narrow', 'Narrow')
    _in('narrow', 'Brit', code='GB', country='United Kingdom', bp=9000)
    # Ranked on a DIFFERENT series, so `active_countries()` knows about them and this board must not.
    _renderable('elsewhere', 'Elsewhere')
    _in('elsewhere', 'Aussie', code='AU', country='Australia', bp=9000)

    codes = {c['code'] for c in client.get(reverse('badge_ranks_panel', args=['narrow'])).context['countries']}
    assert codes == {'GB'}, f'the picker offered countries with nobody on this board: {codes}'


def test_an_unknown_country_falls_back_to_the_whole_board(client):
    """A public fragment takes whatever a URL hands it. An unvalidated code returns an empty window,
    which reads as a gap in the board rather than as a bad parameter."""
    _renderable('junkcc', 'Junk')
    _in('junkcc', 'Brit', code='GB', country='United Kingdom', bp=9000)

    for raw in ('ZZ', 'not-a-code', ''):
        resp = client.get(reverse('badge_ranks_panel', args=['junkcc']), {'country': raw})
        assert resp.status_code == 200, f'country={raw!r} was not handled'
        assert resp.context['total'] == 1, f'country={raw!r} emptied the board'
        assert resp.context['selected_country'] == '', f'country={raw!r} was accepted'


def test_a_selectable_country_can_never_empty_the_board(client):
    """The reason this panel needs no "emptied by the filter" state, unlike the Global Boards landing.

    That page's picker is GLOBAL -- every country on any board -- so most options empty most boards and it
    has to explain itself when they do. This picker is scoped to hunters on THIS series, so every option
    it offers has at least one hunter behind it, and anything else is rejected by `_country()` and falls
    back to the whole board. Written as a property over the whole picker rather than one example, because
    the guarantee is what licenses the missing branch.
    """
    _renderable('everycc', 'Every CC')
    _in('everycc', 'Brit', code='GB', country='United Kingdom', bp=9000)
    _in('everycc', 'Yank', code='US', country='United States', bp=8000)
    _in('everycc', 'Aussie', code='AU', country='Australia', bp=7000)

    offered = client.get(reverse('badge_ranks_panel', args=['everycc'])).context['countries']
    assert len(offered) == 3, 'the fixture no longer exercises a multi-country picker'

    for c in offered:
        resp = client.get(reverse('badge_ranks_panel', args=['everycc']), {'country': c['code']})
        assert resp.context['rows'], f"{c['code']} is selectable and empties the board"
        assert resp.context['selected_country'] == c['code']


def test_the_ranks_panel_ships_its_filter_and_its_wiring(client):
    """The form is a real GET with a <noscript> button, and the page re-fetches the panel in place. Both
    halves have to ship: the form alone reloads the page onto the Overview tab, and the wiring alone is
    a filter that does not exist without JS."""
    _renderable('wired', 'Wired')
    _in('wired', 'Brit', code='GB', country='United Kingdom', bp=9000)

    panel = client.get(reverse('badge_ranks_panel', args=['wired'])).content.decode()
    assert '<form method="get" class="lb-filters" data-filter-form>' in panel
    assert 'id="bd2-country"' in panel

    page = client.get(reverse('badge_detail', args=['wired'])).content.decode()
    assert "closest('[data-filter-form] select')" in page, 'the panel filter is never wired up'


# ---------------------------------------------------------------------------------------------------
# The edition filter (2026-08). Not a filter over the series board's rows -- it SWITCHES THE STORE, the
# way the Global Boards landing swaps `ProfileBadgeStanding` for `ProfileEditionStanding`. Here the swap
# is `SeriesBadgeStanding` -> `UserGroupBadge`, which is keyed on a GroupBadge and therefore per
# (series x edition).
# ---------------------------------------------------------------------------------------------------

def _two_editions(slug='dual', name='Dual'):
    """A series offered in two editions, which is the only shape that renders the picker."""
    from tests.factories import BadgeSeriesFactory, GroupBadgeFactory, PlatformGroupFactory

    series = BadgeSeriesFactory(series_slug=slug, name=name)
    modern = GroupBadgeFactory(
        series=series,
        platform_group=PlatformGroupFactory(key=f'{slug}-ps5', name='PS5', sort_order=1),
        is_live=True)
    legacy = GroupBadgeFactory(
        series=series,
        platform_group=PlatformGroupFactory(key=f'{slug}-ps3', name='Legacy HD', sort_order=2),
        is_live=True)
    return series, modern, legacy


def _chasing(slug, name, progress, *, xp=None, group_xp=None, on=dt.date(2025, 1, 1),
             code='', country=''):
    """A hunter with PER-EDITION progress on a series board.

    `progress` is the `group_progress` read-model the edition board ranks on:
    `{platform_group_key: [stages_cleared, gating_count]}`, materialized on every recompute for every
    earnable edition -- started or not, so an untouched one still carries its denominator.

    `progress_bp` and `stages_cleared` are derived here the way the engine derives them: from the
    FURTHEST-ALONG edition, never from a sum. A badge is earned per edition, so there is no such thing as
    being 6 of 10 through one, and a fixture that summed would be teaching the tests a rule the product
    does not have.

    POINTS are what the board ranks on, and they mirror the write seam: `group_xp` is per edition and `xp`
    is their SUM (`badge_xp.compute_series_standings` sums XP over editions while taking the MAX for
    progress). Defaults derive one point per cleared stage, which is not the real economy but is enough to
    make ordering assertions mean something; a test that cares about the numbers passes them.

    `country_code` is set on the standing as well as the profile, because that is the column the board
    filters -- the denorm exists so a board never has to join Profile.
    """
    best_cleared, best_total = max(progress.values(), key=lambda pr: (pr[0] / pr[1]) if pr[1] else 0)
    if group_xp is None:
        group_xp = {k: v[0] for k, v in progress.items() if v[0]}
    if xp is None:
        xp = sum(group_xp.values())
    prof = ProfileFactory(display_psn_username=name, country_code=code, country=country, is_linked=True)
    SeriesBadgeStanding.objects.create(
        profile=prof, series_slug=slug, country_code=code, is_linked=True,
        xp=xp, group_xp=group_xp,
        progress_bp=int(round(10000 * best_cleared / best_total)) if best_total else 0,
        stages_cleared=best_cleared, stages_total=best_total,
        group_progress=progress, advanced_at=on,
    )
    return prof


def test_picking_an_edition_scopes_the_board_rather_than_swapping_it(client):
    """REGRESSION, reported from the browser: picking an edition said "nobody is on this board" on a
    badge with plenty of chasers.

    The filter read `UserGroupBadge` -- the EARNERS store. Keyed on a GroupBadge it is genuinely per
    (series x edition), so it looks like the right store, and it holds only hunters who FINISHED one. A
    badge with chasers and no finishers therefore emptied under every edition. A filter has to SCOPE a
    board, not swap it for a rarer one.
    """
    _two_editions()
    # One point per cleared stage, so the per-edition points and the per-edition progress agree and the
    # ordering assertions below read the way the fixture looks.
    _chasing('dual', 'Ahead5', {'dual-ps5': [4, 5], 'dual-ps3': [1, 5]})
    _chasing('dual', 'Behind5', {'dual-ps5': [2, 5], 'dual-ps3': [5, 5]})
    _chasing('dual', 'PS3Only', {'dual-ps5': [0, 5], 'dual-ps3': [3, 5]})

    ps5 = client.get(reverse('badge_ranks_panel', args=['dual']), {'edition': 'dual-ps5'})
    body = ps5.content.decode()

    # Chasers are ON it -- nobody in this fixture has finished anything on PS5.
    assert ps5.context['total'] == 2, 'the edition board is not the same population, scoped'
    assert 'Ahead5' in body and 'Behind5' in body
    # ...and an untouched edition is excluded rather than padding the board with zeroes, which is the
    # membership rule the landing's edition board already applies (`total_xp__gt=0`).
    assert 'PS3Only' not in body, 'a hunter who has not started this edition is on its board'
    # Ordered by THIS edition's progress, not the series-level one: Behind5 leads on PS3, trails here.
    assert [e['psn_username'] for e in ps5.context['rows']] == ['Ahead5', 'Behind5']

    ps3 = client.get(reverse('badge_ranks_panel', args=['dual']), {'edition': 'dual-ps3'})
    assert [e['psn_username'] for e in ps3.context['rows']] == ['Behind5', 'PS3Only', 'Ahead5']


def test_the_edition_board_counts_THAT_editions_points(client):
    """The figure has to move with the filter, or the board reorders under a column that did not change
    and reads as broken. On All editions it is the series total; under one, it is that edition's own."""
    _two_editions()
    _chasing('dual', 'Split', {'dual-ps5': [4, 5], 'dual-ps3': [1, 8]},
             group_xp={'dual-ps5': 40, 'dual-ps3': 10}, xp=50)

    whole = client.get(reverse('badge_ranks_panel', args=['dual'])).context['rows'][0]
    assert whole['primary'] == 50, 'the default board is not showing the series total'

    legacy = client.get(reverse('badge_ranks_panel', args=['dual']),
                        {'edition': 'dual-ps3'}).context['rows'][0]
    assert legacy['primary'] == 10, (
        'the edition board showed the series total rather than the edition being asked about'
    )


def test_the_default_board_counts_EVERY_edition(client):
    """"All editions" ranked on `progress_bp`, the furthest-along EDITION's fraction -- so it showed a
    board that ignored every edition except each hunter's best one, and the label promised the opposite.

    Points are the fix rather than a summed stage count: `xp` is already the series total across editions
    (`compute_series_standings` sums XP while taking the MAX for progress), and points count what was
    cleared AND weigh what it was worth, so one figure replaces the tally that was wrong anyway.
    """
    _two_editions()
    both = _chasing('dual', 'Both', {'dual-ps5': [3, 5], 'dual-ps3': [3, 5]})       # 6 points
    _chasing('dual', 'OneDeep', {'dual-ps5': [5, 5], 'dual-ps3': [0, 5]})           # 5 points

    resp = client.get(reverse('badge_ranks_panel', args=['dual']))
    rows = resp.context['rows']

    # The dual-edition chaser leads on TOTAL, though the other hunter has finished an edition outright --
    # which is exactly the ordering the old key could not produce.
    assert [r['psn_username'] for r in rows] == ['Both', 'OneDeep']
    assert rows[0]['primary'] == 6 and rows[0]['primary_label'] == 'points'
    assert rows[0].get('primary_of') is None, 'points were given a denominator they do not have'
    assert 'across every edition' in resp.context['board_meaning']
    assert both is not None


def test_the_edition_switch_carries_onto_every_later_window(client):
    """`edition` decides which ORDERING the rows come back in, so a window that drops it splices
    series-ordered rows into an edition-ordered spacer -- numbered continuously, so it reads as one list
    that inexplicably reshuffles halfway down."""
    _two_editions()
    for i in range(60):
        _chasing('dual', f'PS5-{i:02d}', {'dual-ps5': [(i % 5) + 1, 5], 'dual-ps3': [1, 5]},
                 on=dt.date(2025, 1, 1) + dt.timedelta(days=i))

    panel = client.get(reverse('badge_ranks_panel', args=['dual']), {'edition': 'dual-ps5'})
    assert 'data-lb-params="edition=dual-ps5"' in panel.content.decode()

    window = client.get(reverse('badge_ranks_panel', args=['dual']),
                        {'edition': 'dual-ps5', 'range': 51})
    assert window.content.decode().count('<li class="lb-row') == 10
    assert window.context['entries'][0]['rank'] == 51


def test_the_picker_offers_only_editions_this_series_HAS(client):
    """`active_editions()` is every edition on the site. Offering one this badge was never released in
    is a board that could only be empty."""
    from tests.factories import PlatformGroupFactory

    _two_editions()
    PlatformGroupFactory(key='unrelated', name='Unrelated', sort_order=9)   # live, but not on this series

    keys = {e['key'] for e in client.get(reverse('badge_ranks_panel', args=['dual'])).context['editions']}
    assert keys == {'dual-ps5', 'dual-ps3'}, f'the picker offered editions off this series: {keys}'


def test_a_single_edition_series_gets_no_edition_picker(client):
    """One choice plus "all editions" is two ways to see the same hunters -- a control that cannot do
    anything. The Overview tab hides its own switcher on the same rule."""
    _renderable('solo', 'Solo')
    _standing('solo', 'Someone', bp=9000, on=dt.date(2025, 1, 1))

    resp = client.get(reverse('badge_ranks_panel', args=['solo']))
    assert resp.context['editions'] == []
    assert 'id="bd2-edition"' not in resp.content.decode()


def test_an_unknown_edition_falls_back_to_the_series_board(client):
    """Silently resolving to no edition would leave a filter that appears applied and is not."""
    _two_editions()
    _chasing('dual', 'OnlyPS3', {'dual-ps5': [0, 5], 'dual-ps3': [3, 5]})

    for raw in ('nope', 'dual-ps4', ''):
        resp = client.get(reverse('badge_ranks_panel', args=['dual']), {'edition': raw})
        assert resp.context['selected_edition'] == '', f'edition={raw!r} was accepted'
        # The SERIES board, which holds a hunter who has not touched PS5 at all.
        assert 'OnlyPS3' in resp.content.decode(), f'edition={raw!r} did not fall back'


def test_the_board_card_names_the_edition_it_is_showing(client):
    """The population narrows under a filter -- one edition's chasers rather than the series' -- and the
    card is the one place that says which. Without it the reader infers a scope change from a figure that
    got smaller, which is a guess."""
    _two_editions()
    _chasing('dual', 'Holder', {'dual-ps5': [4, 5], 'dual-ps3': [1, 5]})

    whole = client.get(reverse('badge_ranks_panel', args=['dual'])).context['board_meaning']
    sliced = client.get(reverse('badge_ranks_panel', args=['dual']),
                        {'edition': 'dual-ps5'}).context['board_meaning']

    assert 'across every edition' in whole
    assert sliced != whole and 'PS5' in sliced, 'the board card described the wrong board'


def test_edition_and_country_compose(client):
    """Both apply at once, and the country picker scopes itself to the EDITION board when one is chosen
    -- resolved the other way round, a country valid for the series board could empty an edition."""
    _two_editions()
    _chasing('dual', 'Brit5', {'dual-ps5': [3, 5], 'dual-ps3': [0, 5]}, code='GB', country='United Kingdom')
    _chasing('dual', 'Yank5', {'dual-ps5': [2, 5], 'dual-ps3': [0, 5]}, code='US', country='United States')
    _chasing('dual', 'Brit3', {'dual-ps5': [0, 5], 'dual-ps3': [4, 5]}, code='GB', country='United Kingdom')

    both = client.get(reverse('badge_ranks_panel', args=['dual']),
                      {'edition': 'dual-ps5', 'country': 'GB'})
    body = both.content.decode()
    assert both.context['total'] == 1
    assert 'Brit5' in body and 'Yank5' not in body and 'Brit3' not in body
    assert 'edition=dual-ps5' in body and 'country=GB' in body, 'the pair is not carried onto windows'

    # Only GB and US earned the PS5 edition, so the picker under it offers exactly those.
    codes = {c['code'] for c in both.context['countries']}
    assert codes == {'GB', 'US'}, f'the country picker was not scoped to the edition board: {codes}'


def test_changing_one_filter_does_not_clear_the_other(client):
    """REGRESSION, reported from the browser: picking an edition reset the country and picking a country
    reset the edition, so the two could never be applied together.

    The server was always right -- `test_edition_and_country_compose` passes and always did. The bug was
    in the panel's own change handler, which sent ONLY the field that changed, so every change arrived as
    a request with the other filter absent and the view correctly read that as "not applied".

    Two assertions, because the fix has two halves and each fails differently:

      1. The form carries BOTH selects. A handler that serializes the form cannot preserve a field the
         form does not contain.
      2. The handler serializes THE FORM, not the field. This is a source assertion, which this suite
         normally avoids -- but there is no JS runner in this project, the failure is invisible to every
         markup test (the panel renders perfectly; it is the next request that is wrong), and the thing
         being pinned is one specific expression rather than a class name that might turn up in a
         comment. `FormData` appears nowhere else in this template.
    """
    _two_editions()
    # A row on the DEFAULT board (the series one), WITH a country. Two things have to be true for both
    # selects to render, and each is a real rule rather than a fixture quirk: an earner is not
    # automatically on the series board (they are two stores, which is the whole premise of the edition
    # switch), and the country picker only offers countries someone on the board actually has.
    _in('dual', 'Holder', code='US', country='United States', bp=10000)

    panel = client.get(reverse('badge_ranks_panel', args=['dual'])).content.decode()
    form_start = panel.index('<form method="get" class="lb-filters"')
    form = panel[form_start:panel.index('</form>', form_start)]
    assert 'name="edition"' in form and 'name="country"' in form, (
        'the two filters are on separate forms, so no serialization can carry both'
    )

    page = client.get(reverse('badge_detail', args=['dual'])).content.decode()
    assert 'new FormData(form)' in page, (
        'the filter handler sends one field rather than the whole form, so each filter clears the other'
    )
    assert page.count('new FormData(form)') == 1, 'more than one handler now claims this contract'


def test_the_ranks_filter_has_no_unreachable_noscript_fallback(client):
    """The Global Boards landing's identical form carries a `<noscript>` submit button and should: that
    page server-renders its board, so a JS-off reader has one to filter.

    This panel is `hidden` until JS unhides its tab, and its contents arrive by fetch. Without JS there is
    no board here at all, so a fallback button is markup that cannot be reached pretending to be a safety
    net -- and it was making the comment beside it untrue, which is how it got noticed.
    """
    _two_editions()
    _in('dual', 'Holder', code='US', country='United States', bp=10000)
    panel = client.get(reverse('badge_ranks_panel', args=['dual'])).content.decode()

    assert 'lb-filters' in panel, 'the fixture no longer renders the filter form'
    assert '<noscript>' not in panel


def test_a_filter_that_empties_the_board_leaves_you_able_to_undo_it(client):
    """REGRESSION, reported from the browser: the empty state replaced the whole panel, so the control
    that emptied the board vanished with it and browser Back was the only way out.

    The branch that keeps the chrome was written and then deleted as unreachable, on the reasoning that a
    scoped picker cannot offer an option with nobody behind it. That holds for COUNTRY and does not hold
    for EDITION: the edition picker is scoped to the editions the SERIES has, and a badge can perfectly
    well have an edition nobody has started. One filter's invariant was applied to both.
    """
    _two_editions()
    _chasing('dual', 'OnlyPS3', {'dual-ps5': [0, 5], 'dual-ps3': [3, 5]},
             code='GB', country='United Kingdom')

    empty = client.get(reverse('badge_ranks_panel', args=['dual']), {'edition': 'dual-ps5'})
    body = empty.content.decode()

    assert empty.context['rows'] == [], 'the fixture no longer empties the board'
    assert 'lb-filters' in body, 'the filter that emptied the board disappeared with it'
    # THE CONTROL THAT EMPTIED IT, specifically. That is the requirement -- a filter you cannot un-apply
    # is the dead end being fixed.
    assert 'id="bd2-edition"' in body
    assert f'value="dual-ps5" selected' in body, 'the select does not show what is applied'
    # The COUNTRY select is legitimately absent: its options are scoped to the board being shown, and
    # this board has nobody on it, so there are no countries to offer. Rendering an empty select would be
    # a control with nothing in it. Noted here because it is a decision, not an oversight -- and it costs
    # a reader who had a country selected before switching editions their selection, which is a real if
    # narrow trade for not showing dead options.
    assert 'id="bd2-country"' not in body
    # ...and it blames the SLICE, not the badge. "Nobody is chasing this one yet" over a badge with
    # chasers on another edition is a working board reading as a broken one.
    assert 'Nobody is chasing this one yet' not in body
    assert 'Nobody has started this edition yet' in body
    # No jump bar, though -- there is nothing to jump around in.
    assert 'lb-jumpbar' not in body
