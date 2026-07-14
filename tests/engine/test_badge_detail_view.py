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
    # A cleanly-earned tier keeps the earned check, never the maintenance "M".
    assert resp.context['maint_tiers'] == set()
    assert 'is-maintenance' not in resp.content.decode()


def test_tier_switch_returns_island_partial_for_htmx(client, stub_leaderboards):
    """A tier switch is an HTMX swap of #badge-tier-view: the view returns just the tier-view island partial
    (header + selector + stages), NOT the full page, so switching tiers updates in place."""
    series = "rebuild-tier-swap"
    BadgeFactory(series_slug=series, tier=1, is_live=True)
    BadgeFactory(series_slug=series, tier=2, is_live=True)
    _series_with_stage(series, 1)

    url = reverse('badge_detail', kwargs={'series_slug': series}) + '?tier=2'
    resp = client.get(url, HTTP_HX_REQUEST='true', HTTP_HX_TARGET='badge-tier-view')

    assert resp.status_code == 200
    html = resp.content.decode()
    assert 'bd-tierlad' in html             # the tier ladder is in the partial
    assert 'badge-header-card' in html      # the header rides along in the island
    assert '<!doctype' not in html.lower()  # NOT the full page (no base.html chrome)
    assert 'id="badge-tier-view"' not in html   # the partial is the ISLAND INNER, not the wrapper


def test_tier_switch_full_page_without_htmx(client, stub_leaderboards):
    """Without HTMX the same URL returns the full page: base.html + the #badge-tier-view island wrapper."""
    series = "rebuild-tier-full"
    BadgeFactory(series_slug=series, tier=1, is_live=True)
    BadgeFactory(series_slug=series, tier=2, is_live=True)
    _series_with_stage(series, 1)

    resp = client.get(reverse('badge_detail', kwargs={'series_slug': series}) + '?tier=2')

    assert resp.status_code == 200
    html = resp.content.decode()
    assert 'id="badge-tier-view"' in html   # full page has the island wrapper
    assert 'bd-tierlad' in html


def test_hero_medallion_inspect_is_profile_aware(client, stub_leaderboards):
    """The hero medallion inspect fetches the PROFILE-AWARE peek keyed to the DISPLAYED profile (so the
    medallion shows their real earned state + personalised base), and the generic showcase peek for anon."""
    series = "rebuild-peek-aware"
    BadgeFactory(series_slug=series, tier=1, is_live=True)
    _series_with_stage(series, 1)
    url = reverse('badge_detail', kwargs={'series_slug': series})

    # Anonymous -> generic showcase quick-peek.
    anon = client.get(url).content.decode()
    assert 'id="badge-peek"' in anon
    assert 'data-badge-id' in anon
    assert '/badge-peek/0/' in anon
    assert '/badge-progress-peek/' not in anon

    # Signed-in -> the profile-aware peek keyed to the displayed (own) profile.
    profile = ProfileFactory()
    client.force_login(profile.user)
    authed = client.get(url).content.decode()
    assert '/badge-progress-peek/' + profile.psn_username + '/0/' in authed
    assert '/badge-peek/0/' not in authed


def test_viewing_another_profile_flags_it(client, stub_leaderboards):
    """The /<slug>/<username>/ variant shows an unmistakable 'viewing X's progress' header banner AND points
    the inspect at THAT profile's peek -- not the viewer's own."""
    series = "rebuild-peek-variant"
    BadgeFactory(series_slug=series, tier=1, is_live=True)
    _series_with_stage(series, 1)

    other = ProfileFactory()
    viewer = ProfileFactory()
    client.force_login(viewer.user)

    # The /<slug>/<username>/ path is Cloudflare-guarded; simulate the CF-Ray header so the guard middleware
    # doesn't 302 it back to the proxy origin (see plat_pursuit.middleware).
    resp = client.get(reverse('badge_detail_with_profile',
                              kwargs={'series_slug': series, 'psn_username': other.psn_username}),
                      HTTP_CF_RAY="test-ray")

    assert resp.status_code == 200
    html = resp.content.decode()
    assert 'bdh-viewing' in html                     # the header "not your own" banner
    assert other.psn_username in html                # names the displayed Pursuer
    # Inspect targets the DISPLAYED profile's peek, not the viewer's.
    assert '/badge-progress-peek/' + other.psn_username + '/0/' in html
    assert '/badge-progress-peek/' + viewer.psn_username + '/0/' not in html


def test_progress_peek_endpoint_auth_and_404s(client, stub_leaderboards):
    """The profile-aware peek endpoint: anonymous -> 404 (a Pursuer's progress is signed-in only); unknown
    profile / non-live badge -> 404."""
    profile = ProfileFactory()
    live = BadgeFactory(series_slug="peek-live", tier=1, is_live=True)
    dead = BadgeFactory(series_slug="peek-dead", tier=1, is_live=False)

    def peek_url(username, badge_id):
        return reverse('badge_progress_peek', kwargs={'psn_username': username, 'badge_id': badge_id})

    # Anonymous -> 404 (auth gate).
    assert client.get(peek_url(profile.psn_username, live.id)).status_code == 404

    client.force_login(profile.user)
    assert client.get(peek_url('nobody-here', live.id)).status_code == 404   # unknown profile
    assert client.get(peek_url(profile.psn_username, dead.id)).status_code == 404   # non-live badge


def test_progress_peek_endpoint_reflects_real_state(client, stub_leaderboards):
    """The peek renders the profile's REAL state -- an earned badge shows 'earned' (with owner engraving),
    an unearned one shows 'unearned' -- not the always-earned showcase; and it flags another Pursuer's."""
    earned = BadgeFactory(series_slug="peek-e", tier=1, is_live=True)
    unearned = BadgeFactory(series_slug="peek-u", tier=1, is_live=True)
    profile = ProfileFactory()
    UserBadgeFactory(profile=profile, badge=earned)
    client.force_login(profile.user)

    def peek(username, badge_id):
        return client.get(reverse('badge_progress_peek',
                                  kwargs={'psn_username': username, 'badge_id': badge_id})).content.decode()

    own_earned = peek(profile.psn_username, earned.id)
    own_unearned = peek(profile.psn_username, unearned.id)
    assert 'data-state="earned"' in own_earned        # real earned state
    assert 'data-state="unearned"' in own_unearned     # real unearned state (not showcase)
    assert 'pp-bdetail__viewing' not in own_earned      # own progress -> no "viewing other" chip

    # Viewing ANOTHER Pursuer -> the "X's progress" chip is set.
    other = ProfileFactory()
    other_html = peek(other.psn_username, earned.id)
    assert 'pp-bdetail__viewing' in other_html
    assert other.psn_username in other_html


def test_header_maintenance_state(client, stub_leaderboards):
    """A lapsed (maintenance) hero badge renders the maintenance treatment ('lapsed'), not the 'not started'
    branch it used to fall into."""
    series = "peek-maint"
    badge = BadgeFactory(series_slug=series, tier=1, is_live=True, required_stages=3)
    _series_with_stage(series, 1)
    profile = ProfileFactory()
    UserBadgeFactory(profile=profile, badge=badge, status='maintenance')   # held but lapsed
    client.force_login(profile.user)

    resp = client.get(reverse('badge_detail', kwargs={'series_slug': series}))

    assert resp.status_code == 200
    html = resp.content.decode()
    assert 'lapsed' in html            # the maintenance branch (wrench + "lapsed")
    assert 'not started' not in html   # NOT the unearned branch


def test_maintenance_defaults_to_lowest_lapsed_tier(client, stub_leaderboards):
    """A series with lapsed low tiers must open on the LOWEST maintenance tier (the one to re-earn), not
    skip to the next clean win. Bronze+Silver lapsed, Gold+Plat unearned -> lands on Bronze (tier 1), and
    the ladder marks those rungs as maintenance ('M'), not a clean earned check."""
    series = "maint-default"
    b1 = BadgeFactory(series_slug=series, tier=1, is_live=True, required_stages=2)
    b2 = BadgeFactory(series_slug=series, tier=2, is_live=True, required_stages=2)
    BadgeFactory(series_slug=series, tier=3, is_live=True, required_stages=2)
    BadgeFactory(series_slug=series, tier=4, is_live=True, required_stages=2)
    _series_with_stage(series, 1)
    profile = ProfileFactory()
    UserBadgeFactory(profile=profile, badge=b1, status='maintenance')
    UserBadgeFactory(profile=profile, badge=b2, status='maintenance')
    client.force_login(profile.user)

    resp = client.get(reverse('badge_detail', kwargs={'series_slug': series}))

    assert resp.status_code == 200
    assert resp.context['selected_tier'] == 1              # opens on lapsed Bronze, NOT Gold (the next win)
    assert resp.context['maint_tiers'] == {1, 2}
    html = resp.content.decode()
    assert 'bd-tierstep--bronze is-active is-maintenance' in html   # Bronze (the open rung) shows maintenance
    assert 'bd-tierstep--silver is-maintenance' in html             # ... and Silver, not a clean earned check
    # Gold/Platinum are genuinely unearned -> exactly the two lapsed rungs carry the maintenance mark.
    assert html.count('is-maintenance') == 2


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
    # The "who owns this" path: both the header's "Earners" stat and the leaderboard section CTA link to
    # the full leaderboards page.
    series = "rebuild-lb-links"
    BadgeFactory(series_slug=series, tier=1, is_live=True)
    _series_with_stage(series, 1)

    profile = ProfileFactory()
    client.force_login(profile.user)
    resp = client.get(reverse('badge_detail', kwargs={'series_slug': series}))

    assert resp.status_code == 200
    lb_url = reverse('badge_leaderboards', args=[series])
    # Header Earners stat + leaderboard section CTA both target the full board.
    assert resp.content.decode().count(lb_url) >= 2


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
