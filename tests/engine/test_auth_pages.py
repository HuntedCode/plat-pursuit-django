"""The auth/account family (2026-08 review). Phase A pins: the two audit-caught bugs, the
shadow routes that give password-change and logout single owners, the rate-limit scoping
fixes, and the no-raw-allauth guarantee (an un-overridden reachable route renders allauth's
own <html> -- a white, chrome-less page on this dark site).
"""
from pathlib import Path

import pytest
from django.conf import settings as dj_settings
from django.template.loader import get_template
from django.urls import reverse

from tests.factories import ProfileFactory

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[2]


# ── the two audit-caught bugs ─────────────────────────────────────────────────────────────────

def test_an_expired_confirmation_link_no_longer_claims_success(client):
    """The template only ever renders on FAILURE (valid keys confirm-and-redirect via
    CONFIRM_EMAIL_ON_GET), yet it used to say "Email Confirmed. Time to start hunting."
    unconditionally -- an expired-link holder was told they succeeded."""
    resp = client.get('/accounts/confirm-email/not-a-real-key/')

    assert resp.status_code == 200
    body = resp.content.decode()
    assert 'This link has expired' in body
    assert 'Time to start hunting' not in body


def test_link_psn_redirects_anonymous_visitors_to_login(client):
    """login_url reversed a URL name that does not exist ('login'), so every anonymous hit
    was a NoReverseMatch 500 instead of a login redirect."""
    resp = client.get(reverse('link_psn'))

    assert resp.status_code == 302
    assert 'login' in resp['Location']


def test_link_psn_sends_already_linked_users_home_not_into_a_loop(client):
    profile = ProfileFactory(is_linked=True)
    client.force_login(profile.user)

    resp = client.get(reverse('link_psn'))

    assert resp.status_code == 302
    assert resp['Location'] == '/', 'redirecting back to link_psn itself was an infinite loop'


# ── single owners: password change + logout ───────────────────────────────────────────────────

def test_the_allauth_password_change_url_hands_off_to_settings(client):
    """Settings owns the password form (field errors + its own throttle); two password forms
    was the defect, so the live-and-typeable allauth URL 302s there."""
    profile = ProfileFactory(is_linked=True)
    client.force_login(profile.user)

    resp = client.get('/accounts/password/change/')

    assert resp.status_code == 302
    assert resp['Location'] == reverse('settings')


def test_the_allauth_logout_url_bounces_home(client):
    """/logout/ (the navbar's POST) is the one logout; allauth's GET-confirm page was linked
    from nowhere."""
    resp = client.get('/accounts/logout/')

    assert resp.status_code == 302
    assert resp['Location'] == '/'


def test_the_real_logout_is_post_only_and_renders_signed_out(client):
    profile = ProfileFactory(is_linked=True)
    client.force_login(profile.user)

    assert client.get('/logout/').status_code == 405, 'GET logout is a CSRF-shaped hole'

    resp = client.post('/logout/')
    assert resp.status_code == 200
    assert 'Signed Out' in resp.content.decode()


# ── config pins ───────────────────────────────────────────────────────────────────────────────

def test_rate_limits_are_scoped_and_the_resend_cooldown_is_back():
    limits = dj_settings.ACCOUNT_RATE_LIMITS

    assert limits['reset_password'] == '5/m/ip', \
        "a bare '5/m' was one GLOBAL bucket: five resets a minute across the whole site"
    assert 'confirm_email' not in limits, \
        "the '5/m' override loosened allauth's 1-per-3-min resend cooldown 15x"
    assert limits['signup'] == '5/m/ip'


def test_logout_redirect_carries_its_trailing_slash():
    assert dj_settings.ACCOUNT_LOGOUT_REDIRECT_URL == '/accounts/login/', \
        'the missing slash cost every allauth logout an APPEND_SLASH 301 hop'


def test_the_429_handler_is_declared_and_returns_429():
    from plat_pursuit.urls import handler429, ThrottledView  # noqa: F401  (declared, not incidental)
    from django.test import RequestFactory

    resp = ThrottledView.as_view()(RequestFactory().get('/'))
    resp.render()
    assert resp.status_code == 429


# ── no reachable route renders allauth's package templates ────────────────────────────────────

@pytest.mark.parametrize('name', [
    'account/login.html', 'account/signup.html', 'account/logout.html',
    'account/email.html', 'account/email_confirm.html', 'account/verification_sent.html',
    'account/password_reset.html', 'account/password_reset_done.html',
    'account/password_reset_from_key.html', 'account/password_reset_from_key_done.html',
    'account/link_psn.html',
])
def test_every_reachable_account_template_is_ours(name):
    origin = get_template(name).origin.name

    assert 'site-packages' not in origin, \
        f'{name} resolves to the raw allauth package template (white page on a dark site)'


# ── Phase B: login + signup rebuild ───────────────────────────────────────────────────────────

def _make_verified(password='phase-b-pass-9'):
    from allauth.account.models import EmailAddress
    profile = ProfileFactory(is_linked=True)
    user = profile.user
    user.set_password(password)
    user.save()
    EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
    return user, password


def test_login_preserves_next_through_the_post(client):
    """The ?next= contract is explicit now (a hidden input), not an accident of the empty
    form action re-posting the query string."""
    user, password = _make_verified()

    page = client.get('/accounts/login/?next=/support/').content.decode()
    assert 'name="next" value="/support/"' in page

    resp = client.post('/accounts/login/?next=/support/',
                       {'login': user.email, 'password': password, 'next': '/support/'})
    assert resp.status_code == 302
    assert resp['Location'] == '/support/'


def test_signup_page_renders_with_one_h1_and_the_honeypot(client):
    body = client.get('/accounts/signup/').content.decode()

    assert body.count('<h1') == 1, 'h1 discipline: the page title, not the brand mark'
    assert 'name="website"' in body, 'the honeypot must survive every restyle'
    assert 'pp-head-cascade' in body


def test_login_page_renders_with_one_h1(client):
    body = client.get('/accounts/login/').content.decode()

    assert body.count('<h1') == 1
    assert 'pp-head-cascade' in body


def test_the_honeypot_rejects_bots_that_fill_every_field(client):
    from users.models import CustomUser

    resp = client.post('/accounts/signup/', {
        'email': 'bot@example.com', 'email2': 'bot@example.com',
        'password1': 'a-perfectly-fine-pass-1', 'password2': 'a-perfectly-fine-pass-1',
        'website': 'https://spam.example',
    })

    assert resp.status_code == 200, 'form re-renders with the error, no account'
    assert not CustomUser.objects.filter(email='bot@example.com').exists()


def test_a_human_signup_still_works(client):
    from users.models import CustomUser

    resp = client.post('/accounts/signup/', {
        'email': 'human@example.com', 'email2': 'human@example.com',
        'password1': 'a-perfectly-fine-pass-1', 'password2': 'a-perfectly-fine-pass-1',
        'website': '',
    })

    assert resp.status_code == 302
    assert CustomUser.objects.filter(email='human@example.com').exists()
