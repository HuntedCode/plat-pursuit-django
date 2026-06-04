"""
Management command to clean up old analytics data for GDPR compliance.

Deletes:
- AnalyticsSession records older than 90 days (session metadata)
- IP addresses from PageView records older than 90 days (anonymized to NULL)

Does NOT delete:
- PageView records themselves (view counts preserved forever)
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from datetime import timedelta
from core.models import AnalyticsSession, PageView


class Command(BaseCommand):
    help = "Clean up old analytics data for GDPR compliance and database optimization"

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be deleted without actually deleting',
        )
        parser.add_argument(
            '--days',
            type=int,
            default=90,
            help='Number of days to retain (default: 90)',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Skip confirmation prompt (for cron/unattended use)',
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=5000,
            help='Rows per DELETE/UPDATE batch (default: 5000). Keeps each '
                 'statement under the Postgres statement_timeout.',
        )

    def handle(self, *args, **options):
        cutoff_days = options['days']
        cutoff_date = timezone.now() - timedelta(days=cutoff_days)
        dry_run = options['dry_run']
        batch_size = options['batch_size']

        self.stdout.write(f"Cleaning up analytics data older than {cutoff_days} days (before {cutoff_date.date()})")

        # Count records to be cleaned
        old_sessions = AnalyticsSession.objects.filter(created_at__lt=cutoff_date)
        old_ips = PageView.objects.filter(
            viewed_at__lt=cutoff_date,
            ip_address__isnull=False
        )

        session_count = old_sessions.count()
        ip_count = old_ips.count()

        self.stdout.write(f"\nFound {session_count} old AnalyticsSession records to delete")
        self.stdout.write(f"Found {ip_count} PageView records with IP addresses to anonymize")

        if dry_run:
            self.stdout.write(self.style.WARNING("\n--- DRY RUN MODE - No changes will be made ---"))
            return

        # Confirm before proceeding (unless --force)
        if not options['force']:
            self.stdout.write(self.style.WARNING("\nThis will permanently delete session data and anonymize IP addresses."))
            confirm = input("Type 'yes' to continue: ")

            if confirm.lower() != 'yes':
                self.stdout.write(self.style.ERROR("Cleanup cancelled"))
                return

        # Both operations run in bounded batches so each individual statement
        # stays well under the Postgres statement_timeout. A single sweep over
        # the full backlog (1M+ rows) exceeds the timeout and aborts the run,
        # which previously left IPs un-anonymized while sessions kept deleting,
        # so the un-scrubbed backlog only grew.

        # Delete old AnalyticsSession records
        self.stdout.write("\nDeleting old AnalyticsSession records...")
        deleted_sessions = 0
        while True:
            batch_ids = list(
                old_sessions.values_list('pk', flat=True)[:batch_size]
            )
            if not batch_ids:
                break
            with transaction.atomic():
                AnalyticsSession.objects.filter(pk__in=batch_ids).delete()
            deleted_sessions += len(batch_ids)
            self.stdout.write(f"  deleted {deleted_sessions:,}/{session_count:,} sessions")
        self.stdout.write(self.style.SUCCESS(f"✓ Deleted {deleted_sessions} old sessions"))

        # Anonymize old IP addresses (keep PageView records). Each batch leaves
        # the filter once ip_address is NULL, so the queryset shrinks naturally.
        self.stdout.write("\nAnonymizing old PageView IP addresses...")
        anonymized = 0
        while True:
            batch_ids = list(
                old_ips.values_list('pk', flat=True)[:batch_size]
            )
            if not batch_ids:
                break
            with transaction.atomic():
                anonymized += PageView.objects.filter(
                    pk__in=batch_ids
                ).update(ip_address=None)
            self.stdout.write(f"  anonymized {anonymized:,}/{ip_count:,} IPs")
        self.stdout.write(self.style.SUCCESS(f"✓ Anonymized {anonymized} old IP addresses"))

        self.stdout.write(self.style.SUCCESS(f"\n✓ Cleanup complete!"))
        self.stdout.write(f"  - Deleted {deleted_sessions} AnalyticsSession records")
        self.stdout.write(f"  - Anonymized {anonymized} PageView IP addresses")
        self.stdout.write(f"  - PageView records preserved (view counts intact)")
