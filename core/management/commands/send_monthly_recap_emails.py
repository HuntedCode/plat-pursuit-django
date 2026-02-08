"""
Management command to send monthly recap emails to users.

This command finds all finalized recaps that haven't had emails sent yet,
and sends personalized HTML emails to users with links to their full recap.

Designed to run via Render cron on the 2nd-3rd of each month after recaps
have been generated and finalized.

Usage:
    python manage.py send_monthly_recap_emails                      # Send all pending emails
    python manage.py send_monthly_recap_emails --dry-run            # Preview what would be sent
    python manage.py send_monthly_recap_emails --year 2026 --month 1  # Specific month only
    python manage.py send_monthly_recap_emails --profile-id 123     # Specific user only
    python manage.py send_monthly_recap_emails --force              # Resend even if already sent

Recommended cron schedule:
    - Run on 3rd of month at 06:00 UTC (after generate_monthly_recaps runs)
    - python manage.py send_monthly_recap_emails

Email requirements:
    - User must have a linked PSN account (is_linked=True)
    - User must have an email address
    - Recap must be finalized (is_finalized=True)
    - Email hasn't been sent yet (email_sent=False) unless --force is used
"""
import calendar
import logging
from django.core.management.base import BaseCommand
from django.db.models.functions import Lower
from django.utils import timezone
from django.conf import settings
from trophies.models import MonthlyRecap
from core.services.email_service import EmailService

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Send monthly recap notification emails to users'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be sent without actually sending emails'
        )
        parser.add_argument(
            '--year',
            type=int,
            help='Send emails for specific year only'
        )
        parser.add_argument(
            '--month',
            type=int,
            help='Send emails for specific month only (1-12)'
        )
        parser.add_argument(
            '--profile-id',
            type=int,
            help='Send email for specific profile ID only'
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Resend emails even if already sent (for testing)'
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=100,
            help='Number of emails to send per batch (default: 100)'
        )

    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)
        year = options.get('year')
        month = options.get('month')
        profile_id = options.get('profile_id')
        force = options.get('force', False)
        batch_size = options.get('batch_size', 100)

        self.stdout.write("=" * 70)
        self.stdout.write("Monthly Recap Email Sender")
        self.stdout.write("=" * 70)

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN MODE - No emails will be sent"))

        # Build queryset of recaps to email
        queryset = self._build_queryset(year, month, profile_id, force)

        total_count = queryset.count()

        if total_count == 0:
            self.stdout.write(self.style.WARNING("No recaps found matching criteria"))
            return

        self.stdout.write(f"\nFound {total_count} recap(s) to email")

        if dry_run:
            self._preview_emails(queryset)
            return

        # Send emails
        self._send_emails(queryset, batch_size)

    def _build_queryset(self, year, month, profile_id, force):
        """Build queryset of recaps that need emails sent."""
        # Base query: finalized recaps with linked profiles and user emails
        queryset = MonthlyRecap.objects.filter(
            is_finalized=True,
            profile__is_linked=True,
            profile__user__isnull=False,
            profile__user__email__isnull=False,
        ).exclude(
            profile__user__email=''
        ).select_related('profile', 'profile__user')

        # Filter by email_sent status
        if not force:
            queryset = queryset.filter(email_sent=False)

        # Filter by year/month if specified
        if year:
            queryset = queryset.filter(year=year)
        if month:
            queryset = queryset.filter(month=month)

        # Filter by profile if specified
        if profile_id:
            queryset = queryset.filter(profile_id=profile_id)

        # Order by most recent first
        queryset = queryset.order_by('-year', '-month', Lower('profile__psn_username'))

        return queryset

    def _preview_emails(self, queryset):
        """Preview what emails would be sent in dry-run mode."""
        from users.services.email_preference_service import EmailPreferenceService

        self.stdout.write("\nEmails that would be sent:")
        self.stdout.write("-" * 70)

        skipped = 0
        for recap in queryset:
            user = recap.profile.user
            month_name = calendar.month_name[recap.month]

            # Check if user has opted out
            if not EmailPreferenceService.should_send_email(user, 'monthly_recap'):
                self.stdout.write(
                    self.style.WARNING(
                        f"  ⊘ {user.email} ({recap.profile.psn_username}) - opted out"
                    )
                )
                skipped += 1
                continue

            self.stdout.write(
                f"  • {user.email} ({recap.profile.psn_username})\n"
                f"    {month_name} {recap.year}: {recap.total_trophies_earned} trophies, "
                f"{recap.platinums_earned} platinum(s)"
            )

        self.stdout.write("-" * 70)
        self.stdout.write(f"\nTotal: {queryset.count() - skipped} email(s) would be sent")
        if skipped > 0:
            self.stdout.write(self.style.WARNING(f"Skipped: {skipped} (opted out)"))

    def _send_emails(self, queryset, batch_size):
        """Send emails to all recaps in queryset."""
        total = queryset.count()
        sent = 0
        failed = 0

        self.stdout.write(f"\nSending emails in batches of {batch_size}...")
        self.stdout.write("-" * 70)

        for i, recap in enumerate(queryset, 1):
            try:
                # Send email
                success = self._send_recap_email(recap)

                if success:
                    sent += 1
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"  [{i}/{total}] ✓ Sent to {recap.profile.psn_username}"
                        )
                    )
                else:
                    failed += 1
                    self.stdout.write(
                        self.style.ERROR(
                            f"  [{i}/{total}] ✗ Failed to send to {recap.profile.psn_username}"
                        )
                    )

            except Exception as e:
                failed += 1
                self.stdout.write(
                    self.style.ERROR(
                        f"  [{i}/{total}] ✗ Error sending to {recap.profile.psn_username}: {e}"
                    )
                )
                logger.exception(f"Error sending recap email to profile {recap.profile_id}")

        # Summary
        self.stdout.write("-" * 70)
        self.stdout.write(
            self.style.SUCCESS(f"\n✓ Sent: {sent}")
        )
        if failed > 0:
            self.stdout.write(
                self.style.ERROR(f"✗ Failed: {failed}")
            )
        self.stdout.write(f"Total: {total}")

    def _get_trophy_tier(self, count):
        """
        Get rounded-down trophy tier for email display.
        Returns a string like '10+', '50+', '100+', etc.
        """
        if count == 0:
            return '0'
        elif count < 10:
            return str(count)
        elif count < 25:
            return '10+'
        elif count < 50:
            return '25+'
        elif count < 100:
            return '50+'
        elif count < 250:
            return '100+'
        elif count < 500:
            return '250+'
        elif count < 1000:
            return '500+'
        else:
            return '1000+'

    def _send_recap_email(self, recap):
        """
        Send recap email for a single MonthlyRecap instance.

        Returns:
            bool: True if email sent successfully, False otherwise
        """
        from users.services.email_preference_service import EmailPreferenceService

        user = recap.profile.user
        profile = recap.profile

        # Check email preferences - skip if user opted out
        if not EmailPreferenceService.should_send_email(user, 'monthly_recap'):
            logger.info(f"Skipping recap email for {user.email} - opted out of monthly recaps")
            return False

        # Get active days from activity calendar or streak data
        active_days = recap.activity_calendar.get('total_active_days', 0)
        if not active_days:
            # Fallback to streak data if activity calendar not available
            active_days = recap.streak_data.get('total_active_days', 0)

        # Generate preference token for email footer
        try:
            preference_token = EmailPreferenceService.generate_preference_token(user.id)
            preference_url = f"{settings.SITE_URL}/users/email-preferences/?token={preference_token}"
            logger.debug(f"Generated preference_url for user {user.id}: {preference_url}")
        except Exception as e:
            logger.exception(f"Failed to generate preference_url for user {user.id}: {e}")
            # Fallback to a generic preferences page (no token)
            preference_url = f"{settings.SITE_URL}/users/email-preferences/"

        # Build email context
        context = {
            'username': profile.display_psn_username or profile.psn_username,
            'month_name': recap.month_name,
            'year': recap.year,
            'active_days': active_days,
            'trophy_tier': self._get_trophy_tier(recap.total_trophies_earned),
            'games_started': recap.games_started,
            'total_trophies': recap.total_trophies_earned,  # Keep for backward compat
            'platinums_earned': recap.platinums_earned,
            'games_completed': recap.games_completed,
            'badges_earned': recap.badges_earned_count,
            'has_streak': bool(recap.streak_data.get('longest_streak', 0) > 1),
            'recap_url': f"{settings.SITE_URL}/recap/{recap.year}/{recap.month}/",
            'site_url': settings.SITE_URL,
            'preference_url': preference_url,
        }

        # Build subject
        subject = f"Your {recap.month_name} Monthly Rewind is Ready! 🏆"

        try:
            # Send email using EmailService
            sent_count = EmailService.send_html_email(
                subject=subject,
                to_emails=[user.email],
                template_name='emails/monthly_recap.html',
                context=context,
                fail_silently=False,
            )

            if sent_count > 0:
                # Mark email as sent
                recap.email_sent = True
                recap.email_sent_at = timezone.now()
                recap.save(update_fields=['email_sent', 'email_sent_at'])
                return True

            return False

        except Exception as e:
            logger.exception(f"Failed to send recap email for recap {recap.id}: {e}")
            return False
