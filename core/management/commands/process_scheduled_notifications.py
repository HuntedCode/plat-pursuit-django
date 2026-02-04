"""
Management command to process scheduled notifications.
Run via Render cron every hour.

Usage:
    python manage.py process_scheduled_notifications
    python manage.py process_scheduled_notifications --dry-run
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from notifications.services.scheduled_notification_service import ScheduledNotificationService


class Command(BaseCommand):
    help = 'Process pending scheduled notifications that are due'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be processed without actually sending'
        )

    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)

        if dry_run:
            from notifications.models import ScheduledNotification

            now = timezone.now()
            pending = ScheduledNotification.objects.filter(
                status='pending',
                scheduled_at__lte=now
            )
            count = pending.count()

            self.stdout.write(f"Would process {count} scheduled notification(s)")

            for scheduled in pending:
                self.stdout.write(
                    f"  - {scheduled.title} "
                    f"(target: {scheduled.get_target_type_display()}, "
                    f"~{scheduled.recipient_count} recipients, "
                    f"scheduled: {scheduled.scheduled_at})"
                )
            return

        processed = ScheduledNotificationService.process_pending()

        if processed > 0:
            self.stdout.write(
                self.style.SUCCESS(f'Processed {processed} scheduled notification(s)')
            )
        else:
            self.stdout.write('No pending notifications to process')
