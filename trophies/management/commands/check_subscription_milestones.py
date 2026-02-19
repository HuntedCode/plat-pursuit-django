"""
Check subscription_months milestones for all users with active subscription periods.

Intended to run daily via cron. Only checks users who have an open SubscriptionPeriod
(ended_at IS NULL), avoiding unnecessary work for non-subscribers.

Usage:
    python manage.py check_subscription_milestones
    python manage.py check_subscription_milestones --silent
    python manage.py check_subscription_milestones --dry-run
"""
from django.core.management.base import BaseCommand
from trophies.models import Profile
from trophies.services.milestone_service import check_all_milestones_for_user
from users.models import SubscriptionPeriod


class Command(BaseCommand):
    help = 'Check subscription_months milestones for users with active subscriptions.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--silent',
            action='store_true',
            help='Suppress in-app notifications.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview which users would be checked without running milestone checks.',
        )

    def handle(self, *args, **options):
        silent = options['silent']
        dry_run = options['dry_run']

        notify_webapp = not silent

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE: No milestone checks will be performed.\n'))
        elif silent:
            self.stdout.write(self.style.WARNING('Silent mode: notifications suppressed.'))

        # Find users with an open subscription period
        active_user_ids = SubscriptionPeriod.objects.filter(
            ended_at__isnull=True
        ).values_list('user_id', flat=True).distinct()

        profiles = Profile.objects.filter(
            user_id__in=active_user_ids
        ).select_related('user')

        total_awarded = 0
        profiles_checked = 0

        for profile in profiles.iterator():
            if dry_run:
                self.stdout.write(f'  [WOULD CHECK] {profile.psn_username}')
                profiles_checked += 1
                continue

            awarded = check_all_milestones_for_user(
                profile=profile,
                criteria_type='subscription_months',
                notify_webapp=notify_webapp,
            )
            count = len(awarded) if awarded else 0
            total_awarded += count
            profiles_checked += 1

            if count > 0:
                self.stdout.write(f'  {profile.psn_username}: {count} milestone(s) awarded')

        self.stdout.write('')
        if dry_run:
            self.stdout.write(self.style.SUCCESS(
                f'Done. Would check {profiles_checked} profile(s).'
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f'Done. Checked {profiles_checked} profile(s), '
                f'awarded {total_awarded} total milestone(s).'
            ))
