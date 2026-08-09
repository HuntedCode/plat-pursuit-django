"""Titles page data layer (`MyTitlesView`).

Titles come from BADGE SERIES in the new grouping system: earning any live edition grants the series'
title. These pin the three switcher views -- Yours (held + surviving one-off awards), Within reach
(unheld but started, CLOSEST first), and All (the full live vocabulary) -- plus the equipped word.
"""
import pytest

from trophies.models import (
    BadgeSeries, SeriesBadgeStanding, Title, UserTitle,
)
from tests.factories import BadgeSeriesFactory, GroupBadgeFactory, PlatformGroupFactory, ProfileFactory

pytestmark = pytest.mark.django_db


def _series_with_title(name, title_name, live=True):
    """A badge series that grants `title_name`, with one edition (live by default)."""
    series = BadgeSeriesFactory(name=name, title=Title.objects.create(name=title_name))
    GroupBadgeFactory(series=series, platform_group=PlatformGroupFactory(), is_live=live)
    return series


def _get(client, profile):
    client.force_login(profile.user)
    return client.get('/titles/')


def test_vocabulary_is_live_series_that_grant_a_title(client):
    p = ProfileFactory()
    _series_with_title('Astro', 'Bot Wrangler')
    _series_with_title('Dormant', 'Not Yet', live=False)      # no live edition -> excluded
    BadgeSeriesFactory(name='Titleless')                       # no title -> excluded

    resp = _get(client, p)

    assert resp.status_code == 200
    assert [e['name'] for e in resp.context['all_titles']] == ['Bot Wrangler']
    assert resp.context['all_count'] == 1


def test_held_title_lands_in_yours_with_source_context(client):
    p = ProfileFactory()
    series = _series_with_title('Astro', 'Bot Wrangler')
    UserTitle.objects.create(profile=p, title=series.title, source_type='badge_series',
                             source_id=series.id)

    resp = _get(client, p)

    yours = resp.context['yours']
    assert [e['name'] for e in yours] == ['Bot Wrangler']
    entry = yours[0]
    assert entry['held'] is True and entry['is_special'] is False
    assert entry['series_name'] == 'Astro'                       # what earns it
    assert entry['url'].endswith(f'/{series.series_slug}/')      # links to the badge
    assert entry['frame'] is not None                            # medallion art composed
    assert resp.context['held_count'] == 1


def test_within_reach_is_unheld_with_progress_closest_first(client):
    p = ProfileFactory()
    near = _series_with_title('Near', 'Almost There')
    far = _series_with_title('Far', 'Long Way')
    _series_with_title('Untouched', 'No Progress')               # 0 progress -> not "within reach"
    SeriesBadgeStanding.objects.create(profile=p, series_slug=near.series_slug,
                                       progress_bp=8000, stages_cleared=4, stages_total=5)
    SeriesBadgeStanding.objects.create(profile=p, series_slug=far.series_slug,
                                       progress_bp=2000, stages_cleared=1, stages_total=5)

    resp = _get(client, p)

    reach = resp.context['within_reach']
    assert [e['name'] for e in reach] == ['Almost There', 'Long Way']   # closest first
    assert reach[0]['progress_pct'] == 80
    assert (reach[0]['stages_cleared'], reach[0]['stages_total']) == (4, 5)
    assert resp.context['within_reach_count'] == 2


def test_held_title_is_not_also_within_reach(client):
    p = ProfileFactory()
    series = _series_with_title('Astro', 'Bot Wrangler')
    SeriesBadgeStanding.objects.create(profile=p, series_slug=series.series_slug,
                                       progress_bp=10000, stages_cleared=5, stages_total=5)
    UserTitle.objects.create(profile=p, title=series.title, source_type='badge_series',
                             source_id=series.id)

    resp = _get(client, p)

    assert resp.context['within_reach'] == []
    assert [e['name'] for e in resp.context['yours']] == ['Bot Wrangler']


def test_surviving_one_off_award_shows_in_yours_as_special(client):
    p = ProfileFactory()
    UserTitle.objects.create(profile=p, title=Title.objects.create(name='Case Hardened'),
                             source_type='milestone', source_id=None)

    resp = _get(client, p)

    yours = resp.context['yours']
    assert [e['name'] for e in yours] == ['Case Hardened']
    assert yours[0]['is_special'] is True
    assert yours[0]['series_name'] is None and yours[0]['frame'] is None
    assert resp.context['all_titles'] == []       # not part of the live vocabulary


def test_equipped_title_and_holder_counts(client):
    p, other = ProfileFactory(), ProfileFactory()
    series = _series_with_title('Astro', 'Bot Wrangler')
    UserTitle.objects.create(profile=p, title=series.title, source_type='badge_series',
                             source_id=series.id, is_displayed=True)
    UserTitle.objects.create(profile=other, title=series.title, source_type='badge_series',
                             source_id=series.id)

    resp = _get(client, p)

    assert resp.context['equipped_title'].name == 'Bot Wrangler'
    assert resp.context['equipped_title_id'] == series.title_id
    assert resp.context['all_titles'][0]['holders'] == 2       # social proof
    assert resp.context['yours'][0]['is_displayed'] is True


def test_empty_state_for_a_hunter_with_nothing(client):
    p = ProfileFactory()

    resp = _get(client, p)

    assert resp.status_code == 200
    assert resp.context['yours'] == [] and resp.context['within_reach'] == []
    assert resp.context['equipped_title'] is None
