"""The mobile API is gone (2026-08).

Fifteen endpoints under `/api/v1/auth/` and `/api/v1/mobile/` plus the `DeviceToken` push-registration
table were a backend-only foundation for a React Native client that was never started. The badge half was
tier-shaped and could not survive the badge cutover; the rest went with it, because whenever a mobile
client is actually built it will be a full rebuild and the API should be designed against that client.

What these tests defend is not the deletion itself but the two things a partial teardown leaves behind:

  - a route that resolves to a view that no longer exists (a 500, not a 404)
  - DRF token auth removed as "mobile scaffolding" -- it is not. PlatBot authorises on it, and taking it
    out would silently unauthenticate every bot endpoint.

See docs/guides/mobile-app.md.
"""
import ast
from pathlib import Path

import pytest
from django.conf import settings
from django.urls import NoReverseMatch, reverse

ROOT = Path(__file__).resolve().parents[2]

DELETED_MODULES = [
    'api/mobile_auth_urls.py',
    'api/mobile_auth_views.py',
    'api/mobile_badge_views.py',
    'api/mobile_game_views.py',
    'api/mobile_profile_views.py',
    'api/mobile_psn_views.py',
    'api/mobile_sync_views.py',
    'api/device_token_views.py',
]

DELETED_ROUTE_NAMES = [
    'mobile-login', 'mobile-signup', 'mobile-logout', 'mobile-password-reset',
    'mobile-my-profile', 'mobile-profile',
    'mobile-psn-generate-code', 'mobile-psn-verify', 'mobile-psn-status',
    'mobile-sync-status', 'mobile-sync-trigger',
    'mobile-badge-list', 'mobile-badge-series-detail', 'mobile-user-badges', 'mobile-profile-badges',
    'mobile-profile-games', 'mobile-game-trophies',
    'device-token-register', 'device-token-delete',
]


@pytest.mark.parametrize('rel', DELETED_MODULES)
def test_the_view_modules_are_deleted(rel):
    assert not (ROOT / rel).exists(), f'{rel} still exists'


@pytest.mark.parametrize('name', DELETED_ROUTE_NAMES)
def test_the_routes_are_unreachable(name):
    """Both namespaced and bare, since the mobile auth routes were included without the `api:` prefix."""
    for candidate in (f'api:{name}', name):
        with pytest.raises(NoReverseMatch):
            reverse(candidate)


@pytest.mark.django_db      # the 404 page renders real chrome, which reads the DB
@pytest.mark.parametrize('path', [
    '/api/v1/auth/login/',
    '/api/v1/mobile/me/',
    '/api/v1/mobile/sync/status/',
    '/api/v1/device-tokens/',
])
def test_the_paths_404_rather_than_500(client, path):
    """A 404 proves the route is gone. A 500 would mean a route survived its view -- the failure mode a
    reverse() check alone cannot see, because an unnamed leftover path still resolves."""
    assert client.get(path).status_code == 404


def test_the_device_token_model_is_gone():
    from django.apps import apps
    with pytest.raises(LookupError):
        apps.get_model('notifications', 'DeviceToken')


def test_bot_token_auth_survived_the_teardown():
    """The load-bearing exception. `rest_framework.authtoken` and `TokenAuthentication` read as mobile
    scaffolding but PlatBot depends on them: `IsDiscordBot` authorises by matching the DRF token key
    against BOT_API_KEY. Removing them would leave every bot endpoint rejecting the bot."""
    assert 'rest_framework.authtoken' in settings.INSTALLED_APPS
    assert (
        'rest_framework.authentication.TokenAuthentication'
        in settings.REST_FRAMEWORK['DEFAULT_AUTHENTICATION_CLASSES']
    )
    from api.permissions import IsDiscordBot          # imports cleanly, still referenced by the bot views
    assert IsDiscordBot is not None


def test_the_bot_endpoints_are_still_routed():
    """The other half of the above: the endpoints themselves must have survived a teardown that removed
    everything around them."""
    for name in ('verify', 'sync-roles', 'recheck-badges', 'refresh'):
        assert reverse(f'api:{name}')


def test_api_urls_declares_no_mobile_route():
    """A text-level sweep of the url conf, which catches a leftover path whose NAME differs from the list
    above (or that has no name at all, and so is invisible to reverse())."""
    source = (ROOT / 'api/urls.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    routes = [
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, 'id', None) in {'path', 're_path'}
        and node.args and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    ]
    offenders = [r for r in routes if r.startswith(('mobile/', 'auth/', 'device-tokens/'))]
    assert not offenders, f'api/urls.py still routes {offenders}'
