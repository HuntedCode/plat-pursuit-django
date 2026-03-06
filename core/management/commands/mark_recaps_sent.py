"""
One-time management command to mark all existing recaps as email_sent and notification_sent.

Prevents the fixed send_monthly_recap_emails command from picking up
user-generated recaps (from browsing old months) that were created
before the default-to-previous-month fix.

Usage:
    python manage.py mark_recaps_sent              # Mark all unsent recaps
    python manage.py mark_recaps_sent --dry-run     # Preview what would be updated
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from trophies.models import MonthlyRecap


class Command(BaseCommand):
    help = 'Mark all existing recaps as email_sent and notification_sent to prevent stale sends'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be updated without making changes',
        )

    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)
        now = timezone.now()

        unsent_emails = MonthlyRecap.objects.filter(email_sent=False)
        unsent_notifs = MonthlyRecap.objects.filter(notification_sent=False)

        email_count = unsent_emails.count()
        notif_count = unsent_notifs.count()

        self.stdout.write(f"Recaps with email_sent=False: {email_count}")
        self.stdout.write(f"Recaps with notification_sent=False: {notif_count}")

        if email_count == 0 and notif_count == 0:
            self.stdout.write(self.style.SUCCESS("Nothing to update."))
            return

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN: No changes made."))
            return

        updated_emails = unsent_emails.update(email_sent=True, email_sent_at=now)
        updated_notifs = unsent_notifs.update(notification_sent=True, notification_sent_at=now)

        self.stdout.write(self.style.SUCCESS(
            f"Marked {updated_emails} recap(s) as email_sent, "
            f"{updated_notifs} recap(s) as notification_sent."
        ))
