"""Verifying a PSN profile, through the Discord bot's API (`api:verify`) and the website's poll
(`profile_verify`).

Three things went wrong before 2026-10:
- A private Gaming History never produced the privacy message. Privacy is recorded in the same
  save that moves `last_synced`, and both paths acted on "sync finished" first: the bot's loop broke
  out before its privacy check, and the website linked straight away. A private profile could link
  and then never sync a trophy.
- The bot's verify could commit the link and then fail on the Discord role push, leaving the user
  linked on the site, refused a retry as "already linked", and never given the Verified role.
- The bot's 409 showed the text of ANY ValueError raised during verify.
"""
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from trophies.models import DiscordAlreadyLinkedError, Profile
from trophies.services.verification_service import PRIVATE_HISTORY_MESSAGE
from tests.factories import ProfileFactory, UserFactory, bot_api_client

pytestmark = pytest.mark.django_db

DISCORD_ID = 424242


def _profile_ready_to_verify(*, public=True, **overrides):
    """An unlinked profile whose sync has just finished, with its code already in About Me."""
    profile = ProfileFactory(
        user=None, is_linked=False, discord_id=None, is_discord_verified=False,
        psn_history_public=public, **overrides,
    )
    code = profile.generate_verification_code()
    Profile.objects.filter(pk=profile.pk).update(
        about_me=f'my code is {code}',
        last_synced=timezone.now() + timedelta(minutes=5),  # finished after any start time
    )
    profile.refresh_from_db()
    return profile


@pytest.fixture
def quiet_verify(monkeypatch):
    """The bot's verify without its side trips: no sync job, no polling sleep, and the post-link
    badge, milestone and role work replaced so each test can watch or break them."""
    calls = {'roles': 0}

    def sync_roles(profile):
        calls['roles'] += 1

    monkeypatch.setattr(Profile, 'attempt_sync', lambda self: True)
    monkeypatch.setattr('api.views.time.sleep', lambda seconds: None)
    monkeypatch.setattr('trophies.services.badge_apply.evaluate_and_apply', lambda *a, **k: None)
    monkeypatch.setattr('milestones.services.is_member', lambda profile: False)
    monkeypatch.setattr('api.views.sync_discord_roles', sync_roles)
    return calls


def _bot_verify(profile):
    return bot_api_client().post(
        reverse('api:verify'), {'discord_id': DISCORD_ID, 'psn_username': profile.psn_username}, format='json')


# ── the bot's verify ───────────────────────────────────────────────────────────────────────────

def test_the_bot_refuses_a_private_profile_with_the_privacy_message(quiet_verify):
    profile = _profile_ready_to_verify(public=False)

    resp = _bot_verify(profile)

    assert resp.status_code == 403
    assert resp.json()['message'] == PRIVATE_HISTORY_MESSAGE
    profile.refresh_from_db()
    assert profile.discord_id is None, 'a private profile must not link'
    assert profile.verification_code, 'the code stays valid, so making it public and retrying works'


def test_the_bot_links_a_public_profile(quiet_verify):
    profile = _profile_ready_to_verify()

    resp = _bot_verify(profile)

    assert resp.status_code == 200 and resp.json()['success'] is True
    profile.refresh_from_db()
    assert profile.discord_id == DISCORD_ID and profile.is_discord_verified
    assert quiet_verify['roles'] == 1


def test_a_failed_role_push_does_not_undo_a_committed_link(quiet_verify, monkeypatch):
    def broken_role_push(profile):
        raise RuntimeError('Discord bot unreachable')

    monkeypatch.setattr('api.views.sync_discord_roles', broken_role_push)
    profile = _profile_ready_to_verify()

    resp = _bot_verify(profile)

    assert resp.status_code == 200 and resp.json()['success'] is True
    profile.refresh_from_db()
    assert profile.discord_id == DISCORD_ID


def test_an_already_linked_profile_gets_its_own_409(quiet_verify):
    profile = _profile_ready_to_verify()
    Profile.objects.filter(pk=profile.pk).update(discord_id=777)

    resp = _bot_verify(profile)

    assert resp.status_code == 409
    assert resp.json()['message'] == 'Discord already linked to this profile.'


def test_any_other_value_error_is_not_shown_to_the_user(quiet_verify, monkeypatch):
    """The 409 used to catch every ValueError and return its text."""
    def leaky(self, about_me):
        raise ValueError('internal detail: token pool exhausted for instance 3')

    monkeypatch.setattr(Profile, 'verify_code', leaky)
    profile = _profile_ready_to_verify()

    resp = _bot_verify(profile)

    assert resp.status_code == 500
    assert 'internal detail' not in resp.content.decode()


def test_link_discord_raises_its_own_error():
    profile = ProfileFactory(discord_id=1)
    with pytest.raises(DiscordAlreadyLinkedError):
        profile.link_discord(2)
    assert issubclass(DiscordAlreadyLinkedError, ValueError), 'existing ValueError handlers still catch it'


# ── the website's poll ─────────────────────────────────────────────────────────────────────────

def _poll(client, profile, start):
    return client.get(reverse('profile_verify'), {'profile_id': profile.pk, 'start_time': start.timestamp()})


@pytest.fixture
def signed_in(client):
    user = UserFactory()
    client.force_login(user)
    return client, user


def test_the_website_refuses_a_private_profile_with_the_privacy_message(signed_in):
    client, user = signed_in
    profile = _profile_ready_to_verify(public=False)

    resp = _poll(client, profile, timezone.now())

    assert resp.status_code == 400
    assert resp.json()['error'] == PRIVATE_HISTORY_MESSAGE
    profile.refresh_from_db()
    assert profile.user is None, 'a private profile must not link'


def test_the_website_links_a_public_profile(signed_in):
    client, user = signed_in
    profile = _profile_ready_to_verify()

    resp = _poll(client, profile, timezone.now())

    assert resp.json() == {'synced': True, 'verified': True}
    profile.refresh_from_db()
    assert profile.user == user


@pytest.mark.parametrize('public, message', [
    (False, PRIVATE_HISTORY_MESSAGE),
    (True, 'PSN sync failed. Please try again in a few minutes.'),
])
def test_a_sync_error_says_which_problem_it_is(signed_in, public, message):
    """The old message blamed Gaming History for every sync error. This applies while the
    attempt's own sync has not finished; once it has, that sync decides."""
    client, _ = signed_in
    profile = _profile_ready_to_verify(public=public)
    Profile.objects.filter(pk=profile.pk).update(sync_status='error')

    resp = _poll(client, profile, timezone.now() + timedelta(hours=1))  # this attempt's sync not done

    assert resp.status_code == 400
    assert resp.json()['error'] == message


def test_the_website_keeps_polling_until_the_sync_finishes(signed_in):
    client, _ = signed_in
    profile = _profile_ready_to_verify()

    resp = _poll(client, profile, timezone.now() + timedelta(hours=1))  # started after the last sync

    assert resp.json() == {'synced': False, 'verified': False}


def test_the_link_page_shows_the_servers_reason_not_the_status_code():
    """link-psn.js used to show "Error checking status: API request failed: 400" for every refusal,
    which hid the privacy message above. It now reads the server's own reason."""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / 'static' / 'js' / 'link-psn.js').read_text(encoding='utf-8')
    assert 'API.failureMessage(error)' in source
    assert 'Error checking status:' not in source


# ── retrying after making the profile public ───────────────────────────────────────────────────

def _left_by_a_private_attempt(**overrides):
    """What an earlier private attempt leaves: the privacy flag off, and the privacy-error job's
    sync_status='error'. Neither was ever cleared, so every retry failed before its own sync."""
    profile = _profile_ready_to_verify(public=False, **overrides)
    Profile.objects.filter(pk=profile.pk).update(
        sync_status='error', last_synced=timezone.now() - timedelta(minutes=10))
    profile.refresh_from_db()
    return profile


def _sync_finishes_public(profile_pk):
    """Stands in for the sync the retry starts: it reads the now-public PSN profile."""
    def attempt_sync(self):
        Profile.objects.filter(pk=profile_pk).update(
            psn_history_public=True, last_synced=timezone.now() + timedelta(minutes=5))
        return True
    return attempt_sync


def test_the_bot_verifies_once_the_profile_is_made_public(quiet_verify, monkeypatch):
    """The sync is queued, so the poll's first pass still sees what the private attempt left; it
    finishes (public) while the poll waits. A check of the stale flag on that first pass, as the
    loop used to have, refused every retry."""
    profile = _left_by_a_private_attempt()
    finish_sync = _sync_finishes_public(profile.pk)
    monkeypatch.setattr('api.views.time.sleep', lambda seconds: finish_sync(None))

    resp = _bot_verify(profile)

    assert resp.status_code == 200, resp.json()
    profile.refresh_from_db()
    assert profile.discord_id == DISCORD_ID


def test_starting_a_verification_clears_what_the_last_attempt_left(monkeypatch):
    from trophies.services.verification_service import VerificationService

    profile = _left_by_a_private_attempt()
    started = []
    monkeypatch.setattr(Profile, 'attempt_sync', lambda self: started.append(self.pk) or True)

    VerificationService.start_verification_sync(profile)

    profile.refresh_from_db()
    assert profile.sync_status == 'synced'
    assert started == [profile.pk]


def test_inside_the_cooldown_it_still_syncs_the_profile_summary(monkeypatch):
    """attempt_sync refuses during the refresh cooldown; verifying only needs the summary."""
    from trophies.psn_manager import PSNManager
    from trophies.services.verification_service import VerificationService

    profile = _profile_ready_to_verify()
    summary_syncs = []
    monkeypatch.setattr(Profile, 'attempt_sync', lambda self: False)
    monkeypatch.setattr(PSNManager, 'sync_profile_data', classmethod(lambda cls, p: summary_syncs.append(p.pk)))

    VerificationService.start_verification_sync(profile)

    assert summary_syncs == [profile.pk]


def test_the_website_verifies_once_the_profile_is_made_public(signed_in, monkeypatch):
    """Step 2 starts the retry (and clears the stale error); the poll then sees its sync."""
    client, user = signed_in
    profile = _left_by_a_private_attempt()
    monkeypatch.setattr(Profile, 'attempt_sync', _sync_finishes_public(profile.pk))
    start = timezone.now()

    client.post(reverse('link_psn'), {'action': 'verify', 'psn_username': profile.psn_username})
    resp = _poll(client, profile, start)

    assert resp.json() == {'synced': True, 'verified': True}, resp.json()
    profile.refresh_from_db()
    assert profile.user == user
