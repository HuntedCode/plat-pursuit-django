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


def test_dlc_recomputes_owner_completion():
    """When DLC lands, the trophy total grows, so each owner's completion is recomputed from
    earned_trophies_count / new total. A prior-100% owner drops below 100 (exact at the boundary);
    others recompute proportionally. PSN restores the exact weighted value on next sync."""
    from tests.factories import ProfileGameFactory

    watermark = timezone.now()
    game = _series_game('series-completion')
    # Post-DLC total = 20 defined trophies (was 10 before the DLC pack landed).
    game.defined_trophies = {'bronze': 10, 'silver': 5, 'gold': 4, 'platinum': 1}
    game.save(update_fields=['defined_trophies'])
    TrophyGroup.objects.create(game=game, trophy_group_id='default', created_at=watermark - timedelta(days=1))
    TrophyGroup.objects.create(game=game, trophy_group_id='001', created_at=watermark + timedelta(hours=1))

    # A former-100% owner (10 earned of the old 10) + a mid-range owner (5 earned).
    pg_full = ProfileGameFactory(game=game, progress=100, earned_trophies_count=10)
    pg_mid = ProfileGameFactory(game=game, progress=80, earned_trophies_count=5)

    redis_path = 'trophies.management.commands.detect_dlc_and_refresh.redis_client'
    with mock.patch(PATCH, return_value=(1, 0, 0, 0)), mock.patch(redis_path):
        call_command('detect_dlc_and_refresh', '--since', watermark.isoformat())

    pg_full.refresh_from_db()
    pg_mid.refresh_from_db()
    assert pg_full.progress == 50   # 10 / 20 * 100 -- dropped from a now-false 100
    assert pg_mid.progress == 25    # 5 / 20 * 100


def test_dry_run_does_not_recompute_completion():
    """--dry-run must not write progress (nor advance the watermark)."""
    from tests.factories import ProfileGameFactory

    watermark = timezone.now()
    game = _series_game('series-completion-dry')
    game.defined_trophies = {'bronze': 10, 'silver': 5, 'gold': 4, 'platinum': 1}
    game.save(update_fields=['defined_trophies'])
    TrophyGroup.objects.create(game=game, trophy_group_id='default', created_at=watermark - timedelta(days=1))
    TrophyGroup.objects.create(game=game, trophy_group_id='001', created_at=watermark + timedelta(hours=1))
    pg = ProfileGameFactory(game=game, progress=100, earned_trophies_count=10)

    with mock.patch(PATCH), mock.patch('trophies.management.commands.detect_dlc_and_refresh.redis_client'):
        call_command('detect_dlc_and_refresh', '--since', watermark.isoformat(), '--dry-run')

    pg.refresh_from_db()
    assert pg.progress == 100   # untouched on a dry run


def test_non_dlc_game_completion_not_recomputed():
    """A game that did NOT gain DLC this window is left untouched -- NOT a blanket rewrite of everyone's
    progress (a count-based rewrite would clobber PSN's accurate grade-weighted %). A first-sync game
    (all groups after the watermark) is not DLC, so its owner keeps their reported value."""
    from tests.factories import ProfileGameFactory

    watermark = timezone.now()
    game = _series_game('series-fresh')
    game.defined_trophies = {'bronze': 10, 'silver': 5, 'gold': 4, 'platinum': 1}
    game.save(update_fields=['defined_trophies'])
    TrophyGroup.objects.create(game=game, trophy_group_id='default', created_at=watermark + timedelta(minutes=5))
    TrophyGroup.objects.create(game=game, trophy_group_id='001', created_at=watermark + timedelta(minutes=6))
    pg = ProfileGameFactory(game=game, progress=100, earned_trophies_count=5)  # PSN-reported; count-based would be 25

    with mock.patch(PATCH), mock.patch('trophies.management.commands.detect_dlc_and_refresh.redis_client'):
        call_command('detect_dlc_and_refresh', '--since', watermark.isoformat())

    pg.refresh_from_db()
    assert pg.progress == 100   # untouched -- this game didn't gain DLC


def test_completion_recompute_caps_at_100():
    """A stale earned_trophies_count above the defined total (e.g. a trophy was later removed) clamps to
    100 -- never writes progress > 100."""
    from tests.factories import ProfileGameFactory

    watermark = timezone.now()
    game = _series_game('series-cap')
    game.defined_trophies = {'bronze': 6, 'silver': 3, 'gold': 1, 'platinum': 0}  # total 10
    game.save(update_fields=['defined_trophies'])
    TrophyGroup.objects.create(game=game, trophy_group_id='default', created_at=watermark - timedelta(days=1))
    TrophyGroup.objects.create(game=game, trophy_group_id='001', created_at=watermark + timedelta(hours=1))
    pg = ProfileGameFactory(game=game, progress=90, earned_trophies_count=12)  # 12/10*100 = 120

    with mock.patch(PATCH, return_value=(1, 0, 0, 0)), mock.patch('trophies.management.commands.detect_dlc_and_refresh.redis_client'):
        call_command('detect_dlc_and_refresh', '--since', watermark.isoformat())

    pg.refresh_from_db()
    assert pg.progress == 100   # clamped from a computed 120
