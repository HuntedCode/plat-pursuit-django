"""Integration tests for the convert_series_to_groups management command.

Scaffolds a new BadgeSeries + GroupBadges from a legacy tier-1 Badge: metadata copy, spanned-group
auto-detection, dormant group badges, idempotency, dry-run, megamix mapping, and unmapped-platform reporting.
"""
from io import StringIO

import pytest
from django.core.management import call_command

from trophies.models import BadgeSeries, GroupBadge, Title
from tests.factories import (
    BadgeFactory, StageFactory, ConceptFactory, GameFactory, PlatformGroupFactory,
)

pytestmark = pytest.mark.django_db


def _groups():
    legacy = PlatformGroupFactory(key='legacy-hd', name='Legacy HD', platforms=['PS3', 'PSVITA'], exclude_delisted=False)
    ultra = PlatformGroupFactory(key='ultra-hd', name='Ultra HD', platforms=['PS4', 'PS5'], exclude_delisted=True)
    return legacy, ultra


def _old_series(slug='gow', name='God of War', **badge_kwargs):
    badge = BadgeFactory(series_slug=slug, tier=1, name=name, is_live=True, **badge_kwargs)
    stage = StageFactory(series_slug=slug, stage_number=1)
    concept = ConceptFactory()
    stage.concepts.add(concept)
    return badge, stage, concept


def test_convert_creates_series_and_spanned_group_badges():
    legacy, ultra = _groups()
    _, _, concept = _old_series('gow')
    GameFactory(concept=concept, title_platform=['PS3'])
    GameFactory(concept=concept, title_platform=['PS5'])
    call_command('convert_series_to_groups', 'gow')

    series = BadgeSeries.objects.get(series_slug='gow')
    assert series.name == 'God of War' and series.badge_type == 'series' and series.completion_policy == 'all'
    gbs = GroupBadge.objects.filter(series=series)
    assert set(gbs.values_list('platform_group_id', flat=True)) == {legacy.id, ultra.id}
    assert all(gb.is_live is False for gb in gbs)   # dormant


def test_only_spanned_groups_created():
    legacy, ultra = _groups()
    _, _, concept = _old_series('ps5only')
    GameFactory(concept=concept, title_platform=['PS5'])   # no PS3/Vita game
    call_command('convert_series_to_groups', 'ps5only')
    groups = list(GroupBadge.objects.filter(series__series_slug='ps5only').values_list('platform_group_id', flat=True))
    assert groups == [ultra.id]                             # Legacy HD not created


def test_idempotent():
    _groups()
    _, _, concept = _old_series('idem')
    GameFactory(concept=concept, title_platform=['PS5'])
    call_command('convert_series_to_groups', 'idem')
    call_command('convert_series_to_groups', 'idem')        # re-run
    assert BadgeSeries.objects.filter(series_slug='idem').count() == 1
    assert GroupBadge.objects.filter(series__series_slug='idem').count() == 1


def test_dry_run_writes_nothing():
    _groups()
    _, _, concept = _old_series('dry')
    GameFactory(concept=concept, title_platform=['PS5'])
    call_command('convert_series_to_groups', 'dry', '--dry-run')
    assert not BadgeSeries.objects.filter(series_slug='dry').exists()
    assert not GroupBadge.objects.filter(series__series_slug='dry').exists()


def test_megamix_maps_to_min_count():
    _groups()
    _, _, concept = _old_series('mm', badge_type='megamix', requires_all=False, min_required=3)
    GameFactory(concept=concept, title_platform=['PS5'])
    call_command('convert_series_to_groups', 'mm')
    series = BadgeSeries.objects.get(series_slug='mm')
    assert series.badge_type == 'megamix' and series.completion_policy == 'min_count' and series.min_required == 3


def test_title_reused_from_legacy_badge():
    _groups()
    title = Title.objects.create(name='Champion')
    _, _, concept = _old_series('titled', title=title)
    GameFactory(concept=concept, title_platform=['PS5'])
    call_command('convert_series_to_groups', 'titled')
    assert BadgeSeries.objects.get(series_slug='titled').title_id == title.id


def test_unmapped_platform_is_reported_but_mapped_group_still_created():
    _groups()
    _, _, concept = _old_series('psp')
    GameFactory(concept=concept, title_platform=['PSP'])    # maps to no group
    GameFactory(concept=concept, title_platform=['PS5'])    # Ultra HD
    out = StringIO()
    call_command('convert_series_to_groups', 'psp', stdout=out)
    assert 'PSP' in out.getvalue()
    assert GroupBadge.objects.filter(series__series_slug='psp').count() == 1   # Ultra still created


def test_all_flag_converts_every_live_series():
    _groups()
    for slug in ['a1', 'a2']:
        _, _, concept = _old_series(slug)
        GameFactory(concept=concept, title_platform=['PS5'])
    call_command('convert_series_to_groups', '--all')
    assert BadgeSeries.objects.filter(series_slug__in=['a1', 'a2']).count() == 2


def test_badge_image_reference_copied_from_legacy():
    _groups()
    badge, _, concept = _old_series('art')
    badge.badge_image = 'badges/main/god-of-war.png'      # set the field name (no real file needed)
    badge.save()
    GameFactory(concept=concept, title_platform=['PS5'])
    call_command('convert_series_to_groups', 'art')
    assert BadgeSeries.objects.get(series_slug='art').badge_image.name == 'badges/main/god-of-war.png'


def test_rerun_fills_missing_art_on_existing_series():
    from tests.factories import BadgeSeriesFactory
    _groups()
    badge, _, concept = _old_series('bf')
    badge.badge_image = 'badges/main/bf.png'
    badge.save()
    GameFactory(concept=concept, title_platform=['PS5'])
    BadgeSeriesFactory(series_slug='bf', name='BF')          # scaffolded earlier WITHOUT art
    call_command('convert_series_to_groups', 'bf')           # re-run backfills the art
    assert BadgeSeries.objects.get(series_slug='bf').badge_image.name == 'badges/main/bf.png'
