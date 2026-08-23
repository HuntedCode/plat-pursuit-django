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


def test_django_logout_redirect_stays_unset():
    """Deliberately load-bearing: an unset LOGOUT_REDIRECT_URL is what makes POST /logout/
    render the Signed Out page instead of redirecting. (allauth's own redirect setting was
    deleted as inert -- its LogoutView is shadowed, so nothing read it.)"""
    assert dj_settings.LOGOUT_REDIRECT_URL is None


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
    'account/link_psn.html', 'account/account_inactive.html', 'account/reauthenticate.html',
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


# ── Phases D + E: touch-ups + the created overrides ───────────────────────────────────────────

@pytest.mark.parametrize('url,h1_text', [
    ('/accounts/password/reset/', 'Forgot Password?'),
    ('/accounts/password/reset/done/', 'Reset Link Sent'),
    ('/accounts/password/reset/key/done/', 'Password Reset Complete'),
    ('/accounts/confirm-email/', 'Check Your Inbox'),
])
def test_touched_up_pages_carry_cascade_and_one_h1(client, url, h1_text):
    body = client.get(url).content.decode()

    assert 'pp-head-cascade' in body
    assert body.count('<h1') == 1
    assert h1_text in body


def test_no_sub_aa_dimming_survives_in_the_family():
    """text-base-content/40 and /50 sit below the AA floor --pp-text-mute was raised to;
    the family floor is /60."""
    root = ROOT / 'templates' / 'account'
    for f in sorted(root.glob('*.html')):
        src = f.read_text(encoding='utf-8')
        assert 'text-base-content/40' not in src, f'{f.name} dims below the family floor'
        assert 'text-base-content/50' not in src, f'{f.name} dims below the family floor'


def test_account_inactive_wears_house_chrome(client):
    profile = ProfileFactory(is_linked=True)
    user = profile.user
    user.set_password('inactive-pass-1')
    user.is_active = False
    user.save()
    from allauth.account.models import EmailAddress
    EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)

    resp = client.post('/accounts/login/', {'login': user.email, 'password': 'inactive-pass-1'},
                       follow=True)

    body = resp.content.decode()
    assert 'Account Inactive' in body
    assert 'Contact Us' in body, 'the one moment a bare white page would be most alarming'


def test_link_psn_step_one_renders_for_an_unlinked_user(client):
    profile = ProfileFactory(is_linked=True)
    user = profile.user
    profile.unlink_user()
    client.force_login(user)

    body = client.get(reverse('link_psn')).content.decode()

    assert body.count('<h1') == 1
    assert 'Link Your PSN Account' in body
    assert 'pp-head-cascade' in body


# ── the audit's findings, pinned ──────────────────────────────────────────────────────────────

def test_confirming_an_email_logs_the_new_hunter_in(client, mailoutbox):
    """THE regression pin: ACCOUNT_LOGIN_ON_EMAIL_CONFIRMATION is the live setting in allauth
    65.x (the *_AUTO_LOGIN spelling reads as nothing there). With it off, every fresh signup
    was asked to re-type the password they chose a minute ago. The full real flow, because
    allauth only auto-logs-in when the link is opened in the SAME session as the signup."""
    import re as _re

    client.post('/accounts/signup/', {
        'email': 'fresh@example.com', 'email2': 'fresh@example.com',
        'password1': 'a-perfectly-fine-pass-1', 'password2': 'a-perfectly-fine-pass-1',
        'website': '',
    })
    assert len(mailoutbox) == 1, 'the verification email must send'
    match = _re.search(r'/accounts/confirm-email/[^\s"\']+/', mailoutbox[0].body)
    assert match, 'no confirmation link in the email body'

    resp = client.get(match.group(0), follow=True)

    assert resp.status_code == 200
    assert resp.wsgi_request.user.is_authenticated, \
        'the confirmation link must log the new hunter in, not bounce them to the login form'


def test_titles_page_sends_anonymous_visitors_to_a_real_login(client):
    """login_url was the hardcoded string '/login/' -- not a route, so anonymous visitors
    302d into a 404 (the string twin of the LinkPSN reverse-name bug)."""
    resp = client.get('/titles/')

    assert resp.status_code == 302
    assert resp['Location'].startswith('/accounts/login/')


def test_password_set_url_hands_off_to_settings_too(client):
    """For a user with an UNUSABLE password allauth would render its raw package template;
    Settings owns password management either way."""
    profile = ProfileFactory(is_linked=True)
    client.force_login(profile.user)

    resp = client.get('/accounts/password/set/')

    assert resp.status_code == 302
    assert resp['Location'] == reverse('settings')
