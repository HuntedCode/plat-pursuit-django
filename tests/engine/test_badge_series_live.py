"""`BadgeSeries.objects.live()` and the badge type labels.

`live()` is the one definition of "a series with a live edition" (the set the badge pages serve), replacing
the `filter(group_badges__is_live=True)` join that was written out across the site. The type labels are
the model's own choice labels everywhere, singular, sections included.
"""
import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory
from django.urls import reverse

from tests.factories import (
    BadgeSeriesFactory, GameFactory, GroupBadgeFactory, PlatformGroupFactory, ProfileFactory, StageFactory,
)
from trophies.models import BadgeSeries, SeriesBadgeStanding
from trophies.services.collection_service import badge_type_label, badge_type_sections

pytestmark = pytest.mark.django_db


def test_live_keeps_a_series_with_a_live_edition_once():
    series = BadgeSeriesFactory(series_slug='two-live')
    GroupBadgeFactory(series=series, platform_group=PlatformGroupFactory(key='live-a'))
    GroupBadgeFactory(series=series, platform_group=PlatformGroupFactory(key='live-b'))

    # Once, with no .distinct(): a join on the editions would return this series twice.
    assert list(BadgeSeries.objects.live().values_list('series_slug', flat=True)) == ['two-live']


def test_live_drops_a_dormant_series_and_one_with_no_editions():
    GroupBadgeFactory(series=BadgeSeriesFactory(series_slug='dormant'), is_live=False)
    BadgeSeriesFactory(series_slug='no-editions')
    mixed = BadgeSeriesFactory(series_slug='mixed')
    GroupBadgeFactory(series=mixed, is_live=False, platform_group=PlatformGroupFactory(key='mixed-a'))
    GroupBadgeFactory(series=mixed, platform_group=PlatformGroupFactory(key='mixed-b'))

    assert set(BadgeSeries.objects.live().values_list('series_slug', flat=True)) == {'mixed'}


# ── call sites the suite had not pinned (each now reads live()) ────────────────────────────────────

def _staged(concept, slug, *, live):
    series = BadgeSeriesFactory(series_slug=slug, name=slug.replace('-', ' ').title())
    GroupBadgeFactory(series=series, platform_group=PlatformGroupFactory(), is_live=live)
    StageFactory(series_slug=slug).concepts.add(concept)
    return series


def test_browse_cards_name_only_live_badges():
    from trophies.views.game_views import build_game_card_context

    game = GameFactory(title_platform=['PS5'])
    _staged(game.concept, 'shown-badge', live=True)
    _staged(game.concept, 'hidden-badge', live=False)
    request = RequestFactory().get('/games/')
    request.user = AnonymousUser()

    assert build_game_card_context([game], request)['badge_map'][game.concept_id]['names'] == ['Shown Badge']


def test_the_in_badge_filter_ignores_a_dormant_badge(client):
    live_game = GameFactory(title_name='Zephyr Quest', title_platform=['PS5'], concept__unified_title='Zephyr Quest')
    dormant_game = GameFactory(title_name='Quokka Saga', title_platform=['PS5'], concept__unified_title='Quokka Saga')
    _staged(live_game.concept, 'zephyr-badge', live=True)
    _staged(dormant_game.concept, 'quokka-badge', live=False)

    html = client.get(reverse('games_list'), {'platform': 'PS5', 'in_badge': 'on'}).content.decode()

    assert 'Zephyr Quest' in html
    assert 'Quokka Saga' not in html


def test_badge_points_sum_only_live_series():
    from trophies.services.badge_xp import _live_standings

    profile = ProfileFactory()
    GroupBadgeFactory(series=BadgeSeriesFactory(series_slug='pts-live'))
    GroupBadgeFactory(series=BadgeSeriesFactory(series_slug='pts-dormant'), is_live=False)
    for slug in ('pts-live', 'pts-dormant'):
        SeriesBadgeStanding.objects.create(profile=profile, series_slug=slug, xp=100)

    assert [s.series_slug for s in _live_standings(profile.id)] == ['pts-live']


def test_the_profile_card_chases_only_live_series():
    from core.services.profile_card_service import _badge_summary

    profile = ProfileFactory()
    GroupBadgeFactory(series=BadgeSeriesFactory(series_slug='chase-live'))
    GroupBadgeFactory(series=BadgeSeriesFactory(series_slug='chase-dormant'), is_live=False)
    for slug in ('chase-live', 'chase-dormant'):
        SeriesBadgeStanding.objects.create(profile=profile, series_slug=slug, progress_bp=5000)

    assert _badge_summary(profile)['chasing'] == 1


# ── labels ─────────────────────────────────────────────────────────────────────────────────────────

def test_type_labels_are_the_models_own():
    assert badge_type_label('user') == 'User'
    assert badge_type_label('megamix') == 'Megamix'
    assert dict(badge_type_sections()) == dict(BadgeSeries.BADGE_TYPES)


def test_sections_keep_the_collections_order():
    assert [key for key, _ in badge_type_sections()] == [
        'series', 'franchise', 'collection', 'megamix', 'developer', 'user', 'event',
    ]
