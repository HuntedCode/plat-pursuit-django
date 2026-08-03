"""Badge detail (rebuild, Phase 1): the badge_detail_service data layer + the view render."""
import pytest
from django.urls import reverse
from django.utils import timezone

from trophies.models import ProfileGame, TrophyGroup, ProfileTrophyGroup
from trophies.services.badge_detail_service import get_badge_detail
from trophies.services.badge_apply import evaluate_and_apply
from tests.factories import (
    ProfileFactory, ConceptFactory, GameFactory, StageFactory,
    PlatformGroupFactory, BadgeSeriesFactory, GroupBadgeFactory,
)

pytestmark = pytest.mark.django_db


def _stage(series, n, platforms):
    """Stage n for the series: a concept with one game per platform (stages are SHARED across the series'
    groups; the platform routing decides which game gates for which group)."""
    st = StageFactory(series_slug=series.series_slug, stage_number=n)
    c = ConceptFactory()
    st.concepts.add(c)
    return {p: GameFactory(concept=c, title_platform=[p]) for p in platforms}


def _group(series, key, name, platforms):
    pg = PlatformGroupFactory(key=key, name=name, platforms=platforms, exclude_delisted=(key == 'ultra-hd'))
    return GroupBadgeFactory(series=series, platform_group=pg, is_live=True)


def _complete(profile, game, full=False):
    ProfileGame.objects.update_or_create(profile=profile, game=game, defaults={'progress': 100 if full else 50})
    tg, _ = TrophyGroup.objects.get_or_create(game=game, trophy_group_id='default', defaults={'trophy_group_name': 'B'})
    ProfileTrophyGroup.objects.update_or_create(
        profile=profile, trophy_group=tg, defaults={'progress': 100, 'last_trophy_at': timezone.now()},
    )


# ------------------------------------------------------------------ service ------------------------------

def test_service_earned_group_has_state_rank_and_xp():
    series = BadgeSeriesFactory(series_slug='gow', name='God of War')
    s1 = _stage(series, 1, ['PS5'])
    s2 = _stage(series, 2, ['PS5'])
    gb = _group(series, 'ultra-hd', 'Ultra HD', ['PS4', 'PS5'])
    profile = ProfileFactory()
    _complete(profile, s1['PS5'])
    _complete(profile, s2['PS5'])
    evaluate_and_apply(profile, [gb])            # creates the UserGroupBadge hold + standing

    detail = get_badge_detail(series, profile)
    assert len(detail.groups) == 1 and not detail.has_multiple_groups
    grp = detail.groups[0]
    assert grp.state == 'earned' and grp.progress_pct == 100
    assert grp.earners_rank == 1 and grp.earned_count == 1
    assert detail.series_xp > 0 and detail.viewer_state == 'earned'
    assert grp.frame['state'] == 'earned' and grp.frame['art_layers']   # medallion frame is built
    # Badge-specific stats: 2 PS5 games route to this Ultra HD group; XP = 2 gating stages + completion bonus.
    assert grp.games_count == 2 and grp.xp_on_offer == 2 * 500 + 600
    # Stage journey: 2 stages, each with its PS5 game, both complete for this earner.
    assert len(grp.stages) == 2
    assert all(s['completion_state'] == 'complete' and len(s['obtainable_games']) == 1 for s in grp.stages)


def test_service_partial_progress_is_in_progress_without_a_hold():
    series = BadgeSeriesFactory(series_slug='p', name='Partial')
    s1 = _stage(series, 1, ['PS5'])
    _stage(series, 2, ['PS5'])
    _stage(series, 3, ['PS5'])
    _group(series, 'ultra-hd', 'Ultra HD', ['PS4', 'PS5'])
    profile = ProfileFactory()
    _complete(profile, s1['PS5'])                # 1 of 3, not earned -> no hold
    grp = get_badge_detail(series, profile).groups[0]
    assert grp.state == 'in_progress' and grp.earners_rank is None
    assert grp.stages_cleared == 1 and grp.gating_count == 3


def test_service_bundle_renders_in_journey():
    from trophies.models import ConceptBundle
    series = BadgeSeriesFactory(series_slug='ep', name='Episodic')
    st = StageFactory(series_slug='ep', stage_number=1)
    bundle = ConceptBundle.objects.create(stage=st, label='The Trilogy')
    for _ in range(2):
        c = ConceptFactory()
        GameFactory(concept=c, title_platform=['PS5'])
        bundle.concepts.add(c)
    _group(series, 'ultra-hd', 'Ultra HD', ['PS4', 'PS5'])
    stages = get_badge_detail(series, None).groups[0].stages
    assert len(stages) == 1 and len(stages[0]['bundles']) == 1
    b = stages[0]['bundles'][0]
    assert b['label'] == 'The Trilogy' and b['total_members'] == 2


def test_service_multiple_groups_flags_selector():
    series = BadgeSeriesFactory(series_slug='multi', name='Multi')
    _stage(series, 1, ['PS3', 'PS5'])            # ONE shared stage, a game for each platform
    _group(series, 'legacy-hd', 'Legacy HD', ['PS3', 'PSVITA'])
    _group(series, 'ultra-hd', 'Ultra HD', ['PS4', 'PS5'])
    detail = get_badge_detail(series, None)      # anon
    assert detail.has_multiple_groups and len(detail.groups) == 2
    assert detail.viewer_state == 'none'
    # Default medallion metal: Legacy HD -> gold, Ultra HD -> platinum; each gets a backdrop plate.
    by_key = {g.platform_group.key: g for g in detail.groups}
    assert by_key['legacy-hd'].frame['tier'] == 'gold' and by_key['ultra-hd'].frame['tier'] == 'platinum'
    assert by_key['legacy-hd'].frame['art_layers']   # backdrop plate present (fixes the bare white-rim look)


# ------------------------------------------------------------------ view render --------------------------

def test_view_renders_for_anon(client):
    series = BadgeSeriesFactory(series_slug='gow', name='God of War')
    _stage(series, 1, ['PS5'])
    _group(series, 'ultra-hd', 'Ultra HD', ['PS4', 'PS5'])
    resp = client.get(reverse('badge_detail', kwargs={'series_slug': 'gow'}))
    assert resp.status_code == 200
    body = resp.content.decode()
    assert 'God of War' in body and 'Ultra HD' in body
    assert 'Stage 1' in body           # the stage journey rendered


def test_view_dormant_series_404_for_anon(client):
    series = BadgeSeriesFactory(series_slug='dorm', name='Dormant')
    pg = PlatformGroupFactory(key='ultra-hd', name='Ultra HD', platforms=['PS4', 'PS5'])
    GroupBadgeFactory(series=series, platform_group=pg, is_live=False)   # dormant -> staff-only
    resp = client.get(reverse('badge_detail', kwargs={'series_slug': 'dorm'}))
    assert resp.status_code == 404
