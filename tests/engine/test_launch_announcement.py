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


def test_send_refuses_loudly_while_the_flag_is_off(settings, mailoutbox):
    """CommandError, not a warning: an operator who set the flag on the wrong service would
    otherwise read exit 0 as "it sent"."""
    settings.PP_LAUNCH_DATE = timezone.now() + timedelta(days=1)
    settings.LAUNCH_ANNOUNCEMENT_SEND_ENABLED = False
    _pre_launch_user()

    with pytest.raises(CommandError):
        call_command('send_launch_announcement', '--send', stdout=StringIO())

    assert len(mailoutbox) == 0


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
    # XP comes from CLAIMING contracts: the email must not promise a level they have not earned.
    assert 'levelled up, before you click' not in body
    assert 'claim' in body.lower()


def test_a_limited_resume_makes_progress(settings, mailoutbox):
    """The audit's catch: capping the AUDIENCE meant a resumed `--limit` re-selected the same
    already-sent accounts and mailed nobody while reporting success. The cap belongs on what
    would actually be sent."""
    settings.PP_LAUNCH_DATE = timezone.now() + timedelta(days=1)
    settings.LAUNCH_ANNOUNCEMENT_SEND_ENABLED = True
    for i in range(4):
        _pre_launch_user(f'hunter{i}@example.com')

    call_command('send_launch_announcement', '--send', '--limit', '2', stdout=StringIO())
    assert len(mailoutbox) == 2

    call_command('send_launch_announcement', '--send', '--limit', '2', stdout=StringIO())
    assert len(mailoutbox) == 4, 'the second canary batch sent to nobody'

    recipients = {m.to[0] for m in mailoutbox}
    assert len(recipients) == 4, 'someone got it twice'


def test_a_zero_limit_is_not_a_full_blast(settings, mailoutbox):
    """--limit 0 is falsy: treated as "no limit" it would mail EVERYONE from the one command
    whose whole design is safe-by-default."""
    settings.PP_LAUNCH_DATE = timezone.now() + timedelta(days=1)
    settings.LAUNCH_ANNOUNCEMENT_SEND_ENABLED = True
    _pre_launch_user()

    call_command('send_launch_announcement', '--send', '--limit', '0', stdout=StringIO())

    assert len(mailoutbox) == 0


def test_a_user_id_outside_the_audience_says_so(settings):
    """A silent "Sent: 0" reads as done; it actually means the account was never eligible."""
    settings.PP_LAUNCH_DATE = timezone.now() + timedelta(days=1)
    settings.LAUNCH_ANNOUNCEMENT_SEND_ENABLED = True
    newcomer = _pre_launch_user('new@example.com')
    newcomer.date_joined = timezone.now() + timedelta(days=2)
    newcomer.save(update_fields=['date_joined'])

    with pytest.raises(CommandError):
        call_command('send_launch_announcement', '--send', '--user-id', str(newcomer.id),
                     stdout=StringIO())


def test_the_plaintext_part_leads_with_the_preheader(settings, mailoutbox):
    """strip_tags removes TAGS, not the CONTENTS of <style>: every v2 email's text/plain part
    used to open with ~2KB of raw CSS. EmailService drops those elements first now."""
    settings.PP_LAUNCH_DATE = timezone.now() + timedelta(days=1)
    settings.LAUNCH_ANNOUNCEMENT_SEND_ENABLED = True
    _pre_launch_user()

    call_command('send_launch_announcement', '--send', stdout=StringIO())

    plain = mailoutbox[0].body.strip()
    assert '-webkit-text-size-adjust' not in plain, 'the stylesheet leaked into the plaintext'
    assert 'mso-table-lspace' not in plain
    # The <title> leads (it is real text), then the preheader -- both inside the first 200
    # characters, where a stylesheet used to sit.
    assert 'The site you signed up for has been rebuilt' in plain[:200], plain[:200]


def test_existing_senders_keep_their_empty_headers(settings, mailoutbox):
    """The headers param must be invisible to every caller that does not pass it."""
    from core.services.email_service import send_welcome_email

    profile = ProfileFactory(is_linked=True)
    profile.user.email = 'plain@example.com'
    profile.user.save(update_fields=['email'])

    send_welcome_email(profile)

    assert mailoutbox[0].extra_headers == {}


def test_the_global_opt_out_is_honoured(settings, mailoutbox):
    """The first non-transactional email since the 2026-08 parking, and the preferences page
    is unrouted -- skipping this check would leave an opted-out user with no recourse but a
    human reading the List-Unsubscribe mailbox."""
    settings.PP_LAUNCH_DATE = timezone.now() + timedelta(days=1)
    settings.LAUNCH_ANNOUNCEMENT_SEND_ENABLED = True

    wanted = _pre_launch_user('wanted@example.com')
    opted_out = _pre_launch_user('nope@example.com')
    opted_out.email_preferences = {'global_unsubscribe': True}
    opted_out.save(update_fields=['email_preferences'])

    out = StringIO()
    call_command('send_launch_announcement', '--send', stdout=out)

    assert [m.to[0] for m in mailoutbox] == [wanted.email]
    assert 'Opted out (skipped): 1' in out.getvalue()


def test_a_future_launch_date_warns(settings):
    """Set ahead of the real cutover, every account alive today counts as "existing"."""
    settings.PP_LAUNCH_DATE = timezone.now() + timedelta(days=30)
    settings.LAUNCH_ANNOUNCEMENT_SEND_ENABLED = True
    _pre_launch_user()

    out = StringIO()
    call_command('send_launch_announcement', stdout=out)

    assert 'FUTURE' in out.getvalue()

