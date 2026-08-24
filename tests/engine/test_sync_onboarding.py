"""The syncing hero on the gates surface (new-user onboarding, 2026-08).

The syncing state renders on home/landing.html: a live status hero greets the user with their
real PSN numbers (available seconds into the sync), the landing's real sections play the tour
below, and a first sync ends in an in-place "Your Pursuer has emerged" moment instead of a
silent reload. Doc: docs/features/onboarding.md.
"""
import re
from pathlib import Path

import pytest
from django.conf import settings

from tests.factories import ProfileFactory

pytestmark = pytest.mark.django_db

CF = {'HTTP_CF_RAY': '8f0000000000abcd-LHR'}

SUMMARY = {'bronze': 5000, 'silver': 2500, 'gold': 841, 'platinum': 71}


def _syncing_client(client, **profile_kwargs):
    defaults = dict(is_linked=True, sync_status='syncing', total_trophies=0)
    defaults.update(profile_kwargs)
    profile = ProfileFactory(**defaults)
    client.force_login(profile.user)
    return client, profile


def _code(path):
    """Comment-stripped JS source, borrowed from test_lobby's pin idiom."""
    text = Path(path).read_text(encoding='utf-8')
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
    text = re.sub(r'^\s*//.*$', '', text, flags=re.M)
    return text


# --- personalization ---

def test_the_syncing_page_personalizes_from_the_psn_summary(client):
    client, _ = _syncing_client(client, earned_trophy_summary=SUMMARY, trophy_level=512)

    body = client.get('/', **CF).content.decode()

    assert '8,412' in body, "PSN's own total never reached the greeting"
    # '71' alone appears in hashed filenames and SVG paths; pin the span's rendered value.
    assert body.split('data-psn-found-plats')[1].split('<')[0].endswith('>71'), (
        'the platinum span rendered something other than 71'
    )
    assert 'data-psn-line' in body


def test_the_syncing_page_falls_back_before_the_summary_lands(client):
    """The page can render before update_profile_from_legacy commits; the generic sentence
    holds the fort and carries the hook the live upgrade swaps on. Also pins the
    get_total_trophies_from_summary-returns-None trap: no 'None' in the copy."""
    client, _ = _syncing_client(client, earned_trophy_summary={})

    body = client.get('/', **CF).content.decode()

    assert 'data-psn-pending' in body
    assert 'None' not in body.split('data-psn-line')[1][:400]


def test_the_status_payload_carries_the_psn_summary(client):
    client, profile = _syncing_client(client, earned_trophy_summary=SUMMARY, trophy_level=512)

    data = client.get('/api/profile-sync-status/', **CF).json()

    assert data['psn_found'] == {'total': 8412, 'plats': 71, 'level': 512}


def test_the_status_payload_psn_found_is_none_without_a_summary(client):
    client, _ = _syncing_client(client, earned_trophy_summary={})

    data = client.get('/api/profile-sync-status/', **CF).json()

    assert data['psn_found'] is None


# --- the shared landing sections (the tour, since the gates merge) ---

def test_the_syncing_state_renders_the_landing_sections(client):
    """The gates merge retired the five-panel walkthrough carousel: the landing's real
    sections (ratings carousel, showcase card, medallion shelf) render below the syncing
    hero and ARE the tour."""
    client, _ = _syncing_client(client)

    body = client.get('/', **CF).content.decode()

    assert 'data-land-carousel' in body
    assert 'share-image-content' in body   # the showcase Profile Card (fixture cache-cold)
    assert 'data-sync-live' in body
    assert 'data-land-search' not in body, 'the search hero leaked into the syncing state'


# --- the enter moment ---

def test_the_enter_moment_ships_on_first_syncs(client):
    client, _ = _syncing_client(client, total_trophies=0)

    body = client.get('/', **CF).content.decode()

    assert 'data-sync-complete' in body
    assert 'Your Pursuer has emerged' in body
    assert 'Enter your Pursuit' in body
    assert 'data-enter-pursuit' in body
    # The TEMPLATE half of the JS contract: without data-sync-live the state machine silently
    # falls back to a reload and the feature vanishes with every other assertion still green.
    assert 'data-sync-live' in body
    assert 'data-complete-stat="plats"' in body


def test_quick_refreshes_do_not_get_the_finale(client):
    """A returning user mid-refresh wants straight in; the finale block only renders for
    first syncs, and its absence is what routes syncing.js to the old reload behaviour."""
    client, _ = _syncing_client(client, total_trophies=500)

    body = client.get('/', **CF).content.decode()

    assert 'data-sync-complete' not in body


def test_the_js_reloads_only_for_errors_and_refreshes():
    """Source pin on syncing.js: the synced branch must reach the finale, never a blind
    reload; the reload calls live in the no-finale fallback and the error branch. Also pins
    the template<->JS selector contract."""
    code = _code(Path(settings.BASE_DIR) / 'static' / 'js' / 'syncing.js')

    for selector in ('data-sync-complete', 'data-sync-live',
                     'data-sync-card', 'data-psn-pending'):
        assert selector in code, f'syncing.js lost its {selector} hook'
    assert code.count('window.location.reload') == 2, (
        'expected exactly two reload sites: the quick-refresh fallback and the error branch'
    )
    assert "status === 'synced'" in code.replace('"', "'")


def test_the_dev_panel_never_ships_to_prod(client):
    """The simulate buttons are gated on settings.DEBUG (tests run DEBUG=False)."""
    client, _ = _syncing_client(client)

    body = client.get('/', **CF).content.decode()

    assert 'data-sync-dev' not in body


# --- the team preview door (mirrors the landing preview pins) ---

def test_staff_can_preview_the_syncing_page(client):
    profile = ProfileFactory(is_linked=True, sync_status='synced', total_trophies=500)
    profile.user.is_staff = True
    profile.user.save(update_fields=['is_staff'])
    client.force_login(profile.user)

    body = client.get('/?preview=syncing', **CF).content.decode()

    assert 'data-sync-live' in body
    assert 'data-land-carousel' in body, 'the landing sections must render under the preview'
    # The preview forces the first-sync view, or it previews almost nothing (audit finding):
    # greeting, progress bar, and finale must all render for a synced previewer.
    assert 'data-psn-line' in body or 'data-psn-pending' in body
    assert 'home-sync-progress-bar' in body
    assert 'data-sync-complete' in body


def test_moderators_can_preview_the_syncing_page(client):
    profile = ProfileFactory(is_linked=True, sync_status='synced', total_trophies=500)
    profile.user.role = 'moderator'
    profile.user.save(update_fields=['role'])
    client.force_login(profile.user)

    body = client.get('/?preview=syncing', **CF).content.decode()

    assert 'data-sync-live' in body


def test_regular_users_cannot_preview_the_syncing_page(client):
    """The front door must not grow an undocumented public mode."""
    profile = ProfileFactory(is_linked=True, sync_status='synced', total_trophies=500)
    client.force_login(profile.user)

    body = client.get('/?preview=syncing', **CF).content.decode()

    # data-sync-live is the hero's outer wrapper, present in EVERY syncing sub-state --
    # its absence is what distinguishes "preview denied" from "hero rendered differently".
    assert 'data-sync-live' not in body
