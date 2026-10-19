"""The badge page's stage icon: DERIVED from the stage's games, never stored.

Owner rule (2026-10), in order: real cover art only (no art anywhere -> no icon), then a version a hunter can
still get, then the most recently released. "Released" is that version's OWN date: its trusted IGDB first
PlayStation release (a PS4 remaster's 2015, not the 2001 original's), else its PSN date. The stored
`Stage.stage_icon` it replaces was set from whichever concept happened to come first and re-derived by a
signal on every edit.

Every ordering test creates its expected winner FIRST, so it has the LOWEST game id: the final tie-break is
the highest id, and a winner created last would pass even if the date ordering were deleted.
"""
from datetime import datetime, timezone as dt_timezone

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from tests.factories import (
    BadgeSeriesFactory, ConceptBundleFactory, ConceptFactory, GameFactory, GroupBadgeFactory,
    IGDBMatchFactory, PlatformGroupFactory, StageFactory,
)
from trophies.services import badge_detail_service
from trophies.services.badge_detail_service import get_badge_detail

pytestmark = pytest.mark.django_db


def _date(year):
    return datetime(year, 6, 1, tzinfo=dt_timezone.utc)


def _version(stage, name, *, psn_year=None, igdb_year=None, trusted=True, obtainable=True, delisted=False,
             art=True, bundle=None):
    """One concept (a version) with one PS5 game, added to the stage or a bundle. With `art` its cover is
    `https://img.test/<name>.png` (real art); without, only the generic title icon `.../<name>-icon.png`."""
    concept = ConceptFactory(release_date=_date(psn_year) if psn_year else None)
    if igdb_year is not None:
        IGDBMatchFactory(
            concept=concept, igdb_first_release_date=_date(igdb_year),
            status='auto_accepted' if trusted else 'pending_review',
        )
    GameFactory(
        concept=concept, title_platform=['PS5'], is_obtainable=obtainable, is_delisted=delisted,
        title_image=f'https://img.test/{name}.png' if art else '',
        title_icon_url=f'https://img.test/{name}-icon.png',
    )
    (bundle.concepts if bundle is not None else stage.concepts).add(concept)
    return concept


def _series(slug, exclude_delisted=True):
    series = BadgeSeriesFactory(series_slug=slug)
    GroupBadgeFactory(
        series=series, is_live=True,
        platform_group=PlatformGroupFactory(
            key=f'{slug}-g', name='Ultra', platforms=['PS4', 'PS5'], exclude_delisted=exclude_delisted,
        ),
    )
    return series, StageFactory(series_slug=slug, stage_number=1)


def _stage_dicts(series):
    [group] = get_badge_detail(series, None).groups
    return group.stages + group.retired_stages


def _icon(series):
    [stage] = _stage_dicts(series)
    return stage['icon_url']


def test_the_newest_version_by_its_igdb_date_provides_the_icon():
    series, stage = _series('icon-newest')
    _version(stage, 'remaster', igdb_year=2015)
    _version(stage, 'original', igdb_year=2001)

    assert _icon(series) == 'https://img.test/remaster.png'


def test_the_igdb_date_outranks_the_psn_date_and_untrusted_matches_are_ignored():
    # 'relisted' has a late PSN date but its own IGDB date is old; 'untrusted' has a late IGDB date on a
    # match nobody accepted, so its (older) PSN date counts.
    series, stage = _series('icon-igdb-first')
    _version(stage, 'real-newest', psn_year=2018)
    _version(stage, 'relisted', psn_year=2022, igdb_year=2005)
    _version(stage, 'untrusted', psn_year=2010, igdb_year=2030, trusted=False)

    assert _icon(series) == 'https://img.test/real-newest.png'


def test_an_unobtainable_version_loses_to_an_older_obtainable_one():
    series, stage = _series('icon-obtainable')
    _version(stage, 'still-sold', igdb_year=2010)
    _version(stage, 'pulled', igdb_year=2020, obtainable=False)

    assert _icon(series) == 'https://img.test/still-sold.png'


def test_a_delisted_version_loses_where_the_edition_excludes_delisted():
    series, stage = _series('icon-delisted')
    _version(stage, 'listed', igdb_year=2010)
    _version(stage, 'delisted', igdb_year=2020, delisted=True)

    assert _icon(series) == 'https://img.test/listed.png'


def test_undated_versions_rank_below_dated_ones():
    series, stage = _series('icon-undated')
    _version(stage, 'dated', psn_year=1999)
    _version(stage, 'undated')

    assert _icon(series) == 'https://img.test/dated.png'


def test_real_cover_art_beats_a_newer_version_with_only_the_generic_icon():
    series, stage = _series('icon-art')
    _version(stage, 'has-art', igdb_year=2005)
    _version(stage, 'no-art', igdb_year=2020, art=False)

    assert _icon(series) == 'https://img.test/has-art.png'


def test_with_no_real_art_anywhere_the_stage_shows_no_icon():
    # The generic title icon is never used: it would crop badly in the square, and the stored icon this
    # replaced never showed it either.
    series, stage = _series('icon-no-art')
    _version(stage, 'newer', igdb_year=2020, art=False)
    _version(stage, 'older', igdb_year=2005, art=False)

    assert _icon(series) is None


def test_real_art_on_a_delisted_version_beats_an_obtainable_one_with_none():
    # Owner's order: real art first, THEN obtainability. The icon is a picture of the game.
    series, stage = _series('icon-art-over-obtainable')
    _version(stage, 'delisted-with-art', igdb_year=2005, obtainable=False)
    _version(stage, 'live-no-art', igdb_year=2020, art=False)

    assert _icon(series) == 'https://img.test/delisted-with-art.png'


def test_a_bundle_only_stage_takes_its_icon_from_the_newest_member():
    series, stage = _series('icon-bundle')
    bundle = ConceptBundleFactory(stage=stage)
    _version(stage, 'episode-2', igdb_year=2013, bundle=bundle)
    _version(stage, 'episode-1', igdb_year=2012, bundle=bundle)

    assert _icon(series) == 'https://img.test/episode-2.png'


def test_a_delisted_bundle_member_cannot_outrank_a_live_standalone_game():
    # Bundle members are sorted into "can still get" by the same edition-aware rule as the stage's games.
    series, stage = _series('icon-mixed')
    _version(stage, 'live-standalone', igdb_year=2015)
    bundle = ConceptBundleFactory(stage=stage)
    _version(stage, 'delisted-episode', igdb_year=2018, delisted=True, bundle=bundle)

    assert _icon(series) == 'https://img.test/live-standalone.png'


def test_a_newer_bundle_member_beats_an_older_standalone_game():
    series, stage = _series('icon-mixed-live')
    bundle = ConceptBundleFactory(stage=stage)
    _version(stage, 'new-episode', igdb_year=2018, bundle=bundle)
    _version(stage, 'old-standalone', igdb_year=2015)

    assert _icon(series) == 'https://img.test/new-episode.png'


def test_when_nothing_is_obtainable_the_newest_unobtainable_version_is_used():
    # A retired stage still shows an icon: the "gone" pool is the fallback.
    series, stage = _series('icon-gone')
    _version(stage, 'gone-newer', igdb_year=2016, obtainable=False)
    _version(stage, 'gone-older', igdb_year=2009, obtainable=False)

    assert _icon(series) == 'https://img.test/gone-newer.png'


def test_the_icon_pick_adds_no_queries_to_the_page(monkeypatch):
    # Measured on the REAL page path: the query count with the pick vs. with it stubbed out.
    series, stage = _series('icon-queries')
    bundle = ConceptBundleFactory(stage=stage)
    for year in (2001, 2008):
        _version(stage, f'v{year}', igdb_year=year)
    _version(stage, 'no-match', psn_year=2003)   # no IGDBMatch: the missing reverse one-to-one path
    _version(stage, 'episode', igdb_year=2012, bundle=bundle)

    _icon(series)  # warm the page's own caches, so both measured runs start from the same state
    with CaptureQueriesContext(connection) as with_pick:
        assert _icon(series) == 'https://img.test/episode.png'
    monkeypatch.setattr(badge_detail_service, '_stage_icon_url', lambda *args: None)
    with CaptureQueriesContext(connection) as without_pick:
        assert _icon(series) is None   # the stub took effect, so this measures the page WITHOUT the pick

    assert len(with_pick) == len(without_pick)


def test_the_page_renders_the_derived_icon_in_the_stage_and_its_requirement_tile(client):
    series, stage = _series('icon-render')
    _version(stage, 'shown', igdb_year=2015)

    html = client.get(f'/badges/{series.series_slug}/').content.decode()

    # Scoped to the two icon elements: the game card renders the same cover, so a bare count would pass on
    # the card alone.
    assert '<span class="bd-stage__ico"><img src="https://img.test/shown.png"' in html
    assert '<img src="https://img.test/shown.png" alt="" class="bd-req__tile-ico"' in html
