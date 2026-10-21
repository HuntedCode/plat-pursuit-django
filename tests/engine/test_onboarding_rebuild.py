"""The onboarding rebuild (fix plan branch 5, docs/design/reviews/onboarding-rebuild.md).

One auth shell with focused chrome (FV-01), one email and one password at sign up (FV-07), a "Check your
inbox" that names the address and can resend or undo it (FV-08), link PSN's step 2 that refreshes an
expired code and re-checks ownership (FV-20), house fields, and a sync card that is not a warning (FV-13).
"""
import re
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from allauth.account.models import EmailAddress
from django.core.cache import cache
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from tests.factories import ProfileFactory
from users.models import CustomUser
from users.services import pending_signup

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = 'a-perfectly-fine-pass-1'


def _sign_up(client, email):
    return client.post('/accounts/signup/', {'email': email, 'password1': PASSWORD, 'website': ''})


def _main(body):
    """The page's <main>, so an assertion cannot match the navbar or a hidden template."""
    return body[body.index('<main'):body.index('</main>')]


# ── one shell, focused chrome ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('url', ['/accounts/signup/', '/accounts/login/', '/accounts/password/reset/',
                                 '/accounts/confirm-email/'])
def test_auth_pages_wear_the_shell_without_footer_or_tab_bar(client, url):
    body = client.get(url).content.decode()

    assert 'class="auth-card' in body
    assert 'role="contentinfo"' not in body, 'the site footer is back on a focused auth page'
    assert 'class="mobile-tabbar' not in body, 'the phone tab bar is back on a focused auth page'
    assert reverse('privacy') in _main(body) and reverse('terms') in _main(body), \
        'the legal links the footer carried must stay reachable under the card'


def test_other_pages_keep_the_footer_and_tab_bar(client):
    body = client.get(reverse('games_list')).content.decode()

    assert 'role="contentinfo"' in body
    assert 'class="mobile-tabbar' in body


def test_the_old_generation_is_gone_from_the_auth_family(client):
    """FV-01's markers: the yellow tagline, DaisyUI buttons and salmon alert slabs."""
    for url in ('/accounts/signup/', '/accounts/login/', '/accounts/password/reset/', '/accounts/confirm-email/'):
        main = _main(client.get(url).content.decode())
        assert 'No Trophy Can Hide From Us' not in main, url
        assert not re.search(r'class="[^"]*\b(btn|alert|input-bordered)\b', main), url


# ── sign up: one email, one password (FV-07) ──────────────────────────────────────────────────

def test_sign_up_asks_for_one_email_and_one_password(client):
    main = _main(client.get('/accounts/signup/').content.decode())

    assert 'name="email"' in main and 'name="password1"' in main
    assert 'name="email2"' not in main and 'name="password2"' not in main
    assert 'cool.guy@platpursuit.com' not in main, 'a placeholder that reads as a typed value (FV-07)'
    assert 'Next: confirm your email, then link your PSN.' in main


def test_a_sign_up_with_one_of_each_makes_the_account(client, mailoutbox):
    resp = _sign_up(client, 'single@example.com')

    assert resp.status_code == 302
    assert CustomUser.objects.filter(email='single@example.com').exists()
    assert len(mailoutbox) == 1


def test_a_failed_sign_up_marks_the_field_and_lists_each_error(client):
    """Each error gets its own line (a short password that also resembles the email fails twice), and the
    field is marked invalid by the attribute that draws it."""
    main = _main(client.post('/accounts/signup/', {
        'email': 'shorty@example.com', 'password1': 'shorty', 'website': ''}).content.decode())

    field = main[main.index('id="id_password1"'):]
    assert 'aria-invalid="true"' in main[main.index('id="id_password1"') - 400:main.index('id="id_password1"') + 400]
    errors = field[field.index('id="id_password1-error"'):field.index('</div>', field.index('id="id_password1-error"'))]
    assert errors.count('class="pp-field__error"') >= 2
    assert 'value="shorty@example.com"' in main, 'the typed email must survive'
    assert 'shorty"' not in main.replace('shorty@', ''), 'a password must never be echoed back'


# ── Check your inbox (FV-08) ──────────────────────────────────────────────────────────────────

def test_check_your_inbox_names_the_address_and_offers_the_fix(client):
    _sign_up(client, 'typo@exmaple.com')

    main = _main(client.get(reverse('account_email_verification_sent')).content.decode())

    assert '<b>typo@exmaple.com</b>' in main
    wait = int(re.search(r'data-resend-wait="(\d+)"', main).group(1))
    assert wait > 0, 'right after a send, the page knows allauth\'s limit is running'
    assert ' disabled' not in main[main.index('data-resend-wait'):main.index('</button>', main.index('data-resend-wait'))], \
        'the button renders live (JS holds it for the wait), so it still works without JS'
    assert reverse('account_verification_fix') in main


def test_a_visitor_with_no_sign_up_gets_the_generic_page(client):
    main = _main(client.get(reverse('account_email_verification_sent')).content.decode())

    assert 'We sent you a confirmation link' in main
    assert reverse('account_verification_resend') not in main
    assert reverse('account_verification_fix') not in main


def test_resend_inside_the_limit_sends_nothing_and_says_why(client, mailoutbox):
    _sign_up(client, 'eager@example.com')

    resp = client.post(reverse('account_verification_resend'), follow=True)

    assert len(mailoutbox) == 1, 'allauth\'s one-per-three-minutes limit still holds'
    assert 'You can send another in a few minutes' in resp.content.decode()


def test_resend_after_the_limit_sends_a_second_link(client, mailoutbox):
    _sign_up(client, 'patient@example.com')
    cache.clear()   # allauth's limiter history: as if three minutes had passed

    resp = client.post(reverse('account_verification_resend'), follow=True)

    assert len(mailoutbox) == 2
    assert mailoutbox[1].to == ['patient@example.com']
    assert 'The new link is on its way' in resp.content.decode()


def test_resend_for_a_confirmed_address_goes_to_sign_in(client, mailoutbox):
    _sign_up(client, 'done@example.com')
    EmailAddress.objects.filter(email='done@example.com').update(verified=True)

    resp = client.post(reverse('account_verification_resend'))

    assert resp['Location'] == reverse('account_login')
    assert len(mailoutbox) == 1


def test_resend_without_a_sign_up_goes_to_sign_up(client):
    resp = client.post(reverse('account_verification_resend'))

    assert resp['Location'] == reverse('account_signup')


def test_the_actions_are_post_only(client):
    for name in ('account_verification_resend', 'account_verification_fix'):
        assert client.get(reverse(name)).status_code == 405, name


def test_fix_it_removes_the_mistyped_account_and_prefills_sign_up(client):
    _sign_up(client, 'typo@exmaple.com')

    resp = client.post(reverse('account_verification_fix'))

    assert not CustomUser.objects.filter(email='typo@exmaple.com').exists()
    assert resp['Location'] == reverse('account_signup') + '?email=typo%40exmaple.com'
    page = client.get(resp['Location']).content.decode()
    assert 'value="typo@exmaple.com"' in page
    assert pending_signup.SESSION_KEY not in client.session
    assert 'account_login' not in client.session, 'allauth\'s pending login for the deleted user must go too'


def test_fix_it_cannot_touch_another_browsers_sign_up(client):
    """The actions read the session, never the request: a second browser has nothing to remove."""
    from django.test import Client
    _sign_up(client, 'mine@example.com')

    Client().post(reverse('account_verification_fix'))

    assert CustomUser.objects.filter(email='mine@example.com').exists()


@pytest.mark.parametrize('used', ['verified', 'signed_in', 'staff'])
def test_fix_it_never_removes_an_account_that_was_used(client, used):
    _sign_up(client, 'real@example.com')
    user = CustomUser.objects.get(email='real@example.com')
    if used == 'verified':
        EmailAddress.objects.filter(user=user).update(verified=True)
    elif used == 'signed_in':
        CustomUser.objects.filter(pk=user.pk).update(last_login=timezone.now())
    else:
        CustomUser.objects.filter(pk=user.pk).update(is_staff=True)

    resp = client.post(reverse('account_verification_fix'))

    assert CustomUser.objects.filter(pk=user.pk).exists()
    assert resp['Location'] == reverse('account_email_verification_sent')


def test_a_resend_keeps_fix_it_available(client):
    _sign_up(client, 'twice@example.com')
    cache.clear()
    client.post(reverse('account_verification_resend'))

    assert client.session[pending_signup.SESSION_KEY]['created'] is True


def test_a_staff_resend_does_not_land_in_the_staff_session(rf):
    """The admin can resend a confirmation; that must not write a pending sign-up into the staff member's own
    session (their "Check your inbox" would then offer to delete someone else's account)."""
    from django.contrib.sessions.backends.db import SessionStore
    from users.adapters import CustomAccountAdapter
    target = CustomUser.objects.create_user(email='member@example.com', username='member', password=PASSWORD)
    address = EmailAddress.objects.create(user=target, email=target.email, verified=False, primary=True)
    staff = CustomUser.objects.create_user(email='staff@example.com', username='staffer', password=PASSWORD, is_staff=True)
    request = rf.get('/admin/')
    request.user, request.session = staff, SessionStore()

    from allauth.account.models import EmailConfirmationHMAC
    CustomAccountAdapter().send_confirmation_mail(request, EmailConfirmationHMAC(address), signup=False)

    assert pending_signup.SESSION_KEY not in request.session


def test_the_wait_counts_down_from_the_last_send():
    session = {pending_signup.SESSION_KEY: {'user_id': 1, 'email': 'a@b.c', 'created': True, 'sent_at': 1000.0}}
    request = SimpleNamespace(session=session)

    assert pending_signup.resend_wait(request, now=1000.0) == pending_signup.resend_cooldown_seconds()
    assert pending_signup.resend_wait(request, now=1100.5) == 80
    assert pending_signup.resend_wait(request, now=1180.0) == 0
    assert pending_signup.resend_wait(SimpleNamespace(session={}), now=0) == 0


# ── link PSN: step 2 (FV-09, FV-20) ───────────────────────────────────────────────────────────

def _unlinked_client(client):
    profile = ProfileFactory(is_linked=True)
    user = profile.user
    profile.unlink_user()
    client.force_login(user)
    return client, user


def test_returning_to_step_two_refreshes_an_expired_code(client, monkeypatch):
    """FV-20: every return to step 2 showed the stored code as-is, even after its hour ran out, so the
    hunter pasted a code that could never pass."""
    client, _ = _unlinked_client(client)
    target = ProfileFactory(user=None, is_linked=False)
    old = target.generate_verification_code()
    type(target).objects.filter(pk=target.pk).update(verification_expires_at=timezone.now() - timedelta(minutes=1))
    monkeypatch.setattr('trophies.views.profile_views.redis_client.get',
                        lambda key: b'1' if key == 'site:psn_outage' else None)

    body = client.post(reverse('link_psn'), {'action': 'verify', 'psn_username': target.psn_username}).content.decode()

    target.refresh_from_db()
    assert target.verification_code != old
    assert target.verification_expires_at > timezone.now()
    assert f'>{target.verification_code}</code>' in body


def test_returning_to_step_two_keeps_a_live_code(client, monkeypatch):
    client, _ = _unlinked_client(client)
    target = ProfileFactory(user=None, is_linked=False)
    code = target.generate_verification_code()
    monkeypatch.setattr('trophies.views.profile_views.redis_client.get',
                        lambda key: b'1' if key == 'site:psn_outage' else None)

    client.post(reverse('link_psn'), {'action': 'verify', 'psn_username': target.psn_username})

    target.refresh_from_db()
    assert target.verification_code == code, 'a code still inside its hour must not change under the hunter'


def test_returning_to_step_two_for_a_profile_someone_else_linked_starts_over(client, monkeypatch):
    """FV-20's second half: the return never re-checked ownership."""
    client, _ = _unlinked_client(client)
    taken = ProfileFactory(is_linked=True)
    taken.generate_verification_code()
    monkeypatch.setattr('trophies.views.profile_views.redis_client.get',
                        lambda key: b'1' if key == 'site:psn_outage' else None)

    resp = client.post(reverse('link_psn'), {'action': 'verify', 'psn_username': taken.psn_username}, follow=True)

    body = resp.content.decode()
    assert 'already linked to another user' in body
    assert f'>{taken.verification_code}</code>' not in body, 'someone else\'s code must not be shown'


def test_step_two_says_how_long_the_code_lasts_and_where_to_put_it(client):
    client, _ = _unlinked_client(client)

    body = client.post(reverse('link_psn'), {'action': 'submit_username', 'psn_username': 'freshhunter02'}).content.decode()

    main = _main(body)
    assert 'Works for the next' in main
    assert 'Edit Profile' in main and 'About' in main
    assert 'Who can see your gaming history' in main, 'step 2 must say where the privacy setting lives (FV-09)'


# ── house fields ──────────────────────────────────────────────────────────────────────────────

def test_the_field_partial_never_echoes_a_password():
    from django import forms

    class F(forms.Form):
        secret = forms.CharField()

    html = render_to_string('partials/forms/_field.html',
                            {'field': F({'secret': 'hunter2'})['secret'], 'label': 'Secret', 'type': 'password'})

    assert 'hunter2' not in html
    assert 'data-password-toggle="id_secret"' in html


def test_the_field_partial_ties_hint_and_error_to_the_control():
    from django import forms

    class F(forms.Form):
        name = forms.CharField(min_length=5)

    html = render_to_string('partials/forms/_field.html',
                            {'field': F({'name': 'abc'})['name'], 'label': 'Name', 'hint': 'Five or more'})

    assert 'aria-describedby="id_name-hint id_name-error"' in html
    assert 'aria-invalid="true"' in html
    assert 'value="abc"' in html


# ── the sync card (FV-01, FV-13) ──────────────────────────────────────────────────────────────

def _syncing_body(client, sync_status='syncing'):
    profile = ProfileFactory(is_linked=True, sync_status=sync_status)
    client.force_login(profile.user)
    return client.get('/').content.decode()


def test_a_healthy_first_sync_is_not_dressed_as_a_warning(client):
    body = _syncing_body(client)
    # From the card's own opening tag, so its class attribute is inside the slice.
    card = body[body.index('<div class="sync-card'):body.index('data-sync-complete')]

    assert 'warning' not in card, 'a healthy first sync wears the primary tone (FV-01)'
    assert card.startswith('<div class="sync-card" data-sync-card>'), 'a healthy sync carries no tone modifier'
    assert 'Usually 10 to 30 minutes.' in card, 'the time it takes leads (FV-13)'
    assert 'class="pp-progress"' in card


def test_a_failed_sync_wears_the_error_tone(client):
    body = _syncing_body(client, sync_status='error')

    assert 'class="sync-card sync-card--error"' in body
    assert 'We hit a snag' in body


# ── the audit's findings, pinned ──────────────────────────────────────────────────────────────

def test_step_three_starts_with_the_timeout_hidden(client, monkeypatch):
    """The audit's HIGH: `class="hidden auth-form"` lost to `.auth-form`'s display rule (unlayered beats
    Tailwind's layered utility), so "Verification timed out" showed from the first second of polling."""
    client, _ = _unlinked_client(client)
    target = ProfileFactory(user=None, is_linked=False)
    target.generate_verification_code()
    monkeypatch.setattr('trophies.views.profile_views.VerificationService.start_verification_sync', lambda p: None)

    main = _main(client.post(reverse('link_psn'), {'action': 'verify', 'psn_username': target.psn_username}).content.decode())

    tag = main[main.index('<div id="poll-timeout"'):main.index('>', main.index('<div id="poll-timeout"'))]
    assert re.search(r'\shidden(\s|$)', tag), f'the timeout block must start hidden by attribute: {tag}'
    assert 'timeoutBlock.hidden = false' in (ROOT / 'static/js/link-psn.js').read_text(encoding='utf-8')


def test_verifying_an_expired_code_issues_a_new_one_instead_of_syncing(client, monkeypatch):
    """The main verify path ran a whole verification sync with a code that could no longer pass."""
    client, _ = _unlinked_client(client)
    target = ProfileFactory(user=None, is_linked=False)
    old = target.generate_verification_code()
    type(target).objects.filter(pk=target.pk).update(verification_expires_at=timezone.now() - timedelta(minutes=1))

    # Recorded, not raised: the view's catch-all would swallow an exception and fall back to step 2 anyway.
    synced = []
    monkeypatch.setattr('trophies.views.profile_views.VerificationService.start_verification_sync', synced.append)

    body = client.post(reverse('link_psn'), {'action': 'verify', 'psn_username': target.psn_username}).content.decode()

    assert synced == [], 'a dead code must not start a verification sync'
    target.refresh_from_db()
    assert target.verification_code != old
    assert f'>{target.verification_code}</code>' in body
    assert 'Your code ran out, so here is a new one' in body


def test_re_signing_up_with_the_same_address_after_fix_it_sends_at_once(client, mailoutbox):
    """Fix it, then "oh, it was right": allauth's per-address cooldown survived the delete and skipped the new
    account's first email, so the page claimed a send that never happened."""
    _sign_up(client, 'right@example.com')
    client.post(reverse('account_verification_fix'))

    _sign_up(client, 'right@example.com')

    assert len(mailoutbox) == 2
    assert client.session[pending_signup.SESSION_KEY]['email'] == 'right@example.com'


def test_the_page_knows_the_address_even_when_the_send_was_skipped(client, mailoutbox, monkeypatch):
    monkeypatch.setattr('allauth.account.internal.flows.email_verification.handle_verification_email_rate_limit',
                        lambda request, email, raise_exception=False: False)

    _sign_up(client, 'limited@example.com')

    assert len(mailoutbox) == 0
    main = _main(client.get(reverse('account_email_verification_sent')).content.decode())
    assert '<b>limited@example.com</b>' in main
    assert 'data-resend-wait="0"' in main, 'nothing went, so there is nothing to wait out'


def test_a_resend_shows_one_notice(client):
    _sign_up(client, 'once@example.com')
    cache.clear()

    main = _main(client.post(reverse('account_verification_resend'), follow=True).content.decode())

    assert main.count('class="pp-notice pp-notice--') == 1, 'allauth\'s own "Confirmation email sent" doubled every resend'


def test_a_sign_in_record_never_offers_or_allows_fix_it(client, mailoutbox):
    """Signing in to an unconfirmed account from another browser records the address (created=False): that
    browser did not make the account, so it may resend but never delete."""
    from django.test import Client
    _sign_up(client, 'elsewhere@example.com')
    other = Client()
    cache.clear()
    other.post('/accounts/login/', {'login': 'elsewhere@example.com', 'password': PASSWORD})

    record = other.session[pending_signup.SESSION_KEY]
    assert record['created'] is False
    assert reverse('account_verification_fix') not in _main(other.get(reverse('account_email_verification_sent')).content.decode())
    other.post(reverse('account_verification_fix'))
    assert CustomUser.objects.filter(email='elsewhere@example.com').exists()


def test_resend_for_an_account_that_is_gone_starts_over(client):
    _sign_up(client, 'gone@example.com')
    CustomUser.objects.filter(email='gone@example.com').delete()

    resp = client.post(reverse('account_verification_resend'))

    assert resp['Location'] == reverse('account_signup'), 'it used to say the address was already confirmed'
    assert pending_signup.SESSION_KEY not in client.session


def test_signing_in_ends_check_your_inbox(client, mailoutbox):
    _sign_up(client, 'confirming@example.com')
    link = re.search(r'/accounts/confirm-email/[^\s"\']+/', mailoutbox[0].body).group(0)

    client.get(link, follow=True)

    assert pending_signup.SESSION_KEY not in client.session, 'a stale address would show on a later visit'


def test_the_wait_is_allauths_configured_cooldown(settings):
    assert pending_signup.resend_cooldown_seconds() == 180
    settings.ACCOUNT_EMAIL_CONFIRMATION_COOLDOWN = 60
    assert pending_signup.resend_cooldown_seconds() == 60


def test_the_auth_scripts_keep_their_contracts():
    """No JS runner here (JS is pinned by source text): the resend countdown holds the LIVE button for the wait,
    and the strength meter goes inside the field, under the box."""
    js = (ROOT / 'static/js/auth-forms.js').read_text(encoding='utf-8')
    countdown = js[js.index("querySelectorAll('[data-resend-wait]')"):]
    assert 'button.disabled = true' in countdown and 'button.disabled = false' in countdown
    assert "(input.closest('.pp-input-wrap') || input).after(meter)" in js
    assert "field.closest('.pp-input-wrap')" in js, 'the reveal button lives in the wrap'


# ── the second audit's findings, pinned ───────────────────────────────────────────────────────

def test_manage_email_addresses_still_says_the_link_went(client, mailoutbox):
    """Dropping allauth's "Confirmation email sent" for visitors must not silence it for a signed-in hunter: on
    Manage email addresses it is the only word that the re-send worked."""
    profile = ProfileFactory(is_linked=True)
    EmailAddress.objects.create(user=profile.user, email=profile.user.email, verified=True, primary=True)
    EmailAddress.objects.create(user=profile.user, email='second@example.com', verified=False, primary=False)
    client.force_login(profile.user)

    body = client.post('/accounts/email/', {'action_send': '', 'email': 'second@example.com'}, follow=True).content.decode()

    assert len(mailoutbox) == 1
    assert 'Confirmation email sent to second@example.com' in body


def test_no_cooldown_means_no_wait(settings):
    settings.ACCOUNT_EMAIL_CONFIRMATION_COOLDOWN = 0

    assert pending_signup.resend_cooldown_seconds() == 0


def test_signing_in_to_another_unconfirmed_account_takes_over_the_page(client, mailoutbox):
    """Account A signed up here; then a sign-in to unconfirmed account B while B's email is still rate-limited
    (no send, so no stamp). The page kept naming A and offered to delete it."""
    from django.test import Client
    _sign_up(client, 'first@example.com')
    _sign_up(Client(), 'second-account@example.com')   # B's email just went: its cooldown is running

    client.post('/accounts/login/', {'login': 'second-account@example.com', 'password': PASSWORD})

    record = client.session[pending_signup.SESSION_KEY]
    assert record['email'] == 'second-account@example.com' and record['created'] is False
    main = _main(client.get(reverse('account_email_verification_sent')).content.decode())
    assert '<b>second-account@example.com</b>' in main
    assert reverse('account_verification_fix') not in main


def test_verifying_a_profile_someone_else_linked_never_syncs(client, monkeypatch):
    """The main verify path's ownership check (the outage path is pinned above)."""
    client, _ = _unlinked_client(client)
    taken = ProfileFactory(is_linked=True)
    taken.generate_verification_code()
    synced = []
    monkeypatch.setattr('trophies.views.profile_views.VerificationService.start_verification_sync', synced.append)

    resp = client.post(reverse('link_psn'), {'action': 'verify', 'psn_username': taken.psn_username}, follow=True)

    assert synced == []
    assert 'already linked to another user' in resp.content.decode()

