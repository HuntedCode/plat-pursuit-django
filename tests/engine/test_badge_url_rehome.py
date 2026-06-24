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


def test_my_pursuit_root_now_lands_on_the_collection(client):
    """/my-pursuit/ used to 301 to the badge list; with the catalog gone it lands on the
    Collection (the personal album, which is the hub's sub-nav landing item)."""
    r = client.get('/my-pursuit/')
    assert r.status_code == 301 and r['Location'] == reverse('badge_collection')


def test_badges_is_a_browse_subnav_item():
    assert 'badges' in {item.slug for item in BROWSE_HUB.items}
    assert '/badges/' in BROWSE_HUB.prefixes
    # The badge detail page highlights the Badges browse tab.
    assert _URL_NAME_TO_SLUG_OVERRIDES['badge_detail'] == ('browse', 'badges')
    assert _URL_NAME_TO_SLUG_OVERRIDES['badge_detail_with_profile'] == ('browse', 'badges')
