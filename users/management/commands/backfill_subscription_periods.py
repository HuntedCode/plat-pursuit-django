"""
Backfill SubscriptionPeriod for existing premium subscribers.

Creates an open SubscriptionPeriod for all currently-premium users who
don't yet have one, using the provided start date.

Usage:
    python manage.py backfill_subscription_periods --start 2026-01-20
    python manage.py backfill_subscription_periods --start 2026-01-20 --dry-run
"""
from datetime import datetime
from django.core.management.base import BaseCommand
from django.db.models import Exists, OuterRef
from django.utils import timezone
from users.models import CustomUser, SubscriptionPeriod


class Command(BaseCommand):
    help = 'Backfill SubscriptionPeriod records for existing premium subscribers.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--start',
            type=str,
            required=True,
            help='Start date for backfilled periods (YYYY-MM-DD)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview changes without writing to DB',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        start_str = options['start']

        try:
            start_date = timezone.make_aware(
                datetime.strptime(start_str, '%Y-%m-%d')
            )
        except ValueError:
            self.stderr.write(self.style.ERROR(
                f'Invalid date format: {start_str}. Use YYYY-MM-DD.'
            ))
            return

        if start_date > timezone.now():
            self.stderr.write(self.style.ERROR(
                f'Start date {start_str} is in the future. Use a past date.'
            ))
            return

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE: No changes will be made.\n'))

        # Find premium users with no open (active) SubscriptionPeriod.
        # Uses Exists() instead of .exclude(subscription_periods__ended_at__isnull=True)
        # because the latter fails on empty tables: LEFT JOIN produces NULL for ended_at
        # when no related rows exist, which __isnull=True matches, excluding everyone.
        premium_users = CustomUser.objects.filter(
            premium_tier__isnull=False,
            subscription_provider__isnull=False,  # Skip admin-assigned users
        ).exclude(
            Exists(SubscriptionPeriod.objects.filter(
                user=OuterRef('pk'), ended_at__isnull=True,
            ))
        ).select_related('profile')

        created_count = 0
        for user in premium_users:
            provider = user.subscription_provider or 'stripe'
            if dry_run:
                self.stdout.write(
                    f'  [WOULD CREATE] {user.email} - {provider} from {start_str}'
                )
            else:
                SubscriptionPeriod.objects.create(
                    user=user,
                    started_at=start_date,
                    ended_at=None,
                    provider=provider,
                    notes='backfilled from launch',
                )
                self.stdout.write(self.style.SUCCESS(
                    f'  Created period for {user.email} ({provider}) from {start_str}'
                ))
            created_count += 1

        self.stdout.write('')
        action = 'Would create' if dry_run else 'Created'
        self.stdout.write(self.style.SUCCESS(
            f'Done. {action} {created_count} subscription period(s).'
        ))
