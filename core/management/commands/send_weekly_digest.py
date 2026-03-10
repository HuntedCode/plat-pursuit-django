"""
Management command to send "This Week in PlatPursuit" community newsletter.

Community-focused weekly email with site-wide stats, top platted games, review of
the week, and condensed personal stats. Uses EmailLog for deduplication (no
dedicated model).

Designed to run via Render cron every Monday at 08:00 UTC.

Usage:
    python manage.py send_weekly_digest                        # Send for previous week (default)
    python manage.py send_weekly_digest --dry-run              # Preview what would be sent
    python manage.py send_weekly_digest --profile-id 123       # Specific user only
    python manage.py send_weekly_digest --force                # Resend even if already sent this week
    python manage.py send_weekly_digest --batch-size 50        # Custom batch size

Requirements:
    - User must have a linked PSN account (is_linked=True)
    - User must have an email address
    - User hasn't opted out of weekly_digest emails (EmailPreferenceService)
    - User hasn't already received a digest this week (EmailLog dedup, unless --force)
    - Community had some activity (only suppressed if site had zero activity)
"""
import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models.functions import Lower
from django.utils import timezone

from core.models import EmailLog
from core.services.email_service import EmailService
from core.services.weekly_digest_service import WeeklyDigestService
from trophies.models import Profile
from trophies.services.monthly_recap_service import MonthlyRecapService

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Send "This Week in PlatPursuit" community newsletter to all linked users'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be sent without actually sending emails',
        )
        parser.add_argument(
            '--profile-id',
            type=int,
            help='Send digest for specific profile ID only',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Resend even if already sent this week (for testing)',
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=100,
            help='Number of emails to send per batch (default: 100)',
        )

    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)
        profile_id = options.get('profile_id')
        force = options.get('force', False)
        batch_size = options.get('batch_size', 100)

        self.stdout.write("=" * 70)
        self.stdout.write("This Week in PlatPursuit")
        self.stdout.write("=" * 70)

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN MODE: No emails will be sent"))

        # Pre-fetch community data once (same for all users)
        self.stdout.write("\nFetching community data...")
        # Use a wide UTC window for community data (covers all timezones)
        utc_week_start, utc_week_end = WeeklyDigestService.get_week_date_range()
        community_data = WeeklyDigestService.get_community_data(
            utc_week_start, utc_week_end,
        )

        top_review = community_data.get('top_review')
        site_stats = community_data.get('site_stats', {})
        top_games = community_data.get('top_platted_games', [])

        if top_review:
            self.stdout.write(
                f"  Top review: \"{top_review['game_name']}\" by {top_review['author_username']} "
                f"({top_review['helpful_count']} helpful)"
            )
        else:
            self.stdout.write("  No reviews this week")
        self.stdout.write(
            f"  Site stats: {site_stats.get('total_trophies', 0)} trophies, "
            f"{site_stats.get('total_platinums', 0)} platinums, "
            f"{site_stats.get('active_hunters', 0)} active hunters, "
            f"{site_stats.get('total_reviews', 0)} reviews, "
            f"{site_stats.get('new_signups', 0)} new hunters"
        )
        self.stdout.write(f"  Top platted games: {len(top_games)}")

        # Build profile queryset
        profiles = Profile.objects.filter(
            is_linked=True,
            user__isnull=False,
            user__email__isnull=False,
        ).exclude(
            user__email='',
        ).select_related('user').order_by(Lower('psn_username'))

        if profile_id:
            profiles = profiles.filter(id=profile_id)

        total_count = profiles.count()
        if total_count == 0:
            self.stdout.write(self.style.WARNING("No eligible profiles found"))
            return

        self.stdout.write(f"\nFound {total_count} eligible profile(s)")

        if dry_run:
            self._preview_digests(profiles, community_data, force)
            return

        self._send_digests(profiles, community_data, force, batch_size)

    def _preview_digests(self, profiles, community_data, force):
        """Preview what emails would be sent in dry-run mode."""
        from users.services.email_preference_service import EmailPreferenceService

        self.stdout.write("\nDigests that would be sent:")
        self.stdout.write("-" * 70)

        would_send = 0
        opted_out = 0
        already_sent = 0
        suppressed = 0
        dedup_cutoff = timezone.now() - timedelta(days=6)

        for profile in profiles:
            user = profile.user

            # Dedup check
            if not force and EmailLog.objects.filter(
                user=user,
                email_type='weekly_digest',
                status='sent',
                created_at__gte=dedup_cutoff,
            ).exists():
                already_sent += 1
                continue

            # Preference check
            if not EmailPreferenceService.should_send_email(user, 'weekly_digest'):
                opted_out += 1
                continue

            # Build data for preview
            user_tz = MonthlyRecapService._resolve_user_tz(profile)
            week_start, week_end = WeeklyDigestService.get_week_date_range(user_tz)
            digest_data = WeeklyDigestService.build_digest_data(profile, week_start, week_end)

            if WeeklyDigestService.should_suppress(digest_data, community_data):
                suppressed += 1
                continue

            trophy_stats = digest_data['trophy_stats']
            self.stdout.write(
                f"  \u2022 {user.email} ({profile.psn_username})\n"
                f"    {trophy_stats['total']} trophies, "
                f"{trophy_stats['platinum']} plat(s), "
                f"{len(digest_data['challenges'])} active challenge(s)"
            )
            would_send += 1

        self.stdout.write("-" * 70)
        self.stdout.write(f"\nWould send: {would_send} email(s)")
        if already_sent > 0:
            self.stdout.write(f"Already sent this week: {already_sent}")
        if opted_out > 0:
            self.stdout.write(self.style.WARNING(f"Opted out: {opted_out}"))
        if suppressed > 0:
            self.stdout.write(f"Suppressed (no content): {suppressed}")

    def _send_digests(self, profiles, community_data, force, batch_size):
        """Send digest emails to all eligible profiles."""
        from users.services.email_preference_service import EmailPreferenceService

        total = profiles.count()
        sent = 0
        failed = 0
        opted_out = 0
        already_sent_count = 0
        suppressed = 0
        dedup_cutoff = timezone.now() - timedelta(days=6)

        self.stdout.write(f"\nSending digests in batches of {batch_size}...")
        self.stdout.write("-" * 70)

        for i, profile in enumerate(profiles, 1):
            user = profile.user

            try:
                # Dedup check
                if not force and EmailLog.objects.filter(
                    user=user,
                    email_type='weekly_digest',
                    status='sent',
                    created_at__gte=dedup_cutoff,
                ).exists():
                    already_sent_count += 1
                    continue

                # Preference check
                if not EmailPreferenceService.should_send_email(user, 'weekly_digest'):
                    subject = "This Week in PlatPursuit"
                    EmailService.log_suppressed('weekly_digest', user, subject, 'management_command')
                    opted_out += 1
                    continue

                # Build personalized data
                user_tz = MonthlyRecapService._resolve_user_tz(profile)
                week_start, week_end = WeeklyDigestService.get_week_date_range(user_tz)
                digest_data = WeeklyDigestService.build_digest_data(profile, week_start, week_end)

                # Smart suppression (only if community had zero activity)
                if WeeklyDigestService.should_suppress(digest_data, community_data):
                    suppressed += 1
                    continue

                # Build email context
                context = WeeklyDigestService.build_email_context(
                    profile, digest_data, community_data,
                )

                subject = (
                    f"This Week in PlatPursuit: "
                    f"{context['week_start_display']} - {context['week_end_display']}"
                )

                # Send
                sent_count = EmailService.send_html_email(
                    subject=subject,
                    to_emails=[user.email],
                    template_name='emails/weekly_digest.html',
                    context=context,
                    fail_silently=False,
                    log_email_type='weekly_digest',
                    log_user=user,
                    log_triggered_by='management_command',
                )

                if sent_count > 0:
                    sent += 1
                    if i <= 20 or i % batch_size == 0:
                        self.stdout.write(
                            self.style.SUCCESS(
                                f"  [{i}/{total}] Sent to {profile.psn_username}"
                            )
                        )
                else:
                    failed += 1
                    self.stdout.write(
                        self.style.ERROR(
                            f"  [{i}/{total}] Failed to send to {profile.psn_username}"
                        )
                    )

            except Exception as e:
                failed += 1
                self.stdout.write(
                    self.style.ERROR(
                        f"  [{i}/{total}] Error for {profile.psn_username}: {e}"
                    )
                )
                logger.exception(f"Error sending weekly digest to profile {profile.id}")

        # Summary
        self.stdout.write("-" * 70)
        self.stdout.write(self.style.SUCCESS(f"\nSent: {sent}"))
        if already_sent_count > 0:
            self.stdout.write(f"Already sent this week: {already_sent_count}")
        if opted_out > 0:
            self.stdout.write(self.style.WARNING(f"Opted out: {opted_out}"))
        if suppressed > 0:
            self.stdout.write(f"Suppressed (no content): {suppressed}")
        if failed > 0:
            self.stdout.write(self.style.ERROR(f"Failed: {failed}"))
        self.stdout.write(f"Total eligible: {total}")
