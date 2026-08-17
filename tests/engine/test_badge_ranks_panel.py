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
