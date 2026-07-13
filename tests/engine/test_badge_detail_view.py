"""View-level tests for the badge detail page (BadgeDetailView context).

These guard the data contract the (rebuilding) page templates consume, so the
contract survives the template rebuild. First guard: the tier-tab "earned"
marking. Tiers are INDEPENDENT (a higher tier can be held without a lower one,
see test_badge_eval.test_tiers_are_independent_no_prerequisite), so the tabs
must mark each tier by earned-set membership, NOT by `highest_tier_earned >=
tier` (which would falsely check every lower tier).
"""
import pytest
from django.urls import reverse

from tests.factories import (
    BadgeFactory,
    ConceptFactory,
    GameFactory,
    ProfileFactory,
    ProfileGameFactory,
    StageFactory,
    UserBadgeFactory,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def stub_leaderboards(monkeypatch):
    """Make the Redis-backed leaderboard calls deterministic + Redis-free.

    The badge detail render calls into the leaderboard service; these tests are
    about the tier/earned context, not ranks, so stub them out.
    """
    monkeypatch.setattr('trophies.views.badge_views.get_earners_rank', lambda *a, **k: None)
    monkeypatch.setattr('trophies.views.badge_views.get_progress_rank', lambda *a, **k: None)
    monkeypatch.setattr('trophies.views.badge_views.get_community_xp', lambda *a, **k: 0)


def _series_with_stage(series, stage_number=1):
    """One stage (applies to all tiers) holding a concept with one game."""
    concept = ConceptFactory()
    GameFactory(concept=concept)
    stage = StageFactory(series_slug=series, stage_number=stage_number, required_tiers=[])
    stage.concepts.add(concept)
    return stage


def test_tier_tabs_mark_by_earned_set_not_max(client, stub_leaderboards):
    series = "rebuild-tier-tabs"
    BadgeFactory(series_slug=series, tier=1, is_live=True)
    tier2 = BadgeFactory(series_slug=series, tier=2, is_live=True)
    BadgeFactory(series_slug=series, tier=3, is_live=True)
    _series_with_stage(series, 1)

    profile = ProfileFactory()
    # Independent tiers: hold ONLY tier 2 (tier 1 and 3 unearned).
    UserBadgeFactory(profile=profile, badge=tier2)

    client.force_login(profile.user)
    resp = client.get(reverse('badge_detail', kwargs={'series_slug': series}))

    assert resp.status_code == 200
    # The contract the tier tabs consume: the actual earned SET.
    assert resp.context['earned_tiers'] == {2}
    # The trap this guards: max-based marking would light tier 1 up too.
    assert resp.context['highest_tier_earned'] == 2
    assert 1 not in resp.context['earned_tiers']
    assert 3 not in resp.context['earned_tiers']


def _series_with_games(series, n_games=2, stage_number=1):
    """One stage (applies to all tiers) whose concept holds n_games games."""
    concept = ConceptFactory()
    games = [GameFactory(concept=concept) for _ in range(n_games)]
    stage = StageFactory(series_slug=series, stage_number=stage_number, required_tiers=[])
    stage.concepts.add(concept)
    return stage, games


def test_series_stats_contract(client, stub_leaderboards):
    # Pins the series-stats numbers the (rebuilding) stats section renders, so the
    # contract survives the template rebuild.
    series = "rebuild-series-stats"
    BadgeFactory(series_slug=series, tier=1, is_live=True)
    BadgeFactory(series_slug=series, tier=2, is_live=True)
    _, games = _series_with_games(series, 2)

    profile = ProfileFactory()
    ProfileGameFactory(profile=profile, game=games[0], progress=100, has_plat=True)
    ProfileGameFactory(profile=profile, game=games[1], progress=50, has_plat=False)

    client.force_login(profile.user)
    resp = client.get(reverse('badge_detail', kwargs={'series_slug': series}))

    assert resp.status_code == 200
    stats = resp.context['badge_series_stats']
    assert stats['total_games'] == 2
    assert stats['avg_progress'] == 75.0            # (100 + 50) / 2
    assert stats['total_required_stages'] == 1
    assert stats['user_stages_played'] == 1         # one stage, games played
    assert stats['user_stages_platinumed'] == 1     # games[0] platted


def test_leaderboard_links_point_to_full_board(client, stub_leaderboards):
    # The "who owns this" path: the leaderboard section CTA links to the full board.
    # (The header's "Earners" stat also linked here for a 2nd occurrence, but the header
    # is currently removed pending a from-scratch rebuild -- bump back to >= 2 when it
    # returns with an Earners link.)
    series = "rebuild-lb-links"
    BadgeFactory(series_slug=series, tier=1, is_live=True)
    _series_with_stage(series, 1)

    profile = ProfileFactory()
    client.force_login(profile.user)
    resp = client.get(reverse('badge_detail', kwargs={'series_slug': series}))

    assert resp.status_code == 200
    lb_url = reverse('badge_leaderboards', args=[series])
    assert resp.content.decode().count(lb_url) >= 1  # leaderboard section CTA


def test_stage_with_unobtainable_game_renders(client, stub_leaderboards):
    # Exercises the unobtainable/delisted collapsible branch of the stage section.
    series = "rebuild-unobt"
    BadgeFactory(series_slug=series, tier=1, is_live=True)
    concept = ConceptFactory()
    GameFactory(concept=concept)                       # obtainable
    GameFactory(concept=concept, is_obtainable=False)  # unobtainable
    stage = StageFactory(series_slug=series, stage_number=1, required_tiers=[])
    stage.concepts.add(concept)

    profile = ProfileFactory()
    client.force_login(profile.user)
    resp = client.get(reverse('badge_detail', kwargs={'series_slug': series}))

    assert resp.status_code == 200
    assert 'Unobtainable/Delisted' in resp.content.decode()


def test_anonymous_viewer_has_empty_earned_tiers(client, stub_leaderboards):
    series = "rebuild-tier-tabs-anon"
    BadgeFactory(series_slug=series, tier=1, is_live=True)
    BadgeFactory(series_slug=series, tier=2, is_live=True)
    _series_with_stage(series, 1)

    resp = client.get(reverse('badge_detail', kwargs={'series_slug': series}))

    assert resp.status_code == 200
    assert resp.context['earned_tiers'] == set()
    assert resp.context['selected_tier'] == 1  # anonymous defaults to tier 1
