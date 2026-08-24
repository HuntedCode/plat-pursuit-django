"""The "Welcome to PlatPursuit 1.0" launch greeting (the Home lobby modal).

One-shot for EXISTING users: joined before settings.PP_LAUNCH_DATE, not yet greeted
(ui_flags['launch_welcome']), fully dormant while the date is unset. Render == armed; no
reopen affordance (a one-time announcement needs no recall). Doc: docs/features/onboarding.md.
"""
import re
from datetime import timedelta
from pathlib import Path

import pytest
from django.conf import settings as dj_settings
from django.urls import reverse
from django.utils import timezone

from tests.factories import ProfileFactory

pytestmark = pytest.mark.django_db

CF = {'HTTP_CF_RAY': '8f0000000000abcd-LHR'}


def _synced_client(client):
    profile = ProfileFactory(is_linked=True, sync_status='synced', total_trophies=10)
    client.force_login(profile.user)
    return client, profile


def _code(path):
    """Comment-stripped source (the comment-names-the-forbidden-string trap, thrice bitten)."""
    text = Path(path).read_text(encoding='utf-8')
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
    text = re.sub(r'^\s*//.*$', '', text, flags=re.M)
    text = re.sub(r'{%\s*comment\s*%}.*?{%\s*endcomment\s*%}', '', text, flags=re.S)
    return text


def test_ui_flag_endpoint_accepts_launch_welcome(client):
    client, profile = _synced_client(client)

    resp = client.post(
        reverse('api:user-quick-settings'),
        data={'setting': 'ui_flag', 'value': 'launch_welcome'}, content_type='application/json')

    assert resp.status_code == 200
    profile.user.refresh_from_db()
    assert profile.user.ui_flags.get('launch_welcome') is True


def test_the_modal_is_dormant_while_the_launch_date_is_unset(client, settings):
    """The ships-dormant pin: the feature rides to prod inert and wakes when PP_LAUNCH_DATE
    is set at cutover."""
    settings.PP_LAUNCH_DATE = None
    client, _ = _synced_client(client)

    body = client.get('/', **CF).content.decode()

    assert 'id="launch-welcome"' not in body


def test_existing_users_get_the_greeting(client, settings):
    settings.PP_LAUNCH_DATE = timezone.now() + timedelta(days=1)   # user joined "before launch"
    client, _ = _synced_client(client)

    body = client.get('/', **CF).content.decode()

    assert 'id="launch-welcome"' in body
    assert 'data-auto' in body.split('id="launch-welcome"')[1].split('>')[0]
    assert 'Welcome to PlatPursuit 1.0' in body
    assert 'Look around' in body
    assert 'Your Career' in body and 'Your Collection' in body and 'Your new Home' in body
    assert chr(8212) not in body.split('id="launch-welcome"')[1].split('</script>')[0]
    assert 'confetti' not in body.split('id="launch-welcome"')[1].split('</script>')[0]


def test_post_launch_signups_never_see_it(client, settings):
    """New accounts get the onboarding built for them; a 1.0 welcome for someone who never
    saw 0.x is noise."""
    settings.PP_LAUNCH_DATE = timezone.now() - timedelta(days=1)   # user joined "after launch"
    client, _ = _synced_client(client)

    body = client.get('/', **CF).content.decode()

    assert 'id="launch-welcome"' not in body


def test_it_never_renders_once_flagged(client, settings):
    """Full non-render, unlike the Career howto: there is no reopen affordance to serve."""
    settings.PP_LAUNCH_DATE = timezone.now() + timedelta(days=1)
    client, profile = _synced_client(client)
    profile.user.ui_flags = {'launch_welcome': True}
    profile.user.save(update_fields=['ui_flags'])

    body = client.get('/', **CF).content.decode()

    assert 'id="launch-welcome"' not in body


def test_team_preview_forces_the_greeting(client, settings):
    """?preview=launch-welcome (staff/mod) shows it on demand, even flagged, even pre-date."""
    settings.PP_LAUNCH_DATE = None
    client, profile = _synced_client(client)
    profile.user.is_staff = True
    profile.user.ui_flags = {'launch_welcome': True}
    profile.user.save(update_fields=['is_staff', 'ui_flags'])

    body = client.get('/?preview=launch-welcome', **CF).content.decode()

    assert 'id="launch-welcome"' in body

    profile.user.is_staff = False
    profile.user.save(update_fields=['is_staff'])
    body = client.get('/?preview=launch-welcome', **CF).content.decode()
    assert 'id="launch-welcome"' not in body, 'the preview door leaked past the team gate'


def test_the_home_motion_gate_holds():
    """Source pins: the partial publishes the gate + settled event, home-motion consumes it
    fail-open, and the modal has its ID-scoped exit (the unscoped-close trap)."""
    base = Path(dj_settings.BASE_DIR)
    partial = _code(base / 'templates' / 'trophies' / 'partials' / 'home' / '_launch_welcome.html')
    assert 'ppAfterLaunchWelcome' in partial and 'launch-welcome:settled' in partial
    assert 'ccx-dev' not in partial, 'the one-panel-per-page rule'

    motion = _code(base / 'static' / 'js' / 'home-motion.js')
    assert 'ppAfterLaunchWelcome' in motion

    css = (base / 'static' / 'css' / 'components' / 'series-list.css').read_text(encoding='utf-8')
    assert '#launch-welcome.is-closing' in css


def test_the_flourish_never_promises_a_level_they_have_not_earned(client, settings):
    """XP comes from CLAIMING contracts, so a returning hunter opens this at Level 1 no matter
    how long their history is. The greeting points at the claimable pile instead; congratulating
    them on a level they do not have would be the first thing the new site got wrong."""
    settings.PP_LAUNCH_DATE = timezone.now() + timedelta(days=1)
    client, _ = _synced_client(client)

    body = client.get('/', **CF).content.decode()
    modal = body.split('id="launch-welcome"')[1].split('</script>')[0]

    assert 'Pursuer Level' not in modal
    assert 'You arrive as' not in modal
    assert 'claim' in modal.lower(), 'the flourish must point at claiming'
    # One real job icon per discipline orbits the ring. job_icon renders '' for an unknown
    # Lucide name, so a typo would ship an empty orbit with a green suite.
    assert modal.count('lwm__disc-ic') == 5, 'a discipline icon stopped resolving'
    for disc in ('combat', 'exploration', 'mind', 'heart', 'finesse'):
        assert f'var(--disc-{disc})' in modal, f'{disc} lost its discipline colour'


def test_the_flourish_counts_the_waiting_contracts(client, settings):
    """With claimables the greeting names the pile; without them it still teaches where levels
    come from (and a failed glance degrades to the same line)."""
    from unittest.mock import patch

    settings.PP_LAUNCH_DATE = timezone.now() + timedelta(days=1)
    client, _ = _synced_client(client)

    with patch('core.services.home_service.contract_service.claimable_summary',
               return_value={'count': 3, 'total_xp': 1250, 'items': [], 'more': 0}):
        body = client.get('/', **CF).content.decode()
    modal = body.split('id="launch-welcome"')[1].split('</script>')[0]
    assert '3</b> contract' in modal
    assert '1,250' in modal

    with patch('core.services.home_service.contract_service.claimable_summary',
               return_value={'count': 0, 'total_xp': 0, 'items': [], 'more': 0}):
        body = client.get('/', **CF).content.decode()
    modal = body.split('id="launch-welcome"')[1].split('</script>')[0]
    assert 'That is where every level comes from' in modal
