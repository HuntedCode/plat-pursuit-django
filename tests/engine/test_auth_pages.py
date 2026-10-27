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
from trophies.models import Profile

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

    # A typed or bookmarked GET goes home and does NOT sign out: a GET that signs out is a CSRF-shaped
    # hole. It used to be Django's bare white 405 page (AC-09).
    resp = client.get('/logout/')
    assert resp.status_code == 302 and resp['Location'] == '/'
    assert '_auth_user_id' in client.session, 'a GET must never sign anybody out'

    resp = client.post('/logout/')
    assert resp.status_code == 200
    assert 'Signed out' in resp.content.decode()


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


def test_the_429_handler_is_declared_and_returns_429(rf):
    """allauth's own limits look `handler429` up on the ROOT_URLCONF (`respond_429`); Django never does."""
    from django.contrib.auth.models import AnonymousUser
    from django.contrib.sessions.backends.cache import SessionStore
    from django.utils.module_loading import import_string
    from plat_pursuit.urls import handler429

    request = rf.get('/')
    request.user, request.session = AnonymousUser(), SessionStore()
    assert import_string(handler429)(request).status_code == 429


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
        'email': 'bot@example.com', 'password1': 'a-perfectly-fine-pass-1',
        'website': 'https://spam.example',
    })

    assert resp.status_code == 200, 'form re-renders with the error, no account'
    assert not CustomUser.objects.filter(email='bot@example.com').exists()


def test_a_human_signup_still_works(client):
    from users.models import CustomUser

    resp = client.post('/accounts/signup/', {
        'email': 'human@example.com', 'password1': 'a-perfectly-fine-pass-1',
        'website': '',
    })

    assert resp.status_code == 302
    assert CustomUser.objects.filter(email='human@example.com').exists()


# ── Phases D + E: touch-ups + the created overrides ───────────────────────────────────────────

@pytest.mark.parametrize('url,h1_text', [
    ('/accounts/password/reset/', 'Forgot your password?'),
    ('/accounts/password/reset/done/', 'Reset link sent'),
    ('/accounts/password/reset/key/done/', 'Password updated'),
    ('/accounts/confirm-email/', 'Check your inbox'),
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
    assert 'Account inactive' in body
    assert 'Contact us' in body, 'the one moment a bare white page would be most alarming'


def _unlinked_client(client):
    profile = ProfileFactory(is_linked=True)
    user = profile.user
    profile.unlink_user()
    client.force_login(user)
    return client


def _step_one_form(body):
    """The step-1 form alone, so an assertion cannot match the same words elsewhere on the page."""
    start = body.index('value="submit_username"')
    return body[body.rindex('<form', 0, start):body.index('</form>', start)]


def _submit_link_name(client, name):
    return client.post(reverse('link_psn'), {'action': 'submit_username', 'psn_username': name})


def test_link_psn_step_one_renders_for_an_unlinked_user(client):
    body = _unlinked_client(client).get(reverse('link_psn')).content.decode()

    assert body.count('<h1') == 1
    assert 'Link your PSN account' in body
    assert 'pp-head-cascade' in body


@pytest.mark.parametrize('bad_name', ['a!', 'ab', 'bad name'])
def test_link_psn_step_one_shows_why_a_name_was_rejected(client, bad_name):
    """FV-02 (docs/design/reviews/first-visit.md): the view re-rendered step 1 with the bound
    form, but the template never printed its errors or the typed value, so an invalid name
    reloaded an empty form with no message at all."""
    resp = _submit_link_name(_unlinked_client(client), bad_name)

    assert resp.status_code == 200
    form = _step_one_form(resp.content.decode())
    assert 'must be 3-16 characters' in form
    assert 'role="alert"' in form
    assert 'aria-invalid="true"' in form
    assert f'value="{bad_name}"' in form, 'the typed name must survive the round trip'


def test_a_refilled_link_name_is_escaped(client):
    """The typed value is echoed back into an attribute, so it must stay autoescaped."""
    form = _step_one_form(_submit_link_name(_unlinked_client(client), 'a"><b>x').content.decode())

    assert 'value="a&quot;&gt;&lt;b&gt;x"' in form
    assert '<b>x' not in form


def test_an_over_long_link_name_is_reported_once(client):
    """The form's max_length and the model field's copied validators each carried a
    MaxLengthValidator, so the length error printed twice once errors were shown at all."""
    form = _step_one_form(_submit_link_name(_unlinked_client(client), 'a' * 17).content.decode())

    assert form.count('at most 16 characters') == 1


def test_a_rejected_link_name_creates_no_profile(client):
    """Validation must run before get_or_create: a rejected name must not leave a Profile row
    (or kick off a sync) behind."""
    client = _unlinked_client(client)
    before = Profile.objects.count()

    _submit_link_name(client, 'a!')

    assert Profile.objects.count() == before


def test_a_name_linked_to_someone_else_keeps_the_name_and_says_why(client):
    """The other step-1 re-render: the name is VALID, so the reason travels as a message, not a
    field error. The field keeps the name and must not be marked invalid."""
    taken = ProfileFactory(is_linked=True)
    client = _unlinked_client(client)

    body = _submit_link_name(client, taken.psn_username).content.decode()

    form = _step_one_form(body)
    assert f'value="{taken.psn_username}"' in form
    assert 'aria-invalid' not in form
    assert 'already linked to another user' in body


def _holding_a_code(client):
    """A signed-in hunter mid-link: an unclaimed profile that already holds a verification code."""
    client = _unlinked_client(client)
    target = ProfileFactory(user=None, is_linked=False)
    code = target.generate_verification_code()
    return client, target, code


def test_a_failed_verification_start_keeps_the_code_on_screen(client, monkeypatch):
    """FV-19: the generic error path re-rendered step 2 without `verification_code` or `profile`,
    so the hunter was asked to paste a code into About Me from an empty box."""
    client, target, code = _holding_a_code(client)

    def boom(profile):
        raise RuntimeError('PSN hiccup')
    monkeypatch.setattr('trophies.views.profile_views.VerificationService.start_verification_sync', boom)

    body = client.post(reverse('link_psn'), {'action': 'verify', 'psn_username': target.psn_username}).content.decode()

    assert 'Verify your account' in body
    assert f'>{code}</code>' in body
    assert 'An error occurred during verification' in body


def test_verifying_during_a_psn_outage_keeps_the_code_on_screen(client, monkeypatch):
    """The outage path shares the step-two re-render with FV-19's fix; pin that it still works."""
    client, target, code = _holding_a_code(client)
    monkeypatch.setattr('trophies.views.profile_views.redis_client.get',
                        lambda key: b'1' if key == 'site:psn_outage' else None)

    body = client.post(reverse('link_psn'), {'action': 'verify', 'psn_username': target.psn_username}).content.decode()

    assert f'>{code}</code>' in body
    assert 'PlayStation Network is currently unavailable' in body


def test_a_failed_first_sync_says_so_in_plain_english(client, monkeypatch):
    """The step-one sync failure message read "An error occured" (FV-19's typo)."""
    def boom(profile):
        raise RuntimeError('PSN hiccup')
    monkeypatch.setattr('trophies.views.profile_views.PSNManager.initial_sync', boom)

    body = _submit_link_name(_unlinked_client(client), 'freshhunter01').content.decode()

    assert 'An error occurred during sync' in body
    assert 'occured' not in body
    assert 'value="freshhunter01"' in _step_one_form(body)


def test_link_psn_step_one_starts_clean(client):
    """The unbound form's value is None; it must render as an empty field, never the text "None",
    and an untouched form carries no error state."""
    form = _step_one_form(_unlinked_client(client).get(reverse('link_psn')).content.decode())

    assert 'value=""' in form
    assert 'value="None"' not in form
    assert 'aria-invalid' not in form
    assert 'role="alert"' not in form


# ── the audit's findings, pinned ──────────────────────────────────────────────────────────────

def test_confirming_an_email_logs_the_new_hunter_in(client, mailoutbox):
    """THE regression pin: ACCOUNT_LOGIN_ON_EMAIL_CONFIRMATION is the live setting in allauth
    65.x (the *_AUTO_LOGIN spelling reads as nothing there). With it off, every fresh signup
    was asked to re-type the password they chose a minute ago. The full real flow, because
    allauth only auto-logs-in when the link is opened in the SAME session as the signup."""
    import re as _re

    client.post('/accounts/signup/', {
        'email': 'fresh@example.com', 'password1': 'a-perfectly-fine-pass-1',
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


# ── QoL: failed attempts keep your typing; remember-me verified real ──────────────────────────

def test_a_wrong_password_keeps_the_typed_email(client):
    """The page is a classic form POST, so the reload is inherent -- but the email input had
    no value binding, so a typo in the password also cost you your email."""
    user, password = _make_verified()

    resp = client.post('/accounts/login/', {'login': user.email, 'password': 'not-it'})

    assert resp.status_code == 200
    assert f'value="{user.email}"' in resp.content.decode(), 'the email must survive the failure'


def test_a_failed_signup_keeps_the_typed_email(client):
    resp = client.post('/accounts/signup/', {
        'email': 'keep.me@example.com', 'password1': 'short', 'website': '',
    })

    assert resp.status_code == 200
    assert resp.content.decode().count('value="keep.me@example.com"') == 1


def test_remember_me_actually_remembers_and_forgetting_actually_forgets(client):
    """ACCOUNT_SESSION_REMEMBER is unset, so allauth honours the checkbox exactly: checked =
    a persistent session (SESSION_COOKIE_AGE), unchecked = the session DIES with the browser.
    Both directions verified so 'does it even do anything' has a pinned answer: yes."""
    user, password = _make_verified()

    client.post('/accounts/login/', {'login': user.email, 'password': password})
    assert client.session.get_expire_at_browser_close(), \
        'without remember, the session must end when the browser closes'

    client.post('/logout/')
    client.post('/accounts/login/', {'login': user.email, 'password': password, 'remember': 'on'})
    assert not client.session.get_expire_at_browser_close()
    assert client.session.get_expiry_age() > 60 * 60 * 24, 'remembered sessions persist for days'
