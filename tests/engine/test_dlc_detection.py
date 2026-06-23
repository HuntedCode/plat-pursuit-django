"""Tests for `detect_dlc_and_refresh`: the detection logic (which badge series get
refreshed when a game gains a new trophy group). The heavy series refresh itself
(`refresh_badge_series_awards` -> handle_badge + leaderboards) is mocked, so these
tests pin the DLC-vs-not decision, not the existing badge re-evaluation."""
from datetime import timedelta
from unittest import mock

import pytest
from django.core.management import call_command
from django.utils import timezone

from trophies.models import TrophyGroup
from trophies.services import badge_refresh_service
from tests.factories import BadgeFactory, ConceptFactory, GameFactory, StageFactory

pytestmark = pytest.mark.django_db

PATCH = 'trophies.management.commands.detect_dlc_and_refresh.refresh_badge_series_awards'


def _series_game(series):
    """A game in a badge series (concept on a Stage of `series`, with a Badge)."""
    concept = ConceptFactory()
    game = GameFactory(concept=concept)
    StageFactory(series_slug=series, stage_number=1).concepts.add(concept)
    BadgeFactory(series_slug=series)
    return game


def test_dlc_on_existing_game_refreshes_its_series():
    watermark = timezone.now()
    game = _series_game('series-dlc')
    TrophyGroup.objects.create(game=game, trophy_group_id='default', created_at=watermark - timedelta(days=2))
    TrophyGroup.objects.create(game=game, trophy_group_id='001', created_at=watermark + timedelta(hours=1))

    with mock.patch(PATCH, return_value=(1, 0, 0, 0)) as m:
        call_command('detect_dlc_and_refresh', '--since', watermark.isoformat())

    m.assert_called_once_with('series-dlc', skip_notifications=True)


def test_first_sync_game_is_not_dlc():
    """A brand-new game's groups are all created together (none predate the window),
    so it must NOT trigger a refresh."""
    watermark = timezone.now()
    game = _series_game('series-new')
    TrophyGroup.objects.create(game=game, trophy_group_id='default', created_at=watermark + timedelta(minutes=5))
    TrophyGroup.objects.create(game=game, trophy_group_id='001', created_at=watermark + timedelta(minutes=5))

    with mock.patch(PATCH) as m:
        call_command('detect_dlc_and_refresh', '--since', watermark.isoformat())

    m.assert_not_called()


def test_dlc_on_game_outside_any_series_is_skipped():
    watermark = timezone.now()
    game = GameFactory(concept=ConceptFactory())  # no Stage / badge series
    TrophyGroup.objects.create(game=game, trophy_group_id='default', created_at=watermark - timedelta(days=1))
    TrophyGroup.objects.create(game=game, trophy_group_id='001', created_at=watermark + timedelta(hours=1))

    with mock.patch(PATCH) as m:
        call_command('detect_dlc_and_refresh', '--since', watermark.isoformat())

    m.assert_not_called()


def test_dry_run_detects_but_does_not_refresh():
    watermark = timezone.now()
    game = _series_game('series-dry')
    TrophyGroup.objects.create(game=game, trophy_group_id='default', created_at=watermark - timedelta(days=1))
    TrophyGroup.objects.create(game=game, trophy_group_id='001', created_at=watermark + timedelta(hours=1))

    with mock.patch(PATCH) as m:
        call_command('detect_dlc_and_refresh', '--since', watermark.isoformat(), '--dry-run')

    m.assert_not_called()


def test_concept_in_multiple_series_refreshes_each():
    watermark = timezone.now()
    concept = ConceptFactory()
    game = GameFactory(concept=concept)
    StageFactory(series_slug='series-a', stage_number=1).concepts.add(concept)
    StageFactory(series_slug='series-b', stage_number=1).concepts.add(concept)
    BadgeFactory(series_slug='series-a')
    BadgeFactory(series_slug='series-b')
    TrophyGroup.objects.create(game=game, trophy_group_id='default', created_at=watermark - timedelta(days=1))
    TrophyGroup.objects.create(game=game, trophy_group_id='001', created_at=watermark + timedelta(hours=1))

    with mock.patch(PATCH, return_value=(1, 0, 0, 0)) as m:
        call_command('detect_dlc_and_refresh', '--since', watermark.isoformat())

    assert sorted(c.args[0] for c in m.call_args_list) == ['series-a', 'series-b']


def test_multiple_dlc_groups_on_one_game_refresh_series_once():
    watermark = timezone.now()
    game = _series_game('series-multi')
    TrophyGroup.objects.create(game=game, trophy_group_id='default', created_at=watermark - timedelta(days=1))
    TrophyGroup.objects.create(game=game, trophy_group_id='001', created_at=watermark + timedelta(hours=1))
    TrophyGroup.objects.create(game=game, trophy_group_id='002', created_at=watermark + timedelta(hours=2))

    with mock.patch(PATCH, return_value=(1, 0, 0, 0)) as m:
        call_command('detect_dlc_and_refresh', '--since', watermark.isoformat())

    m.assert_called_once_with('series-multi', skip_notifications=True)


def test_watermark_advances_after_run_but_not_on_dry_run():
    watermark = timezone.now()
    game = _series_game('series-wm')
    TrophyGroup.objects.create(game=game, trophy_group_id='default', created_at=watermark - timedelta(days=1))
    TrophyGroup.objects.create(game=game, trophy_group_id='001', created_at=watermark + timedelta(hours=1))
    redis_path = 'trophies.management.commands.detect_dlc_and_refresh.redis_client'

    with mock.patch(PATCH, return_value=(1, 0, 0, 0)), mock.patch(redis_path) as rc:
        call_command('detect_dlc_and_refresh', '--since', watermark.isoformat())
    assert rc.set.called  # real run advances the watermark

    with mock.patch(PATCH), mock.patch(redis_path) as rc_dry:
        call_command('detect_dlc_and_refresh', '--since', watermark.isoformat(), '--dry-run')
    assert not rc_dry.set.called  # dry run must not advance


def test_refresh_service_returns_zeros_for_series_without_badges():
    assert badge_refresh_service.refresh_badge_series_awards('no-such-series') == (0, 0, 0, 0)
