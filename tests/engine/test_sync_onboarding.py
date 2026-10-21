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
    # The finale shows the Pursuer the history already earned (FV-03), not four PSN trophy counts.
    assert 'data-pursuit-yes' in body and 'data-pursuit-none' in body
    assert 'data-complete-stat' not in body, 'the trophy-count tally is back in the finale'
    assert '/career/?view=contracts&amp;status=claimable' in body, 'the finale lost its claim action'


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
    assert 'PlatPursuit.countUp' in code, (
        'the finale count must ride the house count-up, not snap into place'
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


# --- the banked-so-far tally ---

def test_the_payload_stats_climb_during_the_walk(client):
    """The tally mechanism (audit-corrected): the per-type denorms climb LIVE via the
    EarnedTrophy post_save signal, so the payload's stats block IS the growing tally --
    no bespoke query, no bespoke key. Unearned rows must not count."""
    from tests.factories import EarnedTrophyFactory

    client, profile = _syncing_client(client, total_trophies=0)
    for _ in range(3):
        EarnedTrophyFactory(profile=profile)
    EarnedTrophyFactory(profile=profile, earned=False)

    data = client.get('/api/profile-sync-status/', **CF).json()

    stats = data['stats']
    assert stats['plats'] + stats['golds'] + stats['silvers'] + stats['bronzes'] == 3
    assert 'live_tally' not in data, 'the bespoke key is retired; stats are the tally'


def test_the_tally_line_renders_for_first_syncs_only(client):
    """Server-rendered from the live per-type denorms (zero queries), first syncs only."""
    from tests.factories import EarnedTrophyFactory

    client, profile = _syncing_client(client, total_trophies=0)
    for _ in range(2):
        EarnedTrophyFactory(profile=profile)

    body = client.get('/', **CF).content.decode()
    assert 'data-sync-tally' in body
    assert 'trophies banked so far' in body
    assert 'data-countup="2"' in body, 'the initial paint must show the real server-side count'

    client.logout()
    refresh_client, _ = _syncing_client(client, total_trophies=500)
    body = refresh_client.get('/', **CF).content.decode()
    assert 'data-sync-tally' not in body


def test_the_tally_and_finale_wiring_survives(client):
    """Source pins for the audit-caught races: landing.js must not claim the tally element
    (a second uncancellable countUp loop parks it on a stale number), the finale count-up
    must wait a frame for the trailing stats event, and a re-sync from the finale must not
    replay the reveal."""
    landing = _code(Path(settings.BASE_DIR) / 'static' / 'js' / 'landing.js')
    assert ':not([data-sync-tally])' in landing, 'the global count-up sweep claims the tally again'

    syncing = _code(Path(settings.BASE_DIR) / 'static' / 'js' / 'syncing.js')
    assert 'requestAnimationFrame(startCountUps)' in syncing
    assert 'if (completed) { return; }' in syncing, 'a second synced transition replays the finale'


def test_the_synced_payload_says_what_the_history_already_earned(client):
    """FV-03: the finale reads the synced poll's `pursuit` block. Contract detection runs inside
    sync_complete, before the profile is marked synced, so the claimables exist by then."""
    import itertools
    from django.utils import timezone
    from trophies.models import Contract, EarnedContract, Job

    client, profile = _syncing_client(client, total_trophies=0)
    data = client.get('/api/profile-sync-status/', **CF).json()
    assert 'pursuit' not in data, 'the syncing polls must stay free of the projection'

    profile.sync_status = 'synced'
    profile.save(update_fields=['sync_status'])
    assert client.get('/api/profile-sync-status/', **CF).json()['pursuit'] == {'count': 0}

    ids = itertools.count(95001)
    for _ in range(2):
        n = next(ids)
        contract = Contract.objects.create(name=f'fin-{n}', slug=f'fin-{n}', igdb_id=n, is_live=True)
        contract.jobs.set(Job.objects.filter(slug='pathfinder'))
        EarnedContract.objects.create(profile=profile, contract=contract, full_reached_at=timezone.now())

    pursuit = client.get('/api/profile-sync-status/', **CF).json()['pursuit']
    assert pursuit['count'] == 2
    assert pursuit['from_level'] == 1, 'the finale cannot tell whether claiming moves the level'
    assert pursuit['level'] == 5 and pursuit['rank'] == 'Newbie'      # 2 x 6,000 XP = 4 levels gained
    assert pursuit['top'] == [{'name': 'Pathfinder', 'from': 1, 'to': 5}]


def test_the_finale_script_and_template_agree_on_every_hook(client):
    """syncing.js finds the finale's parts by `data-pursuit-*` hook names; a name renamed on one side only
    would leave that part blank with nothing failing. Job names arrive from the payload, so they are written
    as text, never as markup."""
    import re

    code = _code(Path(settings.BASE_DIR) / 'static' / 'js' / 'syncing.js')
    template = (Path(settings.BASE_DIR) / 'templates' / 'home' / '_hero_syncing.html').read_text(encoding='utf-8')
    used = set(re.findall(r"hook\('([a-z]+)'\)", code)) | set(re.findall(r"setText\('([a-z]+)'", code))
    assert used >= {'yes', 'none', 'claim', 'count', 'noun', 'pronoun', 'reach', 'level', 'rank', 'top'}
    missing = sorted(name for name in used if f'data-pursuit-{name}' not in template)
    assert not missing, f'syncing.js reads hooks the finale does not render: {missing}'
    fill = code[code.index('function fillPursuit'):code.index('function startCountUps')]
    assert 'innerHTML' not in fill, 'the finale writes payload text as markup'
    assert "pursuit.level > (pursuit.from_level || 0)" in fill, 'the finale states a level claiming does not move'
