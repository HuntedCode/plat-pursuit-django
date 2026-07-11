"""Tests for the Frame builder (trophies/services/frame_service.py).

The Frame's first production mounting. These pin the data-derivation contract
(state from viewer progress, tier-name mapping, art layers, progress math) so
the upcoming surfaces — and the future customization layer — build on a stable
base.
"""

import pytest

from trophies.models import UserBadge
from trophies.services.frame_service import build_badge_frame
from tests.factories import (
    BadgeFactory,
    CompanyFactory,
    ProfileFactory,
    UserBadgeFactory,
    UserBadgeProgressFactory,
)

pytestmark = pytest.mark.django_db


def test_anonymous_viewer_gets_showcase_earned():
    badge = BadgeFactory(tier=1, required_stages=10)

    frame = build_badge_frame(badge)  # no profile

    assert frame["state"] == "earned"
    assert frame["tier"] == "bronze"
    assert frame["stages_total"] == 10
    assert frame["art_layers"]  # at least backdrop + main resolved to URLs


def test_tier_int_maps_to_name_and_next_label():
    assert build_badge_frame(BadgeFactory(tier=4))["tier"] == "platinum"
    assert build_badge_frame(BadgeFactory(tier=4))["next_tier_label"] == "Maxed"
    assert build_badge_frame(BadgeFactory(tier=1))["next_tier_label"] == "Silver"


def test_earned_viewer_state_and_date():
    profile = ProfileFactory()
    badge = BadgeFactory(tier=2, required_stages=8)
    UserBadgeFactory(profile=profile, badge=badge)

    frame = build_badge_frame(badge, profile)

    assert frame["state"] == "earned"
    assert frame["stages_done"] == 8
    assert "earned_date" in frame  # formatted string


def test_in_progress_viewer_progress_math():
    profile = ProfileFactory()
    badge = BadgeFactory(tier=1, required_stages=10)
    UserBadgeProgressFactory(profile=profile, badge=badge, completed_concepts=3)

    frame = build_badge_frame(badge, profile)

    assert frame["state"] == "in_progress"
    assert frame["stages_done"] == 3
    assert frame["progress_pct"] == 30


def test_segments_are_one_cell_per_stage_filled_to_progress():
    """The medallion's multi-bar meter reads frame['segments']: one bool per stage, filled to done."""
    profile = ProfileFactory()
    badge = BadgeFactory(tier=1, required_stages=8)
    UserBadgeProgressFactory(profile=profile, badge=badge, completed_concepts=3)

    frame = build_badge_frame(badge, profile)

    assert frame["segments"] == [True, True, True, False, False, False, False, False]


def test_segments_omitted_above_the_cap_so_the_meter_falls_back_to_smooth():
    profile = ProfileFactory()
    badge = BadgeFactory(tier=1, required_stages=13)  # > SEGMENT_CAP (12)
    UserBadgeProgressFactory(profile=profile, badge=badge, completed_concepts=5)

    frame = build_badge_frame(badge, profile)

    assert "segments" not in frame  # template renders the smooth bar off progress_pct instead


def test_earned_badge_segments_are_all_filled():
    profile = ProfileFactory()
    badge = BadgeFactory(tier=1, required_stages=5)
    UserBadgeFactory(profile=profile, badge=badge)

    frame = build_badge_frame(badge, profile)

    assert frame["segments"] == [True] * 5


def test_holographic_flag_is_platinum_tier_only():
    """The chase-card foil is reserved for platinum-tier badges (component still gates on earned)."""
    assert build_badge_frame(BadgeFactory(tier=4))["is_holographic"] is True            # platinum
    assert build_badge_frame(BadgeFactory(tier=3))["is_holographic"] is False           # gold
    assert build_badge_frame(BadgeFactory(tier=1, rarity_pct=1.0))["is_holographic"] is False  # rare but not platinum


def test_unearned_viewer_with_no_progress():
    profile = ProfileFactory()
    badge = BadgeFactory(tier=1, required_stages=10)

    frame = build_badge_frame(badge, profile)

    assert frame["state"] == "unearned"
    assert frame["stages_done"] == 0


# --- showcase mode (the Browse catalog Gallery): full-colour look + a per-viewer owned_state marker ---


def test_showcase_forces_earned_look_on_an_unearned_badge():
    """Showcase presents an unearned badge in full-colour 'earned' glory (no tarnish), but the viewer's
    real state rides along as owned_state so the CARD can mark it -- the medallion itself is unchanged."""
    profile = ProfileFactory()
    badge = BadgeFactory(tier=1, required_stages=10)

    frame = build_badge_frame(badge, profile, showcase=True)

    assert frame["state"] == "earned"            # the medallion shows the badge in full
    assert frame["stages_done"] == 10            # earned look = full meter
    assert frame["owned_state"] == "unearned"    # ... but the card knows you don't have it
    assert "earned_date" not in frame            # no per-viewer earn date on a showcase frame


def test_showcase_earned_viewer_owned_state_is_earned():
    profile = ProfileFactory()
    badge = BadgeFactory(tier=2, required_stages=8)
    UserBadgeFactory(profile=profile, badge=badge)

    frame = build_badge_frame(badge, profile, showcase=True)

    assert frame["state"] == "earned"
    assert frame["owned_state"] == "earned"


def test_showcase_in_progress_stashes_owned_progress_but_shows_full():
    profile = ProfileFactory()
    badge = BadgeFactory(tier=1, required_stages=10)
    UserBadgeProgressFactory(profile=profile, badge=badge, completed_concepts=3)

    frame = build_badge_frame(badge, profile, showcase=True)

    assert frame["state"] == "earned"                 # medallion in full colour
    assert frame["segments"] == [True] * 10           # earned look, not a 3/10 meter
    assert "progress_pct" not in frame                # the showcase medallion carries no progress bar
    assert frame["owned_state"] == "in_progress"      # ... the card shows you're chasing it
    assert frame["owned_progress_pct"] == 30
    assert frame["owned_stages_done"] == 3            # the REAL completed count (for the "3 / 10 stages" card stat)


def test_showcase_maintenance_owned_state():
    profile = ProfileFactory()
    badge = BadgeFactory(tier=1, required_stages=5)
    ub = UserBadgeFactory(profile=profile, badge=badge)
    UserBadge.objects.filter(pk=ub.pk).update(status='maintenance')
    UserBadgeProgressFactory(profile=profile, badge=badge, completed_concepts=2)  # partial repair

    frame = build_badge_frame(badge, profile, showcase=True)

    assert frame["state"] == "earned"
    assert frame["owned_state"] == "maintenance"
    assert frame["owned_stages_done"] == 2   # repair progress drives the card's "2 / 5 stages" stat


def test_showcase_anonymous_has_no_owned_state():
    """Anonymous viewers get the pure catalog look -- earned presentation, no ownership marker at all."""
    badge = BadgeFactory(tier=4, required_stages=10)

    frame = build_badge_frame(badge, showcase=True)  # no profile

    assert frame["state"] == "earned"
    assert "owned_state" not in frame


def test_rarity_set_number_and_type_in_frame():
    badge = BadgeFactory(
        tier=1, badge_type='series', set_number=42,
        rarity_pct=3.5, rarity_rank=7, rarity_class='rare',
    )

    frame = build_badge_frame(badge)

    assert frame["set_number"] == 42
    assert frame["rarity_pct"] == 3.5
    assert frame["rarity_rank"] == 7
    assert frame["rarity_class"] == 'rare'
    assert frame["badge_type"]  # human display label


def test_earned_count_and_funder_in_frame():
    funder = ProfileFactory(display_psn_username='HeroHunter')
    badge = BadgeFactory(tier=1, earned_count=1234)
    badge.funded_by = funder
    badge.save()

    frame = build_badge_frame(badge)

    assert frame["earned_count"] == 1234
    assert frame["funded_by"] == 'HeroHunter'


def test_funder_credit_falls_back_to_psn_username():
    # A real donor whose display name is unset must still get credited
    # (psn_username is normalized to lowercase, which is fine for a credit).
    funder = ProfileFactory(psn_username='rawhandle', display_psn_username='')
    badge = BadgeFactory(tier=1)
    badge.funded_by = funder
    badge.save()

    frame = build_badge_frame(badge)

    assert frame["funded_by"] == 'rawhandle'


def test_effective_franchise_collection_and_developer_in_frame():
    from trophies.models import Franchise
    fr = Franchise.objects.create(igdb_id=1, name='Halo', slug='halo', source_type='franchise')
    coll = Franchise.objects.create(igdb_id=2, name='Bungie Classics', slug='bungie-classics', source_type='collection')
    dev = CompanyFactory(name='Bungie')
    badge = BadgeFactory(tier=1)
    badge.franchise = fr
    badge.collection = coll
    badge.developer = dev
    badge.save()

    frame = build_badge_frame(badge)

    # All three subject keys populate; the front face picks one by precedence
    # (franchise > collection > developer) in the template.
    assert frame["franchise"] == 'Halo'
    assert frame["collection"] == 'Bungie Classics'
    assert frame["developer"] == 'Bungie'


def test_earn_rank_becomes_engraving_and_series_xp_when_earned():
    from trophies.models import UserBadge
    profile = ProfileFactory()
    badge = BadgeFactory(tier=1, required_stages=5)
    ub = UserBadgeFactory(profile=profile, badge=badge)
    UserBadge.objects.filter(pk=ub.pk).update(earn_rank=12)

    frame = build_badge_frame(badge, profile)

    assert frame["engraving_rank"] == 12
    assert frame["series_xp"] > 0  # earned -> completion bonus counts


def test_maintenance_badge_renders_maintenance_state():
    from trophies.models import UserBadge
    profile = ProfileFactory()
    badge = BadgeFactory(tier=1, required_stages=5)
    ub = UserBadgeFactory(profile=profile, badge=badge)
    UserBadge.objects.filter(pk=ub.pk).update(status='maintenance', earn_rank=3)

    frame = build_badge_frame(badge, profile)

    assert frame["state"] == "maintenance"  # not 'earned'
    assert frame["engraving_rank"] == 3     # permanent, survives the lapse


def test_current_rank_uses_earners_leaderboard(monkeypatch):
    profile = ProfileFactory()
    badge = BadgeFactory(tier=1)
    UserBadgeFactory(profile=profile, badge=badge)
    monkeypatch.setattr(
        'trophies.services.redis_leaderboard_service.get_earners_rank',
        lambda slug, pid: 7,
    )

    frame = build_badge_frame(badge, profile)

    assert frame["current_rank"] == 7  # used as-is (already 1-indexed)


def test_current_rank_omitted_when_not_on_leaderboard(monkeypatch):
    profile = ProfileFactory()
    badge = BadgeFactory(tier=1)
    UserBadgeFactory(profile=profile, badge=badge)
    monkeypatch.setattr(
        'trophies.services.redis_leaderboard_service.get_earners_rank',
        lambda slug, pid: None,
    )

    frame = build_badge_frame(badge, profile)

    assert "current_rank" not in frame


def test_in_progress_with_zero_required_stages_no_divide_by_zero():
    # required_stages=0 must not raise ZeroDivisionError in the progress math.
    profile = ProfileFactory()
    badge = BadgeFactory(tier=1, required_stages=0)
    UserBadgeProgressFactory(profile=profile, badge=badge, completed_concepts=2)

    frame = build_badge_frame(badge, profile)

    assert frame["state"] == "in_progress"
    assert frame["progress_pct"] == 0  # guarded fallback, not an exception


# --- Batch path (galleries / the collection album: many frames, one profile) -------
# The caller pre-fetches the viewer's UserBadge/UserBadgeProgress + live stats ONCE and
# passes them in, so the builder issues no per-badge queries/Redis. These pin that the
# pre-fetched values win over (and skip) the per-badge lookups.


def test_prefetched_earned_none_skips_userbadge_query():
    """earned=None means 'pre-fetched, viewer does NOT hold it' -- the builder must trust
    that and render unearned even though a UserBadge exists in the DB (which the single-hero
    path would have found)."""
    profile = ProfileFactory()
    badge = BadgeFactory(tier=1, required_stages=5)
    UserBadgeFactory(profile=profile, badge=badge)  # present in DB, must be ignored

    frame = build_badge_frame(badge, profile, earned=None, progress=None)

    assert frame["state"] == "unearned"  # trusted the passed-in None, did not query


def test_prefetched_progress_used_without_db_row():
    """progress=<obj> is used verbatim; no UserBadgeProgress row exists in the DB, proving
    the value came from the caller, not a query."""
    from trophies.models import UserBadgeProgress

    profile = ProfileFactory()
    badge = BadgeFactory(tier=1, required_stages=10)
    detached = UserBadgeProgress(profile=profile, badge=badge, completed_concepts=4)  # unsaved

    frame = build_badge_frame(badge, profile, earned=None, progress=detached)

    assert frame["state"] == "in_progress"
    assert frame["stages_done"] == 4
    assert frame["progress_pct"] == 40
    assert not UserBadgeProgress.objects.filter(profile=profile, badge=badge).exists()


def test_batched_live_stats_win_and_skip_redis_and_xp(monkeypatch):
    """For an earned badge, pre-fetched current_rank/series_xp are applied as-is and the
    per-badge Redis (get_earners_rank) + XP (calculate_series_xp) fan-out is skipped -- the
    whale-safety contract for rendering hundreds of frames."""
    profile = ProfileFactory()
    badge = BadgeFactory(tier=1, required_stages=5)
    ub = UserBadgeFactory(profile=profile, badge=badge)

    def _boom(*a, **k):
        raise AssertionError("per-badge live-stat fan-out must not run in the batch path")

    monkeypatch.setattr('trophies.services.redis_leaderboard_service.get_earners_rank', _boom)
    monkeypatch.setattr('trophies.services.xp_service.calculate_series_xp', _boom)

    frame = build_badge_frame(
        badge, profile, earned=ub, progress=None,
        include_live_stats=False, current_rank=9, series_xp=4321,
    )

    assert frame["state"] == "earned"
    assert frame["current_rank"] == 9
    assert frame["series_xp"] == 4321


def test_batched_live_stats_ignored_for_unearned_badge(monkeypatch):
    """current_rank/series_xp only mean anything for an earned badge; an unearned frame must
    not carry a current_rank (back-of-card live stats are an earned-only concept)."""
    profile = ProfileFactory()
    badge = BadgeFactory(tier=1, required_stages=5)
    monkeypatch.setattr(
        'trophies.services.redis_leaderboard_service.get_earners_rank',
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not query")),
    )

    frame = build_badge_frame(
        badge, profile, earned=None, progress=None,
        include_live_stats=False, current_rank=9, series_xp=4321,
    )

    assert frame["state"] == "unearned"
    assert "current_rank" not in frame
    assert "series_xp" not in frame  # both live stats are earned-only


def test_prefetched_current_rank_wins_over_live_value(monkeypatch):
    """The docstring's claim: a pre-fetched current_rank takes precedence over the live
    lookup even when include_live_stats is on. The live path returns a DIFFERENT number, so
    seeing the pre-fetched one proves precedence (not just that one path ran)."""
    profile = ProfileFactory()
    badge = BadgeFactory(tier=1, required_stages=5)
    UserBadgeFactory(profile=profile, badge=badge)
    monkeypatch.setattr(
        'trophies.services.redis_leaderboard_service.get_earners_rank',
        lambda *a, **k: 99,  # the live value, which must be overridden
    )

    # earned left to query (finds the UserBadge -> earned); only the rank is pre-fetched.
    frame = build_badge_frame(badge, profile, include_live_stats=True, current_rank=4)

    assert frame["current_rank"] == 4  # pre-fetched wins over the live 99


def test_earned_batch_without_prefetched_stats_omits_them(monkeypatch):
    """The common collection path for an earned badge when the rank/XP maps have no entry:
    include_live_stats=False and NO pre-fetched stats -> neither the live fan-out runs nor
    are the back-of-card keys present. Guards the _UNSET-vs-None asymmetry."""
    profile = ProfileFactory()
    badge = BadgeFactory(tier=1, required_stages=5)
    ub = UserBadgeFactory(profile=profile, badge=badge)

    def _boom(*a, **k):
        raise AssertionError("no live fan-out when include_live_stats is off")

    monkeypatch.setattr('trophies.services.redis_leaderboard_service.get_earners_rank', _boom)
    monkeypatch.setattr('trophies.services.xp_service.calculate_series_xp', _boom)

    frame = build_badge_frame(badge, profile, earned=ub, progress=None, include_live_stats=False)

    assert frame["state"] == "earned"
    assert "current_rank" not in frame
    assert "series_xp" not in frame
