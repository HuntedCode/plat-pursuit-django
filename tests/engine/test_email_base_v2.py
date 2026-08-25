"""Email base v2 -- the scaffold every rebuilt email extends.

The legacy base was div-based with no MSO handling, no preheader, a non-bulletproof CTA and a
purple that exists nowhere in the site's brand. v2 coexists while children migrate one rebuild
at a time (migration table: docs/guides/email-setup.md). These pins are what "born compliant"
means, so the next email rebuilt on it inherits a scaffold that still works.
"""
import re
from pathlib import Path

from django.conf import settings

EMAILS = Path(settings.BASE_DIR) / 'templates' / 'emails'
V2 = EMAILS / 'base_email_v2.html'
V2_CHILDREN = ('welcome.html', 'launch_announcement.html',
               'email_verification.html', 'password_reset.html',
               'payment_action_required.html', 'payment_succeeded.html',
               'subscription_welcome.html', 'subscription_cancelled.html',
               'payment_failed.html')   # grows as templates migrate


def _src(path):
    return path.read_text(encoding='utf-8')


def _no_comments(text):
    """Comment-stripped source: a comment that NAMES a banned string has bitten these guard
    tests three times now."""
    return re.sub(r'{%\s*comment\s*%}.*?{%\s*endcomment\s*%}', '', text, flags=re.S)


def test_the_new_base_is_actually_bulletproof():
    """The four things the legacy base lacked, without which a child cannot render in Outlook
    or preview sanely in an inbox list."""
    src = _src(V2)

    assert 'role="presentation"' in src, 'the table scaffold went missing'
    assert '<!--[if mso]' in src, 'no MSO conditionals: Outlook gets no ghost table'
    assert 'name="color-scheme"' in src, 'the dark-mode declaration went missing'
    assert '{% block preheader %}' in src, 'no preheader block: every inbox preview reads the tagline'


def test_the_new_base_keeps_the_system_stack_and_drops_the_dead_purple():
    """Webfonts cannot ship in email (the site's Bricolage/Inter never reach an inbox), and
    the legacy #667eea belongs to no PlatPursuit that exists."""
    for path in (V2,) + tuple(EMAILS / name for name in V2_CHILDREN):
        src = _no_comments(_src(path))
        assert '-apple-system' in _src(V2), 'the system stack went missing'
        assert '#667eea' not in src, f'{path.name} brought back the pre-rebuild purple'


def test_the_cta_partial_carries_both_halves():
    """The VML half is what Outlook draws; the anchor half is what everyone else draws. One
    without the other is a broken button in half the world's inboxes."""
    src = _src(EMAILS / '_cta_button.html')

    assert 'v:roundrect' in src
    assert '<!--[if !mso]' in src
    assert '#28EBFE' in src and '#05080C' in src, 'the button lost its brand pairing'


def test_the_dead_second_base_stays_dead():
    """monthly_recap.html.backup was a full un-extended copy of an old email: a second,
    invisible base nobody would have thought to update."""
    assert not (EMAILS / 'monthly_recap.html.backup').exists()
