"""The free welcome email (sent once when a hunter links their PSN profile).

Nothing rendered this template before, which is exactly how five feature cards selling
Challenges, Checklists, Reviews, a retired badge model and the deleted dashboard survived a
whole rebuild. These pins are the tripwire for that class of drift.
"""
import pytest
from django.conf import settings
from django.template.loader import render_to_string
from django.utils.html import strip_tags

from tests.factories import ProfileFactory

pytestmark = pytest.mark.django_db

CTX = {
    'username': 'TestHunter',
    'profile_url': 'https://platpursuit.com/hunters/testhunter/',
    'discord_url': 'https://discord.gg/platpursuit',
    'site_url': 'https://platpursuit.com',
}


def _render(**overrides):
    ctx = dict(CTX)
    ctx.update(overrides)
    return render_to_string('emails/welcome.html', ctx)


def test_welcome_sells_the_real_systems():
    body = _render()

    for system in ('Your Career', 'Your Collection', 'Rate My Games',
                   'Monthly Recap', 'Leaderboards'):
        assert system in body, f'the welcome email stopped mentioning {system}'
    assert 'Twenty-five jobs across five disciplines' in body


def test_welcome_buries_the_dead_systems():
    """Every one of these was live copy in the pre-rebuild welcome. A returning name here
    means the email is selling something a new hunter cannot find."""
    body = _render()

    for dead in ('Challenges', 'Checklists', '40+ series', 'four tiers'):
        assert dead not in body, f'the welcome email is selling {dead} again'
    assert 'dashboard' not in body.lower(), 'the dashboard came back'


def test_welcome_carries_the_discord_cta():
    body = _render()
    assert f'href="{CTX["discord_url"]}"' in body

    without = _render(discord_url='')
    assert 'Come say hello' not in without, 'the community box needs its guard'


def test_welcome_has_no_em_dash():
    body = _render()

    assert chr(8212) not in body
    assert 'mdash' not in body


def test_the_profile_url_survives_the_plaintext_strip():
    """The text/plain part is strip_tags(html), which discards every href: a button alone
    leads nowhere for anyone reading in plaintext."""
    plain = strip_tags(_render())

    assert CTX['profile_url'] in plain


def test_welcome_rides_the_new_base():
    body = _render()

    assert 'role="presentation"' in body, 'the v2 table scaffold is missing'
    assert '#667eea' not in body, 'the pre-rebuild purple came back'


def test_send_welcome_email_is_clean_and_idempotent(mailoutbox):
    """The dead preference_url (a token built and passed on every send, read by nothing) is
    gone, and the EmailLog guard still stops a second send."""
    from core.services.email_service import send_welcome_email

    profile = ProfileFactory(is_linked=True)
    profile.user.email = 'hunter@example.com'
    profile.user.save(update_fields=['email'])

    send_welcome_email(profile)

    assert len(mailoutbox) == 1
    assert 'email-preferences' not in mailoutbox[0].body

    send_welcome_email(profile)
    assert len(mailoutbox) == 1, 'the welcome email sent twice'
