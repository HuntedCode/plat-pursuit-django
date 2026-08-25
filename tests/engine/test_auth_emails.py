"""The two auth emails (signup confirmation + password reset) on email base v2.

These are the highest-consequence emails the site sends: one grants account access, the other
grants account recovery. Both live or die on a rule that no rendering test would notice, so it
is pinned here explicitly: the text/plain part is strip_tags(html), which discards every href,
so the destination URL must ALSO appear as visible text or a plaintext reader is stranded.

test_auth_pages.py pins that for the confirmation link through the real signup flow. The reset
link had no such pin before this file, which is exactly how a plaintext reset email with no
reachable link could have shipped silently.
"""
from django.template.loader import render_to_string
from django.utils.html import strip_tags

CONFIRM_URL = 'https://platpursuit.com/accounts/confirm-email/Mjg6MXVyMlZ6OnRva2Vu/'
RESET_URL = 'https://platpursuit.com/accounts/password/reset/key/28-set-password/'


def _verification(**overrides):
    ctx = {
        'username': 'TestHunter',
        'activate_url': CONFIRM_URL,
        'site_url': 'https://platpursuit.com',
        'expiration_days': 3,
    }
    ctx.update(overrides)
    return render_to_string('emails/email_verification.html', ctx)


def _reset(**overrides):
    ctx = {
        'username': 'TestHunter',
        'password_reset_url': RESET_URL,
        'site_url': 'https://platpursuit.com',
    }
    ctx.update(overrides)
    return render_to_string('emails/password_reset.html', ctx)


def test_both_auth_emails_ride_the_new_base():
    for body in (_verification(), _reset()):
        assert 'role="presentation"' in body, 'the v2 table scaffold is missing'
        assert '#667eea' not in body, 'the pre-rebuild purple came back'
        assert 'v:roundrect' in body, 'the CTA lost its Outlook half'


def test_the_confirmation_url_survives_the_plaintext_strip():
    """The rule test_auth_pages.py depends on, pinned here at the template level too."""
    plain = strip_tags(_verification())

    assert CONFIRM_URL in plain


def test_the_reset_url_survives_the_plaintext_strip():
    """Previously unpinned: strip_tags keeps no hrefs, so a button-only reset email is a dead
    end for anyone reading in plaintext."""
    plain = strip_tags(_reset())

    assert RESET_URL in plain


def test_the_urls_are_never_split_by_markup():
    """The plaintext regex in test_auth_pages matches a run of non-whitespace. A wrapping span
    or a line break inside the URL would satisfy the visible-text rule while still breaking a
    copy-paste, so the URL must sit in one unbroken text node."""
    for body, url in ((_verification(), CONFIRM_URL), (_reset(), RESET_URL)):
        # The visible copy is the LAST occurrence (the first is the button href).
        assert body.count(url) >= 2, 'the URL must appear as both href and visible text'


def test_the_header_is_legible_on_the_dark_band():
    """v2's header band is near-black. A bare <h1> (what the legacy base styled for us) would
    render near-black on near-black, so the colour must be inline, not class-only."""
    for body in (_verification(), _reset()):
        assert 'color: #F0F6FD' in body


def test_the_auth_emails_keep_their_footer_and_have_no_em_dash():
    for body in (_verification(), _reset()):
        assert 'Manage your account settings' in body
        assert 'Manage your email preferences' not in body
        assert chr(8212) not in body
