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
    ('/career/', 'career'),
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


def _grouped(ctx):
    groups = {}
    for i in ctx['hub_subnav_items']:
        groups.setdefault(i.group, []).append(i.slug)
    return groups


def test_browse_items_grouped_catalog_curation():
    # The rail groups locked in the chrome workshop.
    groups = _grouped(hub_subnav(_req('/games/')))
    assert groups['Catalog'] == ['games', 'trophies', 'badges', 'recently-added']
    assert groups['Curation'] == ['franchises', 'companies', 'engines', 'genres', 'flagged']


def test_community_items_grouped_explore_create():
    groups = _grouped(hub_subnav(_req('/community/')))
    assert groups['Explore'] == ['hub', 'profiles', 'leaderboards']
    assert groups['Create'] == ['rate_my_games', 'challenges', 'lists']


def test_my_pursuit_items_grouped_progress_tools():
    profile = ProfileFactory(is_linked=True)
    groups = _grouped(hub_subnav(_req('/', user=profile.user)))
    assert groups['Progress'] == ['overview', 'collection', 'career', 'milestones', 'titles']
    assert groups['Tools'] == ['stats', 'shareables', 'recap', 'profile']   # Profile is the dynamic extra
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


# --- Profile ownership-aware chrome (phase 3) ---

def test_own_profile_shows_my_pursuit_chrome():
    me = ProfileFactory(is_linked=True)
    ctx = hub_subnav(_req(f'/community/profiles/{me.psn_username}/', user=me.user))
    assert ctx['hub_section'] == 'my_pursuit'
    assert ctx['hub_subnav_active_slug'] == 'profile'


def test_other_profile_shows_community_chrome():
    me = ProfileFactory(is_linked=True)
    them = ProfileFactory(is_linked=True)
    ctx = hub_subnav(_req(f'/community/profiles/{them.psn_username}/', user=me.user))
    assert ctx['hub_section'] == 'community'


def test_anon_on_profile_shows_community_chrome():
    them = ProfileFactory(is_linked=True)
    ctx = hub_subnav(_req(f'/community/profiles/{them.psn_username}/'))   # anonymous viewer
    assert ctx['hub_section'] == 'community'


def test_strip_shown_for_authed_home_with_overview_profile_and_divider():
    profile = ProfileFactory(is_linked=True)   # Profile item needs a linked PSN profile
    ctx = hub_subnav(_req('/', user=profile.user))
    assert ctx['hub_section'] == 'my_pursuit'
    assert ctx['hub_subnav_active_slug'] == 'overview'
    slugs = [i.slug for i in ctx['hub_subnav_items']]
    assert slugs[0] == 'overview'
    assert 'profile' in slugs                                   # dynamic extra for linked viewers
    stats = next(i for i in ctx['hub_subnav_items'] if i.slug == 'stats')
    assert stats.group == 'Tools'                               # the Progress|Tools group boundary


# --- My Pursuit nav button + mobile tab are login-gated ---
# Anon has no pursuit to show and the logo already reaches /, so the personal-hub nav
# entry is hidden for logged-out visitors. Anchor on the mobile tab's aria-label -- it's
# unique to the gated element (the footer sitemap also carries the text "My Pursuit").

_MY_PURSUIT_TAB = b'aria-label="My Pursuit"'


def test_my_pursuit_nav_hidden_for_anon(client):
    resp = client.get('/support/')
    assert resp.status_code == 200
    assert _MY_PURSUIT_TAB not in resp.content


def test_my_pursuit_nav_shown_for_authed(client):
    profile = ProfileFactory(is_linked=True)
    client.force_login(profile.user)
    resp = client.get('/support/')
    assert resp.status_code == 200
    assert _MY_PURSUIT_TAB in resp.content


@pytest.mark.parametrize('old,new', [
    ('/my-pursuit/collection/', '/collection/'),
    ('/my-pursuit/lab/', '/career/'),
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
