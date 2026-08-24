"""The one-time "PlatPursuit 1.0 is here" announcement + its send command.

The only user-facing blast the site sends, so the pins here are mostly about what must NOT
happen: no send without the flag, no send to post-launch signups, no second send ever, and no
run at all without the cutover instant that defines the audience.
"""
from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.html import strip_tags

from tests.factories import ProfileFactory

pytestmark = pytest.mark.django_db


def _pre_launch_user(email='old@example.com'):
    """An account that existed before the cutover (the factory stamps date_joined=now, so the
    cutover instant is set in the future by each test)."""
    profile = ProfileFactory(is_linked=True)
    profile.user.email = email
    profile.user.save(update_fields=['email'])
    return profile.user


def test_dry_run_previews_without_the_flag(settings, mailoutbox):
    """Previewing the audience is the whole point of a dry run, so it passes the gate."""
    settings.PP_LAUNCH_DATE = timezone.now() + timedelta(days=1)
    settings.LAUNCH_ANNOUNCEMENT_SEND_ENABLED = False
    _pre_launch_user()

    out = StringIO()
    call_command('send_launch_announcement', stdout=out)

    assert len(mailoutbox) == 0
    assert 'Would send to 1 account' in out.getvalue()
    assert 'DRY RUN' in out.getvalue()


def test_send_noops_while_the_flag_is_off(settings, mailoutbox):
    settings.PP_LAUNCH_DATE = timezone.now() + timedelta(days=1)
    settings.LAUNCH_ANNOUNCEMENT_SEND_ENABLED = False
    _pre_launch_user()

    out = StringIO()
    call_command('send_launch_announcement', '--send', stdout=out)

    assert len(mailoutbox) == 0
    assert 'DISABLED' in out.getvalue()


def test_it_refuses_without_a_launch_date(settings):
    """No cutover instant means no definition of "existing user"; even a dry run is meaningless."""
    settings.PP_LAUNCH_DATE = None

    with pytest.raises(CommandError):
        call_command('send_launch_announcement')


def test_the_audience_cut_holds(settings, mailoutbox):
    """Four accounts, one recipient: post-launch signups never saw the old site, inactive
    accounts and address-less accounts cannot receive."""
    settings.PP_LAUNCH_DATE = timezone.now() + timedelta(days=1)
    settings.LAUNCH_ANNOUNCEMENT_SEND_ENABLED = True

    wanted = _pre_launch_user('wanted@example.com')

    newcomer = _pre_launch_user('new@example.com')
    newcomer.date_joined = timezone.now() + timedelta(days=2)   # joined after the cutover
    newcomer.save(update_fields=['date_joined'])

    inactive = _pre_launch_user('inactive@example.com')
    inactive.is_active = False
    inactive.save(update_fields=['is_active'])

    address_less = _pre_launch_user('')
    address_less.email = ''
    address_less.save(update_fields=['email'])

    call_command('send_launch_announcement', '--send', stdout=StringIO())

    assert len(mailoutbox) == 1
    assert mailoutbox[0].to == [wanted.email]


def test_sending_twice_sends_nothing_twice(settings, mailoutbox):
    """Idempotency by EmailLog: a re-run after a crash finishes the job rather than mailing
    everyone a second time."""
    settings.PP_LAUNCH_DATE = timezone.now() + timedelta(days=1)
    settings.LAUNCH_ANNOUNCEMENT_SEND_ENABLED = True
    _pre_launch_user()

    call_command('send_launch_announcement', '--send', stdout=StringIO())
    assert len(mailoutbox) == 1

    call_command('send_launch_announcement', '--send', stdout=StringIO())
    assert len(mailoutbox) == 1, 'the announcement went out twice'


def test_the_list_unsubscribe_header_rides_along(settings, mailoutbox):
    """The one marketing-adjacent email in the set. The SendGrid backend forwards extra
    headers into the personalization, so this survives the trip in prod too."""
    settings.PP_LAUNCH_DATE = timezone.now() + timedelta(days=1)
    settings.LAUNCH_ANNOUNCEMENT_SEND_ENABLED = True
    _pre_launch_user()

    call_command('send_launch_announcement', '--send', stdout=StringIO())

    assert 'List-Unsubscribe' in mailoutbox[0].extra_headers


def test_the_announcement_renders_clean():
    ctx = {'username': 'TestHunter', 'site_url': 'https://platpursuit.com', 'discord_url': ''}

    body = render_to_string('emails/launch_announcement.html', ctx)

    assert 'PlatPursuit 1.0 is here' in body
    assert chr(8212) not in body
    assert 'role="presentation"' in body, 'it must ride the v2 scaffold'
    # Plaintext is strip_tags(html): the CTA's destination has to survive as visible text.
    assert ctx['site_url'] in strip_tags(body)
    assert 'You&#x27;re receiving this because you have a PlatPursuit account' in body \
        or "You're receiving this because you have a PlatPursuit account" in body
    assert 'Manage your account settings' in body
