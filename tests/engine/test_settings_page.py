"""The rebuilt Settings page (/users/settings/), its four POST actions, and the email parking.

The page had ONE smoke test before the 2026-08 rebuild; this file is the backfill. The
load-bearing behaviours: the timezone save goes through the one writer (confirmation stamp +
recap un-finalize -- the old page skipped both), the library toggles recompute the trophy-count
denorms on BOTH write paths (page and quick-settings API, which silently skipped it), the
password action is throttled and shows field errors, and the non-vital emails stay parked
(digest gate, prefs routes 302 home, kept transactional footers point at Settings).
"""
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from tests.factories import ProfileFactory
from trophies.models import MonthlyRecap

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[2]
URL = '/users/settings/'

KEPT_EMAIL_TEMPLATES = [
    'email_verification.html', 'password_reset.html', 'welcome.html',
    'subscription_welcome.html', 'payment_failed.html', 'payment_succeeded.html',
    'payment_action_required.html', 'subscription_cancelled.html',
    'donation_receipt.html', 'badge_claim_confirmation.html', 'artwork_complete.html',
]


@pytest.fixture
def linked_client(client):
    profile = ProfileFactory(is_linked=True)
    client.force_login(profile.user)
    client.profile = profile
    return client


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


# ── the page ──────────────────────────────────────────────────────────────────────────────────

def test_anonymous_visitors_are_sent_to_login(client):
    resp = client.get(URL)

    assert resp.status_code == 302
    assert 'login' in resp['Location']


def test_the_page_renders_with_a_profile(linked_client):
    body = linked_client.get(URL).content.decode()

    for marker in ('Regional', 'Library', 'Account', 'Membership', 'stg-sec'):
        assert marker in body
    assert '{#' not in body  # multi-line comment leak guard


def test_the_page_renders_without_a_profile(client):
    profile = ProfileFactory(is_linked=True)
    user = profile.user
    profile.unlink_user()
    client.force_login(user)

    body = client.get(URL).content.decode()

    assert 'Link your PSN account' in body


# ── regional ──────────────────────────────────────────────────────────────────────────────────

def test_regional_save_goes_through_the_one_timezone_writer(linked_client):
    """The settings page used to write user_timezone alone: no confirmation stamp, no recap
    un-finalize. Both side effects are the point of the rebuild's service."""
    profile = linked_client.profile
    MonthlyRecap.objects.create(profile=profile, year=2024, month=3, is_finalized=True)

    resp = linked_client.post(URL, {'action': 'regional', 'user_timezone': 'America/New_York'})

    assert resp.status_code == 302
    profile.user.refresh_from_db()
    assert profile.user.user_timezone == 'America/New_York'
    assert profile.user.timezone_confirmed_at is not None
    assert not MonthlyRecap.objects.filter(profile=profile, is_finalized=True).exists()


def test_confirming_the_same_zone_stamps_but_keeps_recaps_finalized(linked_client):
    profile = linked_client.profile
    MonthlyRecap.objects.create(profile=profile, year=2024, month=3, is_finalized=True)

    linked_client.post(URL, {'action': 'regional', 'user_timezone': 'UTC'})

    profile.user.refresh_from_db()
    assert profile.user.timezone_confirmed_at is not None, 'confirming UTC is an answer'
    assert MonthlyRecap.objects.filter(profile=profile, is_finalized=True).exists()


def test_an_invalid_timezone_changes_nothing(linked_client):
    resp = linked_client.post(URL, {'action': 'regional', 'user_timezone': 'Mars/Olympus_Mons'})

    assert resp.status_code == 302
    linked_client.profile.user.refresh_from_db()
    assert linked_client.profile.user.user_timezone == 'UTC'
    assert linked_client.profile.user.timezone_confirmed_at is None


def test_the_clock_format_round_trips(linked_client):
    linked_client.post(URL, {'action': 'regional', 'user_timezone': 'UTC', 'use_24hr_clock': 'on'})
    linked_client.profile.user.refresh_from_db()
    assert linked_client.profile.user.use_24hr_clock is True

    linked_client.post(URL, {'action': 'regional', 'user_timezone': 'UTC'})
    linked_client.profile.user.refresh_from_db()
    assert linked_client.profile.user.use_24hr_clock is False


# ── library ───────────────────────────────────────────────────────────────────────────────────

def test_library_toggles_persist_and_recompute_the_denorms(linked_client):
    with patch('users.views.update_profile_trophy_counts') as recompute:
        resp = linked_client.post(URL, {'action': 'library', 'hide_hiddens': 'on'})

    assert resp.status_code == 302
    linked_client.profile.refresh_from_db()
    assert linked_client.profile.hide_hiddens is True
    assert linked_client.profile.hide_zeros is False
    recompute.assert_called_once()


def test_the_quick_settings_api_recomputes_too(linked_client):
    """The API path wrote the same two fields and silently skipped the recompute: totals went
    stale until the nightly recalc. Now both writers pay the same bill."""
    with patch('api.user_settings_views.update_profile_trophy_counts') as recompute:
        resp = linked_client.post(
            reverse('api:user-quick-settings'),
            data={'setting': 'hide_zeros', 'value': True}, content_type='application/json')

    assert resp.status_code == 200
    linked_client.profile.refresh_from_db()
    assert linked_client.profile.hide_zeros is True
    recompute.assert_called_once()


def test_the_quick_settings_timezone_branch_stamps_confirmation_now(linked_client):
    """The third divergent writer: it un-finalized recaps but never stamped the confirmation,
    so the recap prompt kept nagging users who had already answered through it."""
    linked_client.post(
        reverse('api:user-quick-settings'),
        data={'setting': 'user_timezone', 'value': 'Europe/London'}, content_type='application/json')

    linked_client.profile.user.refresh_from_db()
    assert linked_client.profile.user.user_timezone == 'Europe/London'
    assert linked_client.profile.user.timezone_confirmed_at is not None


# ── password ──────────────────────────────────────────────────────────────────────────────────

def _pw_post(client, old, new='correct-horse-battery-staple-9'):
    return client.post(URL, {
        'action': 'change_password',
        'old_password': old, 'new_password1': new, 'new_password2': new,
    })


def test_password_change_works_and_keeps_the_session(linked_client):
    user = linked_client.profile.user
    user.set_password('old-password-123')
    user.save()
    linked_client.force_login(user)

    resp = _pw_post(linked_client, 'old-password-123')

    assert resp.status_code == 302
    user.refresh_from_db()
    assert user.check_password('correct-horse-battery-staple-9')
    # The session survived the hash change (update_session_auth_hash ran).
    assert linked_client.get(URL).status_code == 200


def test_a_failed_password_change_rerenders_with_the_field_error(linked_client):
    """No redirect on failure: the bound form's field errors reach the template instead of
    collapsing into one generic banner."""
    resp = _pw_post(linked_client, 'not-my-password')

    assert resp.status_code == 200
    assert 'stg-field__error' in resp.content.decode()


def test_the_password_action_throttles_after_five_failures(linked_client):
    for _ in range(5):
        _pw_post(linked_client, 'wrong-every-time')

    resp = _pw_post(linked_client, 'wrong-every-time')

    assert resp.status_code == 302, 'the sixth attempt should not even reach the form'


# ── unlink ────────────────────────────────────────────────────────────────────────────────────

def test_unlink_disconnects_the_profile(linked_client):
    profile = linked_client.profile

    resp = linked_client.post(URL, {'action': 'unlink_profile'})

    assert resp.status_code == 302
    profile.refresh_from_db()
    assert profile.user is None
    assert profile.is_linked is False


def test_an_unknown_action_is_a_quiet_redirect(linked_client):
    assert linked_client.post(URL, {'action': 'launch_the_missiles'}).status_code == 302


# ── the email parking ─────────────────────────────────────────────────────────────────────────

def test_the_weekly_digest_command_noops_while_parked(linked_client, settings, mailoutbox):
    settings.WEEKLY_DIGEST_SEND_ENABLED = False

    call_command('send_weekly_digest')

    assert len(mailoutbox) == 0


def test_the_email_preferences_routes_park_to_settings(client):
    for name in ('email_preferences', 'email_preferences_redirect'):
        resp = client.get(reverse(name))
        assert resp.status_code == 302
        assert resp['Location'] == URL


def test_kept_transactional_footers_point_at_settings_not_preferences():
    """The 11 emails that still send are transactional; their footers now link Account
    Settings (via {% url %}, which follows renames) instead of the parked preferences page."""
    for name in KEPT_EMAIL_TEMPLATES:
        body = (ROOT / 'templates' / 'emails' / name).read_text(encoding='utf-8')
        assert 'Manage your account settings' in body, f'{name} lost its footer'
        assert 'Manage your email preferences' not in body, f'{name} still sells the parked page'
    # Both bases: the contract ("the footer sells Account Settings, never the parked
    # preferences page") is base-agnostic, and v2 must be born compliant.
    for base_name in ('base_email.html', 'base_email_v2.html'):
        base = (ROOT / 'templates' / 'emails' / base_name).read_text(encoding='utf-8')
        assert 'Account Settings' in base, f'{base_name} lost its settings link'
        assert '{{ preference_url }}' not in base, f'{base_name} sells the parked page'


def test_default_region_is_gone_from_the_user_model():
    from django.core.exceptions import FieldDoesNotExist
    from users.models import CustomUser

    with pytest.raises(FieldDoesNotExist):
        CustomUser._meta.get_field('default_region')


# ── the additions ─────────────────────────────────────────────────────────────────────────────

def test_the_allauth_email_page_wears_the_house_override(linked_client):
    body = linked_client.get(reverse('account_email')).content.decode()

    assert 'Email addresses' in body
    assert 'Back to settings' in body


def test_a_member_without_a_linked_profile_still_gets_the_membership_card(client):
    """The audit-caught bug: membership was gated on the profile's user_is_premium denorm, so
    a paying member with no linked PSN profile (or a denorm lagging a fresh purchase) read as
    'not a member'. The worn level alone answers membership."""
    profile = ProfileFactory(is_linked=True)
    user = profile.user
    user.premium_tier = 'backer'
    user.save(update_fields=['premium_tier'])
    profile.unlink_user()
    client.force_login(user)

    body = client.get(URL).content.decode()

    assert 'Manage membership' in body
    assert reverse('subscription_management') in body


def test_the_page_links_its_three_additions(linked_client):
    body = linked_client.get(URL).content.decode()

    assert reverse('account_email') in body                  # email management
    assert reverse('subscription_management') in body or reverse('support_hub') in body  # membership
    assert 'data-delete-open' in body                        # account deletion (real flow)


# ── the bundle ────────────────────────────────────────────────────────────────────────────────

def test_the_built_bundle_carries_the_settings_family():
    """Guards the input.css @import actually being written (the unwritten-import incident):
    read the BUILT bundle, not the source file."""
    built = (ROOT / 'static' / 'css' / 'output.css').read_text(encoding='utf-8')

    for selector in ('.stg-sec', '.stg-confirm__go', '.stg-delete__warn',
                     '.pp-cta', '.pp-cta--danger'):
        assert selector in built, f'{selector} missing from the built bundle'
