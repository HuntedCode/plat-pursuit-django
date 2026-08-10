"""Path coverage for the Cloudflare origin guard.

The 2026-08-09 outage started on /community/profiles/<username>/, which was the one
expensive enumerable page covered by none of the four guards. It is the canonical page,
so it has no redirect target and cannot be protected by _BOT_REDIRECT_RULES the way the
profile-scoped variants are -- the origin guard is what covers it instead.

These are pure regex assertions (no DB), pinning both what the guard now catches and
what it must keep leaving alone: over-matching here would bounce real traffic, and the
guard is deliberately scoped so a proxy misconfiguration cannot lock us out of the site.
"""
import pytest

from plat_pursuit.middleware import _CLOUDFLARE_GUARDED_PATH_RE as GUARD


@pytest.mark.parametrize('path', [
    '/community/profiles/hunter0001/',
    '/community/profiles/hunter0001',
    '/community/profiles/hunter0001/trophy-case/',
    '/games/NPWR12345_00/hunter0001/',
    '/my-pursuit/badges/lego/hunter0001/',
    '/badges/lego/hunter0001/',
    '/achievements/badges/lego/hunter0001/',
])
def test_guarded_paths_match(path):
    assert GUARD.match(path), f'{path} should be guarded'


@pytest.mark.parametrize('path', [
    '/',                             # health check target -- must never be guarded
    '/community/profiles/',          # the profile LIST page is cheap and paginated
    '/games/NPWR12345_00/',          # canonical game page
    '/my-pursuit/badges/lego/',      # canonical badge page
    '/static/css/output.css',
    '/api/v1/profiles/',
])
def test_unguarded_paths_do_not_match(path):
    assert not GUARD.match(path), f'{path} must stay unguarded'
