"""Tests for the personal-hub unify (IA build, phase 1).

Pins: the personal pages now live at ROOT and resolve to the My Pursuit hub; the logged-in Home
(/) is the hub Overview; the strip is auth-gated (anon = no strip, even on public members); the
dynamic Profile item appears for linked viewers; and every old /my-pursuit/* + /dashboard/* path
301-redirects to its new root canonical.
"""
import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory
from django.urls import resolve

from core.hub_subnav import resolve_hub_subnav
from plat_pursuit.context_processors import hub_subnav
from tests.factories import ProfileFactory

pytestmark = pytest.mark.django_db


def _req(path, user=None):
    request = RequestFactory().get(path)
    request.resolver_match = resolve(path)
    request.user = user or AnonymousUser()
    return request


@pytest.mark.parametrize('path,slug', [
    ('/', 'overview'),
    ('/collection/', 'collection'),
    ('/lab/', 'lab'),
    ('/research-panel/', 'research-panel'),
    ('/milestones/', 'milestones'),
    ('/titles/', 'titles'),
    ('/stats/', 'stats'),
    ('/shareables/', 'shareables'),
    ('/recap/', 'recap'),
])
def test_personal_pages_resolve_to_my_pursuit(path, slug):
    match = resolve_hub_subnav(_req(path))
    assert match is not None
    assert match['hub'].key == 'my_pursuit'
    assert match['active_slug'] == slug


def test_other_hubs_unchanged():
    assert resolve_hub_subnav(_req('/community/challenges/'))['hub'].key == 'community'
    assert resolve_hub_subnav(_req('/games/'))['hub'].key == 'browse'


def test_strip_hidden_for_anon_on_home():
    assert hub_subnav(_req('/'))['hub_section'] is None


def test_strip_hidden_for_anon_on_public_member():
    # /milestones/ is a public page, but the personal strip is authed-only.
    assert hub_subnav(_req('/milestones/'))['hub_section'] is None


def test_public_hubs_still_render_for_anon():
    # The anon gate is My-Pursuit-specific -- Browse/Community strips must still show.
    assert hub_subnav(_req('/games/'))['hub_section'] == 'browse'
    assert hub_subnav(_req('/community/challenges/'))['hub_section'] == 'community'


# --- Support hub (phase 2) ---

def test_support_hub_resolves_incl_fundraiser():
    assert resolve_hub_subnav(_req('/support/'))['hub'].key == 'support'
    m = resolve_hub_subnav(_req('/fundraiser/spring-drive/'))   # re-homed to Support
    assert m['hub'].key == 'support' and m['active_slug'] is None


def test_support_hub_has_no_strip_items():
    # Support is landing-focused: hub_section set (navbar highlights) but no strip.
    ctx = hub_subnav(_req('/support/'))
    assert ctx['hub_section'] == 'support' and ctx['hub_subnav_items'] == ()


def test_support_landing_renders(client):
    resp = client.get('/support/')
    assert resp.status_code == 200
    assert b'Support Platinum Pursuit' in resp.content


def test_strip_shown_for_authed_home_with_overview_profile_and_divider():
    profile = ProfileFactory(is_linked=True)   # Profile item needs a linked PSN profile
    ctx = hub_subnav(_req('/', user=profile.user))
    assert ctx['hub_section'] == 'my_pursuit'
    assert ctx['hub_subnav_active_slug'] == 'overview'
    slugs = [i.slug for i in ctx['hub_subnav_items']]
    assert slugs[0] == 'overview'
    assert 'profile' in slugs                                   # dynamic extra for linked viewers
    stats = next(i for i in ctx['hub_subnav_items'] if i.slug == 'stats')
    assert stats.divider_before is True                         # the 6+4 group divider


@pytest.mark.parametrize('old,new', [
    ('/my-pursuit/collection/', '/collection/'),
    ('/my-pursuit/lab/', '/lab/'),
    ('/my-pursuit/research-panel/', '/research-panel/'),
    ('/my-pursuit/milestones/', '/milestones/'),
    ('/my-pursuit/titles/', '/titles/'),
    ('/my-pursuit/profile-editor/', '/profile-editor/'),
    ('/dashboard/stats/', '/stats/'),
    ('/dashboard/shareables/', '/shareables/'),
    ('/dashboard/shareables/platinums/', '/shareables/platinums/'),
    ('/dashboard/recap/', '/recap/'),
    ('/my-pursuit/', '/'),
])
def test_old_paths_301_to_root(client, old, new):
    resp = client.get(old)
    assert resp.status_code == 301
    assert resp['Location'] == new
