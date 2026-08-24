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

The audience is accounts that existed BEFORE settings.PP_LAUNCH_DATE -- the same instant the
lobby's launch modal uses to decide "existing user", so the two greetings can never disagree
about who is new. Without that setting the command refuses to run at all.
"""
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.models import EmailLog
from core.services.email_service import EmailService
from users.models import CustomUser

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

        if send and not getattr(settings, 'LAUNCH_ANNOUNCEMENT_SEND_ENABLED', False):
            self.stdout.write(self.style.WARNING(
                'Launch announcement sends are DISABLED '
                '(settings.LAUNCH_ANNOUNCEMENT_SEND_ENABLED is False).\n'
                'No emails were sent. Set it in the environment when you are ready; '
                'the dry run still previews the audience.'
            ))
            return

        recipients = (
            CustomUser.objects
            .filter(is_active=True, date_joined__lt=launch_date)
            .exclude(email='')
            .exclude(email__isnull=True)
            .order_by('id')
        )
        if user_id:
            recipients = recipients.filter(id=user_id)
        if limit:
            recipients = recipients[:limit]

        already = set(
            EmailLog.objects
            .filter(email_type=EMAIL_TYPE, status='sent')
            .values_list('user_id', flat=True)
        )

        self.stdout.write('=' * 70)
        self.stdout.write(self.style.HTTP_INFO(f'{SUBJECT} -- {"SENDING" if send else "DRY RUN"}'))
        self.stdout.write(f'Cutover instant: {launch_date.isoformat()}')
        self.stdout.write('=' * 70)

        sent = skipped = failed = 0
        pending = [u for u in recipients if u.id not in already]
        skipped = recipients.count() - len(pending) if not limit else len(already & {u.id for u in recipients})

        if not send:
            self.stdout.write(f'Would send to {len(pending)} account(s).')
            self.stdout.write(f'Already sent (would skip): {skipped}')
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
                'discord_url': getattr(settings, 'DISCORD_INVITE_URL', ''),
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
                self.stdout.write(f'  ... {index + 1} sent, pausing {sleep_for}s')
                time.sleep(sleep_for)

        self.stdout.write('=' * 70)
        self.stdout.write(self.style.SUCCESS(f'Sent: {sent}'))
        self.stdout.write(f'Already sent (skipped): {skipped}')
        if failed:
            self.stdout.write(self.style.WARNING(f'Failed: {failed}'))

    @staticmethod
    def _display_name(user):
        profile = getattr(user, 'profile', None)
        if profile:
            return profile.display_psn_username or profile.psn_username or user.username
        return user.username
