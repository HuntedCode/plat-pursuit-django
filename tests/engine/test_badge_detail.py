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
    # Series standing (fed by recompute_standing in apply): rank #1 of the 1 profile with a standing.
    assert detail.series_rank == 1 and detail.series_size == 1
    assert grp.frame['state'] == 'earned' and grp.frame['art_layers']   # medallion frame is built
    # Badge-specific stats: 2 PS5 games route to this Ultra HD group; XP = 2 gating stages + completion bonus.
    assert grp.games_count == 2 and grp.xp_on_offer == 2 * 500 + 600
    # Per-group My Stats present for the viewer (2 games route here); zeroed haul since _complete sets no trophies.
    assert grp.user_stats is not None and grp.user_stats['games_total'] == 2
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


def test_service_user_stats_aggregate_per_group():
    # The per-group My Stats: trophy haul + play time + games platted/100%'d + the stage split, all from
    # denormalized ProfileGame fields over THIS badge's games (bounded / whale-safe).
    from datetime import timedelta
    series = BadgeSeriesFactory(series_slug='us', name='UserStats')
    s1 = _stage(series, 1, ['PS5'])
    _group(series, 'ultra-hd', 'Ultra HD', ['PS4', 'PS5'])
    profile = ProfileFactory()
    ProfileGame.objects.update_or_create(profile=profile, game=s1['PS5'], defaults={
        'progress': 100, 'has_plat': True,
        'earned_trophies': {'bronze': 10, 'silver': 5, 'gold': 2, 'platinum': 1},
        'earned_trophies_count': 18, 'play_duration': timedelta(hours=12),
        'first_played_date_time': timezone.now() - timedelta(days=30),
        'most_recent_trophy_date': timezone.now(),
    })
    us = get_badge_detail(series, profile).groups[0].user_stats
    assert us is not None
    assert us['haul'] == {'bronze': 10, 'silver': 5, 'gold': 2, 'platinum': 1} and us['trophies_total'] == 18
    assert us['games_platted'] == 1 and us['games_hundred'] == 1 and us['playtime_hours'] == 12
    assert us['stages_platted'] == 1 and us['stages_hundred'] == 1
    assert us['first_played'] is not None and us['last_trophy'] is not None


def test_service_user_stats_none_for_anon():
    series = BadgeSeriesFactory(series_slug='an', name='Anon')
    _stage(series, 1, ['PS5'])
    _group(series, 'ultra-hd', 'Ultra HD', ['PS4', 'PS5'])
    assert get_badge_detail(series, None).groups[0].user_stats is None


def test_service_delisted_game_splits_from_obtainable():
    # Backfilled from the retired tier-page suite: the journey splits a stage's games into obtainable vs
    # delisted (an unobtainable title satisfies but doesn't gate; Ultra HD excludes delisted).
    series = BadgeSeriesFactory(series_slug='dl', name='Delisted')
    st = StageFactory(series_slug='dl', stage_number=1)
    c = ConceptFactory()
    st.concepts.add(c)
    GameFactory(concept=c, title_platform=['PS5'])                          # obtainable
    GameFactory(concept=c, title_platform=['PS5'], is_obtainable=False)     # delisted / unobtainable
    _group(series, 'ultra-hd', 'Ultra HD', ['PS4', 'PS5'])
    stages = get_badge_detail(series, None).groups[0].stages
    assert len(stages) == 1
    assert len(stages[0]['obtainable_games']) == 1 and len(stages[0]['delisted_games']) == 1


def test_service_marks_lowest_open_stage_up_next():
    # Backfilled: stages complete in ANY order, so completing a HIGHER stage still leaves the lowest OPEN one
    # flagged 'is_next' (a suggested entry point), and the completed stage is not the suggestion.
    series = BadgeSeriesFactory(series_slug='nx', name='Next')
    _stage(series, 1, ['PS5'])
    s2 = _stage(series, 2, ['PS5'])
    _group(series, 'ultra-hd', 'Ultra HD', ['PS4', 'PS5'])
    profile = ProfileFactory()
    _complete(profile, s2['PS5'])                # complete the HIGHER stage only
    sd = {s['stage'].stage_number: s for s in get_badge_detail(series, profile).groups[0].stages}
    assert sd[2]['completion_state'] == 'complete'
    assert sd[1]['is_next'] is True and sd[2]['is_next'] is False


def test_service_anon_has_no_up_next():
    # Backfilled regression guard: "up next" is grounded in the viewer's own progress, so an anon viewer (no
    # known progress) must see NO up-next suggestion (else every stage would falsely read as next).
    series = BadgeSeriesFactory(series_slug='anx', name='AnonNext')
    _stage(series, 1, ['PS5'])
    _stage(series, 2, ['PS5'])
    _group(series, 'ultra-hd', 'Ultra HD', ['PS4', 'PS5'])
    stages = get_badge_detail(series, None).groups[0].stages
    assert stages and all(s['is_next'] is False for s in stages)


def test_service_user_stats_platted_distinct_from_hundred():
    # A 100%'d game WITHOUT a platinum counts toward 100%'d only, never platted -- the two metrics must not
    # be conflated (guards _group_user_stats' has_plat vs progress==100 split).
    series = BadgeSeriesFactory(series_slug='pd', name='PlatDistinct')
    s1 = _stage(series, 1, ['PS5'])
    s2 = _stage(series, 2, ['PS5'])
    _group(series, 'ultra-hd', 'Ultra HD', ['PS4', 'PS5'])
    profile = ProfileFactory()
    ProfileGame.objects.update_or_create(profile=profile, game=s1['PS5'], defaults={'progress': 100, 'has_plat': True})
    ProfileGame.objects.update_or_create(profile=profile, game=s2['PS5'], defaults={'progress': 100, 'has_plat': False})
    us = get_badge_detail(series, profile).groups[0].user_stats
    assert us['games_platted'] == 1 and us['games_hundred'] == 2         # 100%'d includes the non-platted game
    assert us['stages_platted'] == 1 and us['stages_hundred'] == 2


def test_service_user_stats_bounded_to_badge_games():
    # Whale-safety: the aggregate reads ONLY this badge's games. A ProfileGame for an unrelated game (not in
    # the series) must not leak into the haul / play time / counts.
    from datetime import timedelta
    series = BadgeSeriesFactory(series_slug='wb', name='WhaleBound')
    s1 = _stage(series, 1, ['PS5'])
    _group(series, 'ultra-hd', 'Ultra HD', ['PS4', 'PS5'])
    profile = ProfileFactory()
    ProfileGame.objects.update_or_create(profile=profile, game=s1['PS5'], defaults={
        'progress': 100, 'has_plat': True, 'earned_trophies': {'bronze': 3},
        'earned_trophies_count': 3, 'play_duration': timedelta(hours=5),
    })
    unrelated = GameFactory(concept=ConceptFactory(), title_platform=['PS5'])   # NOT in the series
    ProfileGame.objects.update_or_create(profile=profile, game=unrelated, defaults={
        'progress': 100, 'has_plat': True, 'earned_trophies': {'bronze': 999},
        'earned_trophies_count': 999, 'play_duration': timedelta(hours=999),
    })
    us = get_badge_detail(series, profile).groups[0].user_stats
    assert us['trophies_total'] == 3 and us['haul']['bronze'] == 3   # unrelated game's 999 excluded
    assert us['playtime_hours'] == 5 and us['games_total'] == 1


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
    # Backfilled a11y / wiring contracts from the retired tier-page suite:
    assert 'role="button"' in body and 'aria-label="Inspect' in body   # medallion inspect is a keyboard control
    assert reverse('badge_leaderboards', args=['gow']) in body         # earners tally links to the full board
    assert '/group-badge-peek/0/' in body                             # anon peek points at the showcase endpoint


def test_view_dormant_series_404_for_anon(client):
    series = BadgeSeriesFactory(series_slug='dorm', name='Dormant')
    pg = PlatformGroupFactory(key='ultra-hd', name='Ultra HD', platforms=['PS4', 'PS5'])
    GroupBadgeFactory(series=series, platform_group=pg, is_live=False)   # dormant -> staff-only
    resp = client.get(reverse('badge_detail', kwargs={'series_slug': 'dorm'}))
    assert resp.status_code == 404


# ------------------------------------------------------------------ inspect modal (peek) -----------------

def test_inspect_view_anon_renders_showcase(client):
    series = BadgeSeriesFactory(series_slug='gow', name='God of War')
    _stage(series, 1, ['PS5'])
    gb = _group(series, 'ultra-hd', 'Ultra HD', ['PS4', 'PS5'])
    resp = client.get(reverse('group_badge_quick_peek', kwargs={'group_badge_id': gb.id}))
    assert resp.status_code == 200
    body = resp.content.decode()
    assert 'God of War' in body and 'Ultra HD' in body
    assert 'Showcase' in body                       # anon = full-colour display piece, not owner state
    assert 'data-state="earned"' in body            # showcase forces the full-colour medallion


def test_inspect_view_dormant_group_badge_404(client):
    series = BadgeSeriesFactory(series_slug='d', name='Dormant')
    pg = PlatformGroupFactory(key='ultra-hd', name='Ultra HD', platforms=['PS4', 'PS5'])
    gb = GroupBadgeFactory(series=series, platform_group=pg, is_live=False)   # not live -> not inspectable
    resp = client.get(reverse('group_badge_quick_peek', kwargs={'group_badge_id': gb.id}))
    assert resp.status_code == 404


def test_inspect_progress_peek_requires_auth(client):
    series = BadgeSeriesFactory(series_slug='gow', name='God of War')
    gb = _group(series, 'ultra-hd', 'Ultra HD', ['PS4', 'PS5'])
    profile = ProfileFactory()
    resp = client.get(reverse('group_badge_progress_peek',
                              kwargs={'psn_username': profile.psn_username, 'group_badge_id': gb.id}))
    assert resp.status_code == 404                  # a specific Pursuer's progress is signed-in-only


def test_inspect_progress_peek_reflects_real_state(client):
    series = BadgeSeriesFactory(series_slug='pk', name='Peek')
    s1 = _stage(series, 1, ['PS5'])
    gb = _group(series, 'ultra-hd', 'Ultra HD', ['PS4', 'PS5'])
    profile = ProfileFactory()
    _complete(profile, s1['PS5'], full=True)
    evaluate_and_apply(profile, [gb])               # earns the hold
    client.force_login(profile.user)
    body = client.get(reverse('group_badge_progress_peek',
                              kwargs={'psn_username': profile.psn_username, 'group_badge_id': gb.id})).content.decode()
    assert 'data-state="earned"' in body            # the DISPLAYED profile's real state, not the showcase
    assert 'Showcase' not in body
    assert 'pp-bdetail__viewing' not in body        # own progress -> no "viewing other" chip


def test_inspect_progress_peek_flags_viewing_another(client):
    series = BadgeSeriesFactory(series_slug='vo', name='ViewOther')
    _stage(series, 1, ['PS5'])
    gb = _group(series, 'ultra-hd', 'Ultra HD', ['PS4', 'PS5'])
    other = ProfileFactory()
    viewer = ProfileFactory()
    client.force_login(viewer.user)
    body = client.get(reverse('group_badge_progress_peek',
                              kwargs={'psn_username': other.psn_username, 'group_badge_id': gb.id})).content.decode()
    assert 'pp-bdetail__viewing' in body            # names the DISPLAYED Pursuer, not the viewer


def test_view_authed_uses_progress_peek_and_renders_stats(client):
    # Authed page wiring: the medallion inspect targets the profile-aware peek, and the My Stats modal renders
    # even when the viewer owns NONE of the badge's games (user_stats is a zeros dict, not None -> no crash).
    series = BadgeSeriesFactory(series_slug='aw', name='AuthWire')
    _stage(series, 1, ['PS5'])
    _group(series, 'ultra-hd', 'Ultra HD', ['PS4', 'PS5'])
    profile = ProfileFactory()
    client.force_login(profile.user)
    resp = client.get(reverse('badge_detail', kwargs={'series_slug': 'aw'}))
    assert resp.status_code == 200
    body = resp.content.decode()
    assert '/group-badge-progress-peek/' + profile.psn_username + '/0/' in body
    assert '/group-badge-peek/0/' not in body       # authed uses the profile-aware peek, not the anon showcase
    assert 'id="badge-stats-modal"' in body and 'bd-mystats' in body   # My Stats renders (no owned games -> zeros)
