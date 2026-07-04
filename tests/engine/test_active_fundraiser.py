"""Active-fundraiser resolution shared by the site-wide banner (the `active_fundraiser` context
processor) and the Support hub landing (`get_active_fundraiser`). Pins the linked-only gating, the
live-window filter, and the banner_active flag after the phase-2 refactor extracted the fetch into
fundraiser.models.get_active_fundraiser."""
from datetime import timedelta

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.test import RequestFactory
from django.utils import timezone

from fundraiser.models import Fundraiser, get_active_fundraiser, get_live_fundraiser
from plat_pursuit.context_processors import active_fundraiser
from tests.factories import ProfileFactory, UserFactory

pytestmark = pytest.mark.django_db

CACHE_KEY = 'fundraiser:active_banner'


@pytest.fixture(autouse=True)
def _clear_cache():
    for k in (CACHE_KEY, 'fundraiser:live'):
        cache.delete(k)
    yield
    for k in (CACHE_KEY, 'fundraiser:live'):
        cache.delete(k)


def _live(**over):
    now = timezone.now()
    defaults = dict(
        name='Badge Artwork', slug='badge-art', description='Fund the art.',
        banner_active=True, start_date=now - timedelta(days=1), end_date=None,
    )
    defaults.update(over)
    return Fundraiser.objects.create(**defaults)


def _req(user=None):
    r = RequestFactory().get('/')
    r.user = user or AnonymousUser()
    return r


# --- get_active_fundraiser (ungated; the Support landing uses this) ---

def test_get_active_returns_live_banner_fundraiser():
    f = _live()
    assert get_active_fundraiser() == f


def test_get_active_none_when_banner_off():
    _live(banner_active=False)
    assert get_active_fundraiser() is None


def test_get_active_none_when_upcoming():
    _live(start_date=timezone.now() + timedelta(days=1))
    assert get_active_fundraiser() is None


def test_get_active_none_when_ended():
    now = timezone.now()
    _live(start_date=now - timedelta(days=5), end_date=now - timedelta(days=1))
    assert get_active_fundraiser() is None


# --- get_live_fundraiser (ungated + banner-independent; the Support landing uses this) ---

def test_get_live_shows_campaign_even_with_banner_off():
    """The decoupling: the Support hub shows a LIVE campaign even when the site banner is off,
    while the banner (get_active_fundraiser) stays hidden."""
    f = _live(banner_active=False)
    assert get_live_fundraiser() == f
    assert get_active_fundraiser() is None


def test_get_live_none_when_upcoming():
    _live(start_date=timezone.now() + timedelta(days=1))
    assert get_live_fundraiser() is None


# --- active_fundraiser banner processor (gated to linked viewers) ---

def test_banner_shows_for_linked_viewer():
    f = _live()
    assert active_fundraiser(_req(ProfileFactory(is_linked=True).user)) == {'active_fundraiser': f}


def test_banner_hidden_for_anon():
    _live()
    assert active_fundraiser(_req()) == {}


def test_banner_hidden_for_authed_unlinked():
    _live()
    assert active_fundraiser(_req(ProfileFactory(is_linked=False).user)) == {}
