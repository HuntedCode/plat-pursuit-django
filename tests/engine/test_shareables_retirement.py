"""My Shareables is plat-cards-only (2026-08).

Two surfaces were retired (Platinum Grid, Profile Card) and one duplicate entry point was closed (the
Game Detail hero's Share Card button), so that a plat card is obtainable from exactly ONE place. These
pin all three, plus the paid-perk cleanup that has to travel with a retirement.
"""
import pytest

from core.hub_subnav import _URL_NAME_TO_SLUG_OVERRIDES
from tests.factories import GameFactory, ProfileFactory

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize('path', ['/shareables/platinum-grid/', '/shareables/profile-card/'])
def test_retired_surfaces_bounce_to_the_shareables_landing(client, path):
    profile = ProfileFactory(is_linked=True)
    client.force_login(profile.user)

    resp = client.get(path)

    assert resp.status_code == 302 and resp['Location'] == '/shareables/'


@pytest.mark.parametrize('path', ['/shareables/platinum-grid/', '/shareables/profile-card/'])
def test_the_bounce_is_temporary(client, path):
    """302, not 301: both views are PARKED for a revival under the new card design, and a browser-cached
    permanent redirect would strand users if they come back."""
    from django.urls import resolve

    assert resolve(path).func.view_initkwargs['permanent'] is False


@pytest.mark.parametrize('legacy', ['/staff/platinum-grid/', '/tools/platinum-grid/'])
def test_legacy_grid_paths_funnel_into_the_bounce(client, legacy):
    profile = ProfileFactory(is_linked=True)
    client.force_login(profile.user)

    assert client.get(legacy, follow=True).redirect_chain[-1] == ('/shareables/', 302)


def test_retired_pages_are_off_the_subnav():
    assert 'platinum_grid' not in _URL_NAME_TO_SLUG_OVERRIDES
    assert 'my_shareables_profile_card' not in _URL_NAME_TO_SLUG_OVERRIDES


def test_game_detail_no_longer_offers_a_share_card(client):
    """A plat card comes from My Shareables and nowhere else. Removing the button also drops
    share-image.js, shareable-manager.js, color-grid-modal.js and an inlined GRADIENT_THEMES blob
    off this page."""
    from tests.factories import EarnedTrophyFactory, ProfileGameFactory, TrophyFactory

    game = GameFactory(defined_trophies={'bronze': 5, 'platinum': 1})
    profile = ProfileFactory(is_linked=True)
    # The button lived behind `if not has_platinum: return {}` -- without an EARNED platinum on this
    # game the assertions below pass against the pre-change code too, which is exactly how a
    # retirement test goes quietly vacuous.
    trophy = TrophyFactory(game=game, trophy_type='platinum', trophy_group_id='default')
    EarnedTrophyFactory(profile=profile, trophy=trophy, earned=True)
    ProfileGameFactory(profile=profile, game=game, progress=100, has_plat=True)
    client.force_login(profile.user)

    resp = client.get(f'/games/{game.np_communication_id}/')

    assert resp.status_code == 200, 'a 404/500 page would satisfy every "not in" below'
    content = resp.content.decode()
    assert 'share-card-btn' not in content
    assert 'shareable-manager.js' not in content
    assert 'GRADIENT_THEMES' not in content


def test_dashboard_registry_drops_the_retired_share_modules():
    """The modules fed the two retired pages and the duplicate plat-card surface; leaving them would
    also leave providers pointed at a share pipeline that no longer exists."""
    from trophies.services.dashboard_service import DASHBOARD_MODULES

    slugs = {m['slug'] for m in DASHBOARD_MODULES}
    assert not slugs & {'profile_card_preview', 'recent_platinum_card', 'platinum_grid_cta'}


def test_platinum_grid_is_no_longer_sold_as_a_perk(client):
    """A paid perk must not advertise a page that bounces the buyer."""
    profile = ProfileFactory(is_linked=True)
    client.force_login(profile.user)

    for path in ('/users/subscribe/', '/users/subscription-management/'):
        resp = client.get(path, follow=True)
        # Assert the 200 too, so an auth/redirect change can't quietly make this test vacuous.
        assert resp.status_code == 200, path
        assert 'Platinum Grid' not in resp.content.decode(), path
