"""Badge catalog URL re-home.

The badge list + detail moved from ``/my-pursuit/badges/*`` to the Browse hub at ``/badges/*``
(the public discovery catalog). The personal album (Collection) stays at
``/my-pursuit/collection/``. This pins the canonical paths, the legacy 301s, and the Browse
sub-nav wiring so the move can't silently regress (URL renames are easy to break invisibly).
"""
import pytest
from django.urls import reverse

from core.hub_subnav import BROWSE_HUB, _URL_NAME_TO_SLUG_OVERRIDES

pytestmark = pytest.mark.django_db


def test_badge_catalog_is_canonical_under_slash_badges():
    assert reverse('badges_list') == '/badges/'
    assert reverse('badge_detail', kwargs={'series_slug': 'resident-evil'}) == '/badges/resident-evil/'


def test_legacy_my_pursuit_badge_paths_redirect_to_browse(client):
    r = client.get('/my-pursuit/badges/')
    assert r.status_code == 301 and r['Location'] == '/badges/'

    r = client.get('/my-pursuit/badges/resident-evil/')
    assert r.status_code == 301 and r['Location'] == '/badges/resident-evil/'


def test_my_pursuit_root_now_redirects_home(client):
    """/my-pursuit/ used to 301 to the Collection; in the personal-hub unify the Overview lives at
    the Home (/), so the bare hub path redirects there."""
    r = client.get('/my-pursuit/')
    assert r.status_code == 301 and r['Location'] == reverse('home')


def test_badges_is_a_browse_subnav_item():
    assert 'badges' in {item.slug for item in BROWSE_HUB.items}
    assert '/badges/' in BROWSE_HUB.prefixes
    # The badge detail page highlights the Badges browse tab.
    assert _URL_NAME_TO_SLUG_OVERRIDES['badge_detail'] == ('browse', 'badges')
    assert _URL_NAME_TO_SLUG_OVERRIDES['badge_detail_with_profile'] == ('browse', 'badges')


def test_bot_redirect_collapses_profile_badge_urls_to_new_canonical():
    """The crawler short-circuit must target the NEW canonical /badges/<slug>/ in one hop --
    targeting the old /my-pursuit/badges/<slug>/ would now bounce through a second 301."""
    from plat_pursuit.middleware import _BOT_REDIRECT_RULES

    def collapse(path):
        for pattern, target in _BOT_REDIRECT_RULES:
            m = pattern.match(path)
            if m:
                return target.format(slug=m.group(1))
        return None

    # The new canonical profile-scoped path collapses to the canonical badge page.
    assert collapse('/badges/resident-evil/someuser/') == '/badges/resident-evil/'
    # Legacy prefixes also collapse straight to the new canonical (one hop, not via a 301).
    assert collapse('/my-pursuit/badges/re/u/') == '/badges/re/'
    assert collapse('/achievements/badges/re/u/') == '/badges/re/'
