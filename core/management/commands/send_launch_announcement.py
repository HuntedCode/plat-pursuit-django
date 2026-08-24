"""Send the one-time "PlatPursuit 1.0 is here" announcement to pre-launch accounts.

DRY RUN BY DEFAULT. This is the only user-facing blast the site sends, so the safe thing has
to be the thing that happens when you type the command with no flags:

    python manage.py send_launch_announcement                  # preview the audience, send nothing
    python manage.py send_launch_announcement --send           # actually send (needs the settings flag)
    python manage.py send_launch_announcement --user-id 42 --send   # single-recipient smoke test

Two independent safeties, because an accidental blast cannot be recalled:
  1. settings.LAUNCH_ANNOUNCEMENT_SEND_ENABLED (env, default False) gates --send. --dry-run is
     always allowed through: it writes nothing, and previewing the audience is its whole point.
  2. Idempotency per user via EmailLog, so a re-run after a crash finishes the job instead of
     mailing everyone twice. Deliberately NO --force: re-sending a one-time announcement is a
     shell decision (delete the log rows), not a flag someone can fat-finger.

This is the first NON-transactional email the site has sent since the 2026-08 parking, so it
honours `global_unsubscribe` like every other bulk sender (the preference UI itself is parked
and unrouted, which is exactly why skipping the check would leave opted-out users with no
recourse but the List-Unsubscribe mailbox).

The audience is accounts that existed BEFORE settings.PP_LAUNCH_DATE -- the same INSTANT the
lobby's launch modal uses, so the two can never disagree about who counts as new. They do not
cover the same POPULATION: the modal needs a linked, synced profile (it lives on the Home
lobby), while this reaches every active account with an address, including people who signed up
and never linked. The copy is written to be true for both. Without the setting the command
refuses to run at all.
"""
import time

from django.utils import timezone

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.models import EmailLog
from core.services.email_service import EmailService
from users.models import CustomUser
from users.services.email_preference_service import EmailPreferenceService

SUBJECT = 'PlatPursuit 1.0 is here'
EMAIL_TYPE = 'launch_announcement'


class Command(BaseCommand):
    help = 'Send the one-time PlatPursuit 1.0 announcement to pre-launch accounts (dry run by default).'

    def add_arguments(self, parser):
        parser.add_argument('--send', action='store_true',
                            help='Actually send. Without this the command only previews the audience.')
        parser.add_argument('--batch-size', type=int, default=100,
                            help='Recipients per batch before pausing (default 100).')
        parser.add_argument('--sleep', type=float, default=2.0,
                            help='Seconds to pause between batches (default 2.0).')
        parser.add_argument('--limit', type=int, default=None,
                            help='Cap the number of recipients this run (a canary).')
        parser.add_argument('--user-id', type=int, default=None,
                            help='Send to one specific user (smoke test).')

    def handle(self, *args, **options):
        send = options['send']
        batch_size = options['batch_size']
        sleep_for = options['sleep']
        limit = options['limit']
        user_id = options['user_id']

        launch_date = getattr(settings, 'PP_LAUNCH_DATE', None)
        if not launch_date:
            # Loud in BOTH modes: without the cutover instant there is no such thing as an
            # "existing user", so even a dry run would preview a meaningless audience.
            raise CommandError(
                'PP_LAUNCH_DATE is not set. It defines who counts as an existing user, so the '
                'audience cannot be computed without it. Set it in the environment first.'
            )

        if limit is not None and limit < 0:
            raise CommandError('--limit cannot be negative.')

        if send and not getattr(settings, 'LAUNCH_ANNOUNCEMENT_SEND_ENABLED', False):
            # CommandError, not a warning-and-return: an operator who set the flag on the
            # wrong service would otherwise read a success exit code as "it sent".
            raise CommandError(
                'Launch announcement sends are DISABLED '
                '(settings.LAUNCH_ANNOUNCEMENT_SEND_ENABLED is False). No emails were sent. '
                'Set it in the environment of THIS service when you are ready; the dry run '
                'still previews the audience.'
            )

        recipients = (
            CustomUser.objects
            .filter(is_active=True, date_joined__lt=launch_date)
            .exclude(email='')
            .exclude(email__isnull=True)
            .select_related('profile')   # _display_name reads it; otherwise one query per send
            .order_by('id')
        )
        if user_id:
            recipients = recipients.filter(id=user_id)
            if not recipients.exists():
                # A silent "Sent: 0" would read as "done" when it actually means "that account
                # is not in the audience" (post-cutover signup, inactive, or no address).
                raise CommandError(
                    f'User {user_id} is not in the audience: they must be active, have an '
                    f'email address, and have joined before {launch_date.isoformat()}.'
                )

        if launch_date > timezone.now():
            self.stdout.write(self.style.WARNING(
                f'PP_LAUNCH_DATE ({launch_date.isoformat()}) is in the FUTURE, so every '
                f'account alive today counts as "existing" -- including signups from after '
                f'the real cutover. Check the value before sending.'
            ))

        already = set(
            EmailLog.objects
            .filter(email_type=EMAIL_TYPE, status='sent')
            .values_list('user_id', flat=True)
        )

        self.stdout.write('=' * 70)
        self.stdout.write(self.style.HTTP_INFO(f'{SUBJECT} -- {"SENDING" if send else "DRY RUN"}'))
        self.stdout.write(f'Cutover instant: {launch_date.isoformat()}')
        self.stdout.write('=' * 70)

        sent = failed = opted_out = 0
        # The cap applies to what would actually be SENT, not to the audience: slicing the
        # queryset first meant a resumed `--limit 5` re-selected the same five already-sent
        # accounts and made zero progress while reporting success.
        audience = list(recipients)
        pending = []
        for user in audience:
            if user.id in already:
                continue
            # Honour the global opt-out even though the preferences page is parked: a user who
            # set it before the parking still means it.
            if not EmailPreferenceService.should_send_email(user, 'admin_announcements'):
                opted_out += 1
                continue
            pending.append(user)
        skipped = len(audience) - len(pending) - opted_out
        if limit is not None:
            pending = pending[:limit]

        if not send:
            self.stdout.write(f'Would send to {len(pending)} account(s).')
            self.stdout.write(f'Already sent (would skip): {skipped}')
            self.stdout.write(f'Opted out (would skip): {opted_out}')
            for user in pending[:10]:
                self.stdout.write(f'  {user.email}')
            if len(pending) > 10:
                self.stdout.write(f'  ... and {len(pending) - 10} more')
            self.stdout.write(self.style.SUCCESS('Dry run complete. Nothing was sent.'))
            return

        for index, user in enumerate(pending):
            context = {
                'username': self._display_name(user),
                'site_url': settings.SITE_URL,
            }
            try:
                count = EmailService.send_html_email(
                    subject=SUBJECT,
                    to_emails=[user.email],
                    template_name='emails/launch_announcement.html',
                    context=context,
                    fail_silently=True,
                    log_email_type=EMAIL_TYPE,
                    log_user=user,
                    log_triggered_by='management_command',
                    # The one marketing-adjacent email we send; cheap insurance, and the
                    # SendGrid backend forwards extra headers into the personalization.
                    headers={'List-Unsubscribe': '<mailto:support@platpursuit.com?subject=unsubscribe>'},
                )
                if count:
                    sent += 1
                else:
                    failed += 1
            except Exception as exc:   # noqa: BLE001 -- one bad address must not end the run
                failed += 1
                self.stderr.write(f'Failed for {user.email}: {exc}')

            if batch_size and (index + 1) % batch_size == 0 and index + 1 < len(pending):
                self.stdout.write(f'  ... {index + 1} processed, pausing {sleep_for}s')
                time.sleep(sleep_for)

        self.stdout.write('=' * 70)
        self.stdout.write(self.style.SUCCESS(f'Sent: {sent}'))
        self.stdout.write(f'Already sent (skipped): {skipped}')
        self.stdout.write(f'Opted out (skipped): {opted_out}')
        if failed:
            self.stdout.write(self.style.WARNING(f'Failed: {failed}'))

    @staticmethod
    def _display_name(user):
        profile = getattr(user, 'profile', None)
        if profile:
            return profile.display_psn_username or profile.psn_username or user.username
        return user.username
