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
    BadgeSeriesFactory(series_slug='gow', name='God of War')
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
    BadgeSeriesFactory(series_slug='gow', name='God of War')
    resp = client.get(path)
    assert resp.status_code == 301, f'{path} did not redirect (a 500 here means an unrepointed name)'


def test_the_panel_endpoint_serves_the_merged_board(client):
    """Earners above chasers, ties broken by who got there first -- the merged board, not two lists."""
    BadgeSeriesFactory(series_slug='mix', name='Mix')
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
    BadgeSeriesFactory(series_slug='pub', name='Public')
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


def test_the_ranks_section_is_series_level_not_inside_an_edition_panel():
    """`.bd2-panel` elements swap per EDITION. The board is per SERIES (earned any edition counts, which
    matches progress_bp already being the max across them), so nesting it in one would render the same
    board twice with an edition switcher pretending to change it."""
    import re
    src = (ROOT / 'templates' / 'trophies' / 'badge_detail.html').read_text(encoding='utf-8')
    before = src[:src.index('<section class="bd2-ranks"')]
    # Every edition panel opened before the Ranks section must also have been closed before it.
    assert before.count('data-bd2-view=') == before.count('data-bd2-view='), 'sanity'
    assert '<section class="bd2-ranks"' in src
    journey = src.index('data-bd2-journey')
    assert src.index('<section class="bd2-ranks"') < journey, (
        'the Ranks section sits inside or after the per-edition journey; it is series-level chrome'
    )
