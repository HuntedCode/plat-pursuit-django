"""BetaStaffGateMiddleware: the staff-only lock for the beta/staging deployment.

Pins the gate's behaviour so a future settings/middleware change can't silently
open beta to the public or lock staff out. Everything is driven by the IS_BETA
flag; with it off the gate is inert (prod behaviour).
"""
import pytest

from tests.factories import ProfileFactory

pytestmark = pytest.mark.django_db


def test_gate_inert_when_not_beta(client, settings):
    # Production: IS_BETA off -> a public page is reachable by anyone, no gate.
    settings.IS_BETA = False
    assert client.get('/support/').status_code == 200


def test_gate_redirects_anonymous_to_login(client, settings):
    settings.IS_BETA = True
    resp = client.get('/support/')
    assert resp.status_code == 302
    assert '/accounts/login/' in resp['Location']
    assert 'next=/support/' in resp['Location']   # bounces back after sign-in


def test_gate_forbids_authenticated_non_staff(client, settings):
    settings.IS_BETA = True
    profile = ProfileFactory(is_linked=True)
    assert profile.user.is_staff is False
    client.force_login(profile.user)
    assert client.get('/support/').status_code == 403


def test_gate_allows_staff_and_stamps_noindex(client, settings):
    settings.IS_BETA = True
    profile = ProfileFactory(is_linked=True)
    profile.user.is_staff = True
    profile.user.save(update_fields=['is_staff'])
    client.force_login(profile.user)
    resp = client.get('/support/')
    assert resp.status_code == 200
    assert resp['X-Robots-Tag'] == 'noindex, nofollow'


def test_gate_keeps_login_flow_open_for_anon(client, settings):
    # /accounts/ must stay reachable or staff could never sign in to pass the gate.
    settings.IS_BETA = True
    assert client.get('/accounts/login/').status_code == 200


def test_gate_answers_healthz_for_anon(client, settings):
    # Render's health check is anonymous; the probe must return 200 through the gate.
    settings.IS_BETA = True
    resp = client.get('/healthz/')
    assert resp.status_code == 200
    assert resp.content == b'ok'


# --- Cloudflare origin guard must NOT bounce beta detail pages to prod ---

def test_cf_origin_guard_skipped_on_beta(settings, rf):
    # Beta isn't behind Cloudflare, so a guarded detail path lacks CF-Ray. Without
    # the IS_BETA skip the guard would 302 it to https://platpursuit.com (prod).
    from plat_pursuit.middleware import CloudflareOriginGuardMiddleware
    settings.DEBUG = False
    settings.IS_BETA = True
    sentinel = object()
    mw = CloudflareOriginGuardMiddleware(lambda req: sentinel)
    assert mw(rf.get('/games/some-slug/some-user/')) is sentinel   # passes through


def test_cf_origin_guard_still_active_in_prod(settings, rf):
    # Guard rail on the guard: with beta+debug off it still bounces direct-origin hits.
    from plat_pursuit.middleware import CloudflareOriginGuardMiddleware
    settings.DEBUG = False
    settings.IS_BETA = False
    mw = CloudflareOriginGuardMiddleware(lambda req: None)
    resp = mw(rf.get('/games/some-slug/some-user/'))
    assert resp.status_code == 302
    assert resp['Location'].startswith('https://platpursuit.com')
