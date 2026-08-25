"""The two auth emails (signup confirmation + password reset) on email base v2.

These are the highest-consequence emails the site sends: one grants account access, the other
grants account recovery. Both live or die on a rule that no rendering test would notice, so it
is pinned here explicitly: the text/plain part is strip_tags(html), which discards every href,
so the destination URL must ALSO appear as visible text or a plaintext reader is stranded.

test_auth_pages.py pins that for the confirmation link through the real signup flow. The reset
link had no such pin before this file, which is exactly how a plaintext reset email with no
reachable link could have shipped silently.
"""
import html as _html
import re

from django.template.loader import render_to_string
from django.utils.html import strip_tags


def _plain(body):
    """What a plaintext reader actually receives (EmailService's pipeline)."""
    return _html.unescape(strip_tags(re.sub(r'(?is)<(style|script)[^>]*>.*?</\1>', ' ', body)))

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


def test_exactly_one_reachable_url_reaches_the_plaintext_reader():
    """Audit-corrected: the old version counted occurrences in the HTML, which the CTA partial
    satisfies on its own (VML href + anchor href) -- the whole visible panel could have been
    deleted and it would still have passed. Assert on the PLAINTEXT instead, where every href
    has been stripped, so the only survivor is the visible copy. Catches deletion, splitting,
    duplication and trailing punctuation in one line."""
    for body, url in ((_verification(), CONFIRM_URL), (_reset(), RESET_URL)):
        assert re.findall(r'https?://\S+', _plain(body)) == [url]


def test_the_header_is_legible_on_the_dark_band():
    """v2's header band is near-black. A bare <h1> (what the legacy base styled for us) would
    render near-black on near-black, so the colour must be INLINE, not class-only.

    Audit-corrected: the old version searched the whole body for the colour, which base v2
    itself emits three times regardless of the child -- it passed even against a naked <h1>.
    Anchor the assertion to the child's own headline tag."""
    for body in (_verification(), _reset()):
        assert re.search(r'<h1[^>]*style="[^"]*color: #F0F6FD', body), 'headline colour is class-only'


def test_the_auth_emails_keep_their_footer_and_have_no_em_dash():
    for body in (_verification(), _reset()):
        assert 'Manage your account settings' in body
        assert 'Manage your email preferences' not in body
        assert chr(8212) not in body


def test_an_empty_reset_url_never_ships_a_button_to_nowhere():
    """The adapter reads the URL out of allauth's context with an '' default, so this case is
    reachable. It used to render a dead button plus a panel saying "paste this into your
    browser" followed by nothing."""
    body = _reset(password_reset_url='')

    assert 'href=""' not in body
    assert 'paste this into your browser' not in body
    assert 'Request a new one' in body


def test_the_expiry_line_reads_correctly():
    assert 'expires in 3 days' in _verification()
    assert 'expires in 1 day.' in _verification(expiration_days=1), 'it said "1 days"'


def test_the_fallback_panel_is_one_idiom_across_every_v2_child():
    """Divergence here becomes the pattern the remaining templates copy, so the panel markup
    is pinned as shared: same classes, same colours, one place to change them."""
    from pathlib import Path

    from django.conf import settings

    emails = Path(settings.BASE_DIR) / 'templates' / 'emails'
    base = (emails / 'base_email_v2.html').read_text(encoding='utf-8')
    for cls in ('pp-fallback', 'pp-fallback-t', 'pp-fallback-u', 'pp-body-link'):
        assert cls in base, f'{cls} must be declared in the base, not per template'

    for name in ('email_verification.html', 'password_reset.html'):
        src = (emails / name).read_text(encoding='utf-8')
        assert 'pp-fallback-u' in src and 'word-break: break-all' in src

