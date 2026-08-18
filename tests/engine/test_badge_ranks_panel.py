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
    who never scroll to it."""
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

    assert client.get(reverse('badge_ranks_panel', args=[series.series_slug])).status_code == 404


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
                                       stages_cleared=3, stages_total=4, advanced_at=dt.date(2025, 2, 1))
    body = client.get(reverse('badge_ranks_panel', args=['chase'])).content.decode()
    assert 'You are' in body and 'Not on this board yet' not in body


def test_a_signed_out_visitor_is_told_nothing_about_a_standing_they_cannot_have(client):
    series = _renderable('chase', 'Chase')
    _standing('chase', 'Ahead', bp=5000, on=dt.date(2025, 1, 1))
    body = client.get(reverse('badge_ranks_panel', args=['chase'])).content.decode()
    assert 'Not on this board yet' not in body and 'You are' not in body


def test_the_board_can_be_read_past_the_first_slice(client):
    """It stopped dead at 25 under a comment promising a link to a full board -- a page that does not
    exist and deliberately does not, since this panel REPLACED it. So the panel told a hunter their rank
    and in the same breath made the row it points at unreachable.
    """
    _renderable('deep', 'Deep')
    for i in range(27):
        _standing('deep', f'Hunter{i:02d}', bp=9900 - i, on=dt.date(2025, 1, 1))

    first = client.get(reverse('badge_ranks_panel', args=['deep']))
    body = first.content.decode()
    assert first.context['has_more'] is True
    assert first.context['next_offset'] == 25
    assert 'data-ranks-more="25"' in body
    assert 'Hunter00' in body and 'Hunter26' not in body

    more = client.get(reverse('badge_ranks_panel', args=['deep']), {'offset': 25})
    tail = more.content.decode()
    # ROWS ONLY -- the meta line and the button must not be re-emitted into the middle of the list.
    assert 'Hunter25' in tail and 'Hunter26' in tail
    assert 'bd2-ranks__meta' not in tail and 'data-ranks-more' not in tail
    # `page()` numbers by SLOT, so the continuation must not restart at #1.
    assert more.context['rows'][0]['rank'] == 26


def test_a_junk_offset_serves_the_full_panel_rather_than_erroring(client):
    _renderable('deep', 'Deep')
    _standing('deep', 'Only', bp=5000, on=dt.date(2025, 1, 1))
    for raw in ('abc', '-5', ''):
        resp = client.get(reverse('badge_ranks_panel', args=['deep']), {'offset': raw})
        assert resp.status_code == 200
        assert 'bd2-ranks__meta' in resp.content.decode(), f'offset={raw!r} lost the panel chrome'


def test_the_server_decides_whether_more_remain(client):
    """The client used to infer "that was the last slice" from a short response, which cost one dead click
    on every board whose size is an exact multiple of the page size. `X-Has-Next` is the same header
    `ContractsResultsView` already uses for its infinite scroll."""
    _renderable('exact', 'Exact')
    for i in range(25):                                  # EXACTLY one slice
        _standing('exact', f'Hunter{i:02d}', bp=9900 - i, on=dt.date(2025, 1, 1))

    full = client.get(reverse('badge_ranks_panel', args=['exact']))
    assert full.context['has_more'] is False, 'a board of exactly one slice offered more'

    _standing('exact', 'Hunter25', bp=5000, on=dt.date(2025, 1, 1))
    more = client.get(reverse('badge_ranks_panel', args=['exact']), {'offset': 25})
    assert more['X-Has-Next'] == '0', 'the last slice claimed more remained'

    for i in range(30):
        _standing('exact', f'Later{i:02d}', bp=4000 - i, on=dt.date(2025, 1, 1))
    mid = client.get(reverse('badge_ranks_panel', args=['exact']), {'offset': 25})
    assert mid['X-Has-Next'] == '1', 'a mid-board slice claimed it was the last'


def test_the_appended_rows_carry_the_class_the_reveal_engine_looks_for(client):
    """`staggerReveal` puts `.pp-reveal` on the wall permanently, and `.pp-reveal .lb-row { opacity: 0 }`
    -- so an appended row is INVISIBLE until the returned handle observes it and adds `.is-revealed`.
    The client-side half of that fix cannot be tested here; what this pins is that the fragment still
    emits `.lb-row` elements, which is the selector both the append and the observer key on."""
    _renderable('deep2', 'Deep2')
    for i in range(27):
        _standing('deep2', f'H{i:02d}', bp=9900 - i, on=dt.date(2025, 1, 1))

    tail = client.get(reverse('badge_ranks_panel', args=['deep2']), {'offset': 25}).content.decode()
    assert tail.count('<li class="lb-row') == 2   # closing-quote-free prefix; `lb-row__rank` etc. must not match


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


def test_a_huge_offset_is_clamped_rather_than_scanned(client):
    """Same reasoning as the job board's page cap: a public fragment must not accept a nine-figure
    OFFSET. Distinct from the junk-offset test above, which covers unparseable values."""
    _renderable('deep3', 'Deep3')
    _standing('deep3', 'Only', bp=5000, on=dt.date(2025, 1, 1))

    resp = client.get(reverse('badge_ranks_panel', args=['deep3']), {'offset': 99999999})

    assert resp.status_code == 200
    assert resp['X-Has-Next'] == '0'
