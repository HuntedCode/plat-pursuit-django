"""Tests for `detect_dlc_and_refresh`: the detection logic (which badge series get refreshed when a game
gains a new trophy group). The heavy re-evaluation itself (`evaluate_and_apply_batch`) is mocked, so these
tests pin the DLC-vs-not decision, not the badge engine.

Repointed off the legacy engine in the 2026-08 cutover. The command used to call
`refresh_badge_series_awards(slug, skip_notifications=True)`; it now resolves the series' LIVE group
badges and the profiles who played its games, and hands both to `evaluate_and_apply_batch`. The
"silence" that `skip_notifications=True` used to buy is now structural: the batch entry point has no
notify parameter at all."""
from datetime import timedelta
from collections import Counter
from unittest import mock

import pytest
from django.core.management import call_command
from django.utils import timezone

from trophies.models import TrophyGroup
from tests.factories import (
    BadgeSeriesFactory, ConceptFactory, GameFactory, GroupBadgeFactory, PlatformGroupFactory,
    StageFactory,
)

pytestmark = pytest.mark.django_db

PATCH = 'trophies.management.commands.detect_dlc_and_refresh.evaluate_and_apply_batch'


def _series_game(series):
    """A game in a badge series: a concept on a Stage of `series`, with one LIVE group badge.

    The live badge matters -- the command skips a series with none, so a fixture without it would make
    every refresh assertion below pass for the wrong reason.
    """
    concept = ConceptFactory()
    game = GameFactory(concept=concept)
    StageFactory(series_slug=series, stage_number=1).concepts.add(concept)
    GroupBadgeFactory(
        series=BadgeSeriesFactory(series_slug=series),
        platform_group=PlatformGroupFactory(),
        is_live=True,
    )
    return game


def _refreshed_profiles(mock_call):
    """The profile ids a mocked `evaluate_and_apply_batch` was asked to re-evaluate.

    `call.args[0]` was never inspected before, which meant the repoint's stated reason -- re-evaluate
    EVERY player of the series, because DLC can newly qualify a hunter as easily as it lapses one -- had
    no coverage at all. Replacing the command's profile query with `Profile.objects.none()` left every
    test in this file green.
    """
    ids = set()
    for call in mock_call.call_args_list:
        profiles, _badges = call.args
        ids.update(profiles.values_list('id', flat=True))
    return ids


def _refreshed_series(mock_call):
    """The series slugs a mocked `evaluate_and_apply_batch` was asked to refresh.

    The command passes (profiles_queryset, group_badges_list), so the slug is read back off the badges
    rather than from a positional string as it was under the legacy signature.
    """
    slugs = set()
    for call in mock_call.call_args_list:
        _profiles, badges = call.args
        slugs.update(gb.series.series_slug for gb in badges)
    return slugs


def test_dlc_on_existing_game_refreshes_its_series():
    watermark = timezone.now()
    game = _series_game('series-dlc')
    TrophyGroup.objects.create(game=game, trophy_group_id='default', created_at=watermark - timedelta(days=2))
    TrophyGroup.objects.create(game=game, trophy_group_id='001', created_at=watermark + timedelta(hours=1))

    with mock.patch(PATCH, return_value=Counter()) as m:
        call_command('detect_dlc_and_refresh', '--since', watermark.isoformat())

    assert _refreshed_series(m) == {'series-dlc'}


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
    group = PlatformGroupFactory()
    GroupBadgeFactory(series=BadgeSeriesFactory(series_slug='series-a'), platform_group=group, is_live=True)
    GroupBadgeFactory(series=BadgeSeriesFactory(series_slug='series-b'), platform_group=group, is_live=True)
    TrophyGroup.objects.create(game=game, trophy_group_id='default', created_at=watermark - timedelta(days=1))
    TrophyGroup.objects.create(game=game, trophy_group_id='001', created_at=watermark + timedelta(hours=1))

    with mock.patch(PATCH, return_value=Counter()) as m:
        call_command('detect_dlc_and_refresh', '--since', watermark.isoformat())

    assert _refreshed_series(m) == {'series-a', 'series-b'}


def test_multiple_dlc_groups_on_one_game_refresh_series_once():
    watermark = timezone.now()
    game = _series_game('series-multi')
    TrophyGroup.objects.create(game=game, trophy_group_id='default', created_at=watermark - timedelta(days=1))
    TrophyGroup.objects.create(game=game, trophy_group_id='001', created_at=watermark + timedelta(hours=1))
    TrophyGroup.objects.create(game=game, trophy_group_id='002', created_at=watermark + timedelta(hours=2))

    with mock.patch(PATCH, return_value=Counter()) as m:
        call_command('detect_dlc_and_refresh', '--since', watermark.isoformat())

    assert _refreshed_series(m) == {'series-multi'}
    assert m.call_count == 1          # one refresh for the series, not one per DLC group


def test_watermark_advances_after_run_but_not_on_dry_run():
    watermark = timezone.now()
    game = _series_game('series-wm')
    TrophyGroup.objects.create(game=game, trophy_group_id='default', created_at=watermark - timedelta(days=1))
    TrophyGroup.objects.create(game=game, trophy_group_id='001', created_at=watermark + timedelta(hours=1))
    redis_path = 'trophies.management.commands.detect_dlc_and_refresh.redis_client'

    with mock.patch(PATCH, return_value=Counter()), mock.patch(redis_path) as rc:
        call_command('detect_dlc_and_refresh', '--since', watermark.isoformat())
    assert rc.set.called  # real run advances the watermark

    with mock.patch(PATCH), mock.patch(redis_path) as rc_dry:
        call_command('detect_dlc_and_refresh', '--since', watermark.isoformat(), '--dry-run')
    assert not rc_dry.set.called  # dry run must not advance


def test_series_without_a_live_badge_is_skipped():
    """A series whose editions are all dormant must not be handed to the engine. Under the legacy
    signature this was the refresh service's own no-badges guard; the guard now lives in the command,
    so it needs asserting from out here."""
    watermark = timezone.now()
    concept = ConceptFactory()
    game = GameFactory(concept=concept)
    StageFactory(series_slug='dormant', stage_number=1).concepts.add(concept)
    GroupBadgeFactory(
        series=BadgeSeriesFactory(series_slug='dormant'),
        platform_group=PlatformGroupFactory(),
        is_live=False,
    )
    TrophyGroup.objects.create(game=game, trophy_group_id='default', created_at=watermark - timedelta(days=2))
    TrophyGroup.objects.create(game=game, trophy_group_id='001', created_at=watermark + timedelta(hours=1))

    with mock.patch(PATCH) as m:
        call_command('detect_dlc_and_refresh', '--since', watermark.isoformat())

    m.assert_not_called()


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
    with mock.patch(PATCH, return_value=Counter()), mock.patch(redis_path):
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

    with mock.patch(PATCH, return_value=Counter()), mock.patch('trophies.management.commands.detect_dlc_and_refresh.redis_client'):
        call_command('detect_dlc_and_refresh', '--since', watermark.isoformat())

    pg.refresh_from_db()
    assert pg.progress == 100   # clamped from a computed 120


def test_the_refresh_covers_every_player_of_the_series_not_just_holders():
    """DLC can newly QUALIFY a hunter as easily as it lapses one, so the sweep must re-evaluate everyone
    who has played a game in the series -- holders and non-holders alike."""
    from tests.factories import ProfileFactory, ProfileGameFactory

    watermark = timezone.now()
    game = _series_game('series-pop')
    player = ProfileFactory()
    ProfileGameFactory(profile=player, game=game)
    bystander = ProfileFactory()          # plays nothing in the series
    TrophyGroup.objects.create(game=game, trophy_group_id='default', created_at=watermark - timedelta(days=2))
    TrophyGroup.objects.create(game=game, trophy_group_id='001', created_at=watermark + timedelta(hours=1))

    with mock.patch(PATCH, return_value=Counter()) as m:
        call_command('detect_dlc_and_refresh', '--since', watermark.isoformat())

    refreshed = _refreshed_profiles(m)
    assert player.id in refreshed, 'a player of the series was not re-evaluated'
    assert bystander.id not in refreshed, 'an unrelated profile was swept in'


def test_a_bundle_member_gaining_dlc_refreshes_its_series():
    """The ConceptBundle trap. A concept is either a direct stage member OR a bundle member on that stage,
    never both -- so a scan that matches only `Stage.concepts` misses every episodic/bundled game, and
    that series is never flagged no matter how much DLC it gains.

    Everything else in this cutover walks both paths; this scan did not until the 5b audit.
    """
    from tests.factories import ConceptBundleFactory

    watermark = timezone.now()
    series = BadgeSeriesFactory(series_slug='series-bundle')
    GroupBadgeFactory(series=series, platform_group=PlatformGroupFactory(), is_live=True)
    stage = StageFactory(series_slug='series-bundle', stage_number=1)
    concept = ConceptFactory()
    bundle = ConceptBundleFactory(stage=stage)
    bundle.concepts.add(concept)          # bundle member, deliberately NOT in stage.concepts
    game = GameFactory(concept=concept)

    TrophyGroup.objects.create(game=game, trophy_group_id='default', created_at=watermark - timedelta(days=2))
    TrophyGroup.objects.create(game=game, trophy_group_id='001', created_at=watermark + timedelta(hours=1))

    with mock.patch(PATCH, return_value=Counter()) as m:
        call_command('detect_dlc_and_refresh', '--since', watermark.isoformat())

    assert _refreshed_series(m) == {'series-bundle'}, 'DLC on a bundled game did not refresh its series'


def test_a_bundle_only_player_is_re_evaluated():
    """The same trap on the profile side: a hunter whose only game in the series is a bundle member must
    still be swept. The legacy query matched `stages` only and this was inherited verbatim."""
    from tests.factories import ConceptBundleFactory, ProfileFactory, ProfileGameFactory

    watermark = timezone.now()
    series = BadgeSeriesFactory(series_slug='series-bp')
    GroupBadgeFactory(series=series, platform_group=PlatformGroupFactory(), is_live=True)
    stage = StageFactory(series_slug='series-bp', stage_number=1)

    direct_concept = ConceptFactory()
    stage.concepts.add(direct_concept)
    direct_game = GameFactory(concept=direct_concept)

    bundled_concept = ConceptFactory()
    ConceptBundleFactory(stage=stage).concepts.add(bundled_concept)
    bundled_game = GameFactory(concept=bundled_concept)

    bundle_player = ProfileFactory()
    ProfileGameFactory(profile=bundle_player, game=bundled_game)

    TrophyGroup.objects.create(game=direct_game, trophy_group_id='default',
                               created_at=watermark - timedelta(days=2))
    TrophyGroup.objects.create(game=direct_game, trophy_group_id='001',
                               created_at=watermark + timedelta(hours=1))

    with mock.patch(PATCH, return_value=Counter()) as m:
        call_command('detect_dlc_and_refresh', '--since', watermark.isoformat())

    assert bundle_player.id in _refreshed_profiles(m), 'a bundle-only player was skipped'
