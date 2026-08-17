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
    ('/collection/', 'collection'),
    ('/career/', 'career'),
    ('/milestones/', 'milestones'),
    ('/titles/', 'titles'),
    ('/shareables/', 'shareables'),
    ('/recap/', 'recap'),
    ('/rate-my-games/', 'rate_my_games'),
])
def test_personal_pages_resolve_to_my_pursuit(path, slug):
    match = resolve_hub_subnav(_req(path))
    assert match is not None
    assert match['hub'].key == 'my_pursuit'
    assert match['active_slug'] == slug


def test_other_hubs_unchanged():
    # Community was retired 2026-08; Leaderboards is the hub that replaced it in the nav.
    assert resolve_hub_subnav(_req('/badges/'))['hub'].key == 'browse'
    assert resolve_hub_subnav(_req('/leaderboards/'))['hub'].key == 'leaderboards'


def test_the_retired_community_paths_belong_to_no_hub():
    """What is left under /community/ is redirects and the reviews tombstone. None of it should sprout
    a sub-nav strip: a strip implies a section you are inside, and there is no longer one."""
    assert resolve_hub_subnav(_req('/community/reviews/')) is None
    assert hub_subnav(_req('/community/reviews/'))['hub_section'] is None


def _grouped(ctx):
    groups = {}
    for i in ctx['hub_subnav_items']:
        groups.setdefault(i.group, []).append(i.slug)
    return groups


def test_browse_items_grouped_catalog_curation():
    # Grouped rail, kept consistent with the other hubs' group labels.
    groups = _grouped(hub_subnav(_req('/games/')))
    # Profiles joined Catalog in 2026-08: hunters are another thing you browse.
    # 'jobs' joins Catalog 2026-08: `/jobs/` is the public jobs catalogue (leaderboards rebuild step 7).
    # It sits beside Games and Badges because those are the three things a hunter pursues; Recently
    # Added and Hunters keep their positions.
    assert groups['Catalog'] == ['games', 'badges', 'jobs', 'recently-added', 'profiles']
    assert groups['Curation'] == ['franchises', 'companies', 'genres']


def test_my_pursuit_items_grouped_progress_tools():
    profile = ProfileFactory(is_linked=True)
    # Read from a personal PAGE, not from `/`: the lobby has no hub and therefore no items.
    groups = _grouped(hub_subnav(_req('/career/', user=profile.user)))
    # Career leads Progress: it is My Pursuit's landing as of 2026-08 (Overview retired -- `/` became
    # the hub-less lobby).
    assert groups['Progress'] == ['career', 'collection', 'milestones', 'titles']
    # My Stats is hidden for 1.0 (staff-gated, off the rail). Profile is the dynamic extra.
    assert groups['Tools'] == ['shareables', 'recap', 'rate_my_games']
    assert resolve_hub_subnav(_req('/games/'))['hub'].key == 'browse'


def test_strip_hidden_for_anon_on_home():
    assert hub_subnav(_req('/'))['hub_section'] is None


def test_strip_hidden_for_anon_on_public_member():
    # /milestones/ is a public page, but the personal strip is authed-only.
    assert hub_subnav(_req('/milestones/'))['hub_section'] is None


def test_public_hubs_still_render_for_anon():
    # The anon gate is My-Pursuit-specific -- the public hubs' strips must still show.
    assert hub_subnav(_req('/games/'))['hub_section'] == 'browse'
    assert hub_subnav(_req('/hunters/'))['hub_section'] == 'browse'


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


# --- Profile chrome ---

def test_profiles_are_chromed_as_a_browse_surface():
    """They moved out from under /community/ with the hub teardown -- hunters are another thing you
    browse, alongside games and badges -- and were renamed Profiles -> Hunters (/hunters/) after."""
    them = ProfileFactory(is_linked=True)
    ctx = hub_subnav(_req(f'/hunters/{them.psn_username}/'))
    assert ctx['hub_section'] == 'browse'
    assert ctx['hub_subnav_active_slug'] == 'profiles'


def test_your_own_profile_is_chromed_like_anyone_elses():
    """The Profile strip-item and the ownership-aware chrome swap were removed together (2026-08).
    They only ever made sense as a pair: the swap put your own profile under the personal strip so the
    Profile TAB could be highlighted, and without that tab it would have rendered a strip highlighting
    nothing and naming nothing in the mobile collapse bar. Your profile is reached from the avatar menu
    now, and the page looks the same whoever is viewing it."""
    me = ProfileFactory(is_linked=True)
    ctx = hub_subnav(_req(f'/hunters/{me.psn_username}/', user=me.user))
    assert ctx['hub_section'] == 'browse'


def test_no_profile_tab_in_the_personal_strip():
    profile = ProfileFactory(is_linked=True)
    slugs = [i.slug for i in hub_subnav(_req('/career/', user=profile.user))['hub_subnav_items']]
    assert 'profile' not in slugs


def test_other_profile_shows_the_same_chrome():
    me = ProfileFactory(is_linked=True)
    them = ProfileFactory(is_linked=True)
    ctx = hub_subnav(_req(f'/hunters/{them.psn_username}/', user=me.user))
    assert ctx['hub_section'] == 'browse'


def test_anon_on_profile_shows_the_same_chrome():
    them = ProfileFactory(is_linked=True)
    ctx = hub_subnav(_req(f'/hunters/{them.psn_username}/'))   # anonymous viewer
    assert ctx['hub_section'] == 'browse'


def test_the_lobby_carries_no_strip_at_all():
    """`/` sits ABOVE the four hubs, so it belongs to none of them and renders no rail. On a lobby the
    CTAs are the navigation; a hub rail underneath them would be a second, competing set of directions."""
    profile = ProfileFactory(is_linked=True)
    assert hub_subnav(_req('/', user=profile.user))['hub_section'] is None


def test_strip_shown_for_authed_personal_page_with_career_first_and_divider():
    profile = ProfileFactory(is_linked=True)
    ctx = hub_subnav(_req('/career/', user=profile.user))
    assert ctx['hub_section'] == 'my_pursuit'
    assert ctx['hub_subnav_active_slug'] == 'career'
    slugs = [i.slug for i in ctx['hub_subnav_items']]
    assert slugs[0] == 'career'                                 # the hub's landing leads its rail
    shareables = next(i for i in ctx['hub_subnav_items'] if i.slug == 'shareables')
    assert shareables.group == 'Tools'                          # the Progress|Tools group boundary


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
    # Straight to `/`, not to `/profile-editor/`. Profile customization is hidden (2026-08) and the
    # editor 302s home itself, so pointing here at the canonical path would make every visitor holding
    # the older bookmark take two hops to reach the same place. Still 301: the /my-pursuit/ -> root
    # move is permanent whatever happens to customization. See test_showcases_hidden.py.
    ('/my-pursuit/profile-editor/', '/'),
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
