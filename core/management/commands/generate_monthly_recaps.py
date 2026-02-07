"""
Management command to generate and finalize monthly recaps.
Run via Render cron a few days into each new month (to allow syncs to complete).

By default, targets the PREVIOUS month (not current), since recaps are most useful
after a month has fully completed and users have synced their trophy data.

Usage:
    python manage.py generate_monthly_recaps                    # Generate for previous month
    python manage.py generate_monthly_recaps --finalize         # Generate and mark as finalized
    python manage.py generate_monthly_recaps --notify           # Generate and send notifications
    python manage.py generate_monthly_recaps --finalize --notify
    python manage.py generate_monthly_recaps --dry-run
    python manage.py generate_monthly_recaps --profile-id 123
    python manage.py generate_monthly_recaps --year 2026 --month 1  # Override target month
    python manage.py generate_monthly_recaps --current-month    # Target current month instead

Cron schedule recommendation:
    - 3rd of month at 00:05 UTC: --finalize --notify
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from trophies.services.monthly_recap_service import MonthlyRecapService


class Command(BaseCommand):
    help = 'Generate monthly recaps for active profiles and optionally finalize previous month'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be generated without actually creating recaps'
        )
        parser.add_argument(
            '--finalize',
            action='store_true',
            help='Mark generated recaps as finalized (immutable)'
        )
        parser.add_argument(
            '--notify',
            action='store_true',
            help='Send notifications to users when their recap is ready'
        )
        parser.add_argument(
            '--profile-id',
            type=int,
            help='Generate recap for a specific profile ID only'
        )
        parser.add_argument(
            '--year',
            type=int,
            help='Override year (defaults to previous month)'
        )
        parser.add_argument(
            '--month',
            type=int,
            help='Override month (defaults to previous month)'
        )
        parser.add_argument(
            '--current-month',
            action='store_true',
            help='Target current month instead of previous month'
        )

    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)
        finalize = options.get('finalize', False)
        notify = options.get('notify', False)
        profile_id = options.get('profile_id')
        year_override = options.get('year')
        month_override = options.get('month')
        current_month = options.get('current_month', False)

        now = timezone.now()

        # Determine target year/month
        # Default: previous month (not current)
        if year_override and month_override:
            # Explicit override
            year, month = year_override, month_override
        elif current_month:
            # Explicitly requested current month
            year, month = now.year, now.month
        else:
            # Default to previous month
            if now.month == 1:
                year, month = now.year - 1, 12
            else:
                year, month = now.year, now.month - 1

        import calendar
        month_name = calendar.month_name[month]
        self.stdout.write(f"Target month: {month_name} {year} ({year}/{month:02d})")

        # Step 1: Generate recaps
        if profile_id:
            self._generate_for_profile(profile_id, year, month, dry_run, notify)
        else:
            self._generate_for_all(year, month, dry_run, notify)

        # Step 2: Finalize if requested
        if finalize and not dry_run:
            self.stdout.write(f"\nFinalizing recaps for {month_name} {year}...")
            count = MonthlyRecapService.finalize_month_recaps(year, month)
            self.stdout.write(
                self.style.SUCCESS(f"  Finalized {count} recap(s)")
            )

    def _generate_for_profile(self, profile_id, year, month, dry_run, notify):
        """Generate recap for a specific profile."""
        from trophies.models import Profile

        self.stdout.write(f"\nGenerating recap for profile {profile_id}...")

        try:
            profile = Profile.objects.get(id=profile_id)
        except Profile.DoesNotExist:
            self.stdout.write(
                self.style.ERROR(f"Profile {profile_id} not found")
            )
            return

        if dry_run:
            trophy_count = MonthlyRecapService.get_trophy_count_for_month(
                profile, year, month
            )
            if trophy_count > 0:
                self.stdout.write(
                    f"  Would generate recap for {profile.psn_username} "
                    f"({trophy_count} trophies)"
                )
            else:
                self.stdout.write(
                    f"  {profile.psn_username} has no activity - skipping"
                )
            return

        recap = MonthlyRecapService.get_or_generate_recap(profile, year, month)

        if recap:
            self.stdout.write(
                self.style.SUCCESS(
                    f"  Generated recap for {profile.psn_username}: "
                    f"{recap.total_trophies_earned} trophies, "
                    f"{recap.platinums_earned} platinums"
                )
            )

            if notify and profile.user:
                self._send_notification(profile, year, month)
        else:
            self.stdout.write(
                f"  {profile.psn_username} has no activity - skipping"
            )

    def _generate_for_all(self, year, month, dry_run, notify):
        """Generate recaps for all active profiles."""
        from trophies.models import Profile, EarnedTrophy
        from datetime import timedelta
        import pytz

        self.stdout.write(f"\nGenerating recaps for all active profiles...")

        # Use a wider window to catch all possible timezones (UTC-12 to UTC+14)
        utc_start, utc_end = MonthlyRecapService.get_month_date_range(year, month, pytz.UTC)
        # Expand by max timezone offset to catch edge cases
        search_start = utc_start - timedelta(hours=14)
        search_end = utc_end + timedelta(hours=14)

        # Find profiles with activity this month
        active_profile_ids = EarnedTrophy.objects.filter(
            earned=True,
            earned_date_time__gte=search_start,
            earned_date_time__lt=search_end
        ).values_list('profile_id', flat=True).distinct()

        # Filter to linked profiles only
        profiles = Profile.objects.filter(
            id__in=active_profile_ids,
            is_linked=True,
            user__isnull=False
        ).select_related('user')

        total = profiles.count()
        self.stdout.write(f"  Found {total} profiles with activity")

        if dry_run:
            self.stdout.write(f"  Would generate {total} recap(s)")
            return

        generated = 0
        failed = 0
        notified = 0

        for profile in profiles:
            try:
                recap = MonthlyRecapService.get_or_generate_recap(profile, year, month)
                if recap:
                    generated += 1

                    if notify and profile.user:
                        self._send_notification(profile, year, month)
                        notified += 1

            except Exception as e:
                failed += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"  Error generating recap for {profile.psn_username}: {e}"
                    )
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"  Generated {generated} recap(s), {failed} failed"
            )
        )

        if notify:
            self.stdout.write(f"  Sent {notified} notification(s)")

    def _send_notification(self, profile, year, month):
        """Send notification to user that their recap is ready."""
        import calendar
        from notifications.services.notification_service import NotificationService

        month_name = calendar.month_name[month]

        try:
            NotificationService.create_notification(
                user=profile.user,
                notification_type='monthly_recap',
                title=f"Your {month_name} Recap is Ready!",
                message=f"See your trophy hunting highlights from {month_name} {year}.",
                action_url=f"/recap/{year}/{month}/",
                icon='trophy',
            )
        except Exception as e:
            self.stdout.write(
                self.style.WARNING(
                    f"  Failed to send notification to {profile.psn_username}: {e}"
                )
            )
