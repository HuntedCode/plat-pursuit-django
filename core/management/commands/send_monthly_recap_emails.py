"""
Management command to send monthly recap emails and in-app notifications to users.

This command finds all finalized recaps that haven't had emails/notifications sent yet,
and sends personalized HTML emails and in-app notifications to users with links to their full recap.

Designed to run via Render cron on the 2nd-3rd of each month after recaps
have been generated and finalized.

Usage:
    python manage.py send_monthly_recap_emails                      # Send all pending emails & notifications
    python manage.py send_monthly_recap_emails --dry-run            # Preview what would be sent
    python manage.py send_monthly_recap_emails --year 2026 --month 1  # Specific month only
    python manage.py send_monthly_recap_emails --profile-id 123     # Specific user only
    python manage.py send_monthly_recap_emails --force              # Resend even if already sent

Recommended cron schedule:
    - Run on 3rd of month at 06:00 UTC (after generate_monthly_recaps runs)
    - python manage.py send_monthly_recap_emails

Requirements:
    - User must have a linked PSN account (is_linked=True)
    - User must have an email address
    - Recap must be finalized (is_finalized=True)
    - Email hasn't been sent yet (email_sent=False) unless --force is used
    - User hasn't opted out of monthly_recap emails (checked via EmailPreferenceService)

Note: In-app notifications are sent to ALL users regardless of email preferences.
"""
import calendar
import logging
from django.core.management.base import BaseCommand
from django.db.models.functions import Lower
from django.utils import timezone
from django.conf import settings
from trophies.models import MonthlyRecap
from core.services.email_service import EmailService
from core.services.monthly_recap_message_service import MonthlyRecapMessageService
from notifications.services.notification_service import NotificationService

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Send monthly recap emails and in-app notifications to users'

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

                    # Try to send notification (independent of email)
                    try:
                        notification_sent = self._send_recap_notification(recap)
                        if notification_sent:
                            logger.debug(f"Notification sent for recap {recap.id}")
                    except Exception as e:
                        # Don't fail the whole batch if notification fails
                        logger.exception(f"Failed to send notification for recap {recap.id}: {e}")

                else:
                    failed += 1
                    self.stdout.write(
                        self.style.ERROR(
                            f"  [{i}/{total}] ✗ Failed to send to {recap.profile.psn_username}"
                        )
                    )

                    # Still try to send notification even if email failed
                    try:
                        notification_sent = self._send_recap_notification(recap)
                        if notification_sent:
                            logger.debug(f"Notification sent for recap {recap.id} (email failed)")
                    except Exception as e:
                        logger.exception(f"Failed to send notification for recap {recap.id}: {e}")

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

        DEPRECATED: Use MonthlyRecapMessageService.get_trophy_tier() instead.
        Kept for backward compatibility.
        """
        return MonthlyRecapMessageService.get_trophy_tier(count)

    def _send_recap_email(self, recap):
        """
        Send recap email for a single MonthlyRecap instance.

        Returns:
            bool: True if email sent successfully, False otherwise
        """
        from users.services.email_preference_service import EmailPreferenceService

        user = recap.profile.user
        profile = recap.profile
        subject = f"Your {recap.month_name} Monthly Rewind is Ready! 🏆"

        # Check email preferences - skip if user opted out
        if not EmailPreferenceService.should_send_email(user, 'monthly_recap'):
            logger.info(f"Skipping recap email for {user.email} - opted out of monthly recaps")
            EmailService.log_suppressed('monthly_recap', user, subject, 'management_command')
            return False

        # Build email context using shared service
        context = MonthlyRecapMessageService.build_email_context(recap)

        try:
            # Send email using EmailService
            sent_count = EmailService.send_html_email(
                subject=subject,
                to_emails=[user.email],
                template_name='emails/monthly_recap.html',
                context=context,
                fail_silently=False,
                log_email_type='monthly_recap',
                log_user=user,
                log_triggered_by='management_command',
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

    def _send_recap_notification(self, recap):
        """
        Send in-app notification for a single MonthlyRecap instance.

        Notifications are sent independently of emails - failure here should
        not block email delivery. ALL users receive notifications regardless
        of email opt-out preferences.

        Args:
            recap: MonthlyRecap instance

        Returns:
            bool: True if notification sent successfully, False otherwise
        """
        user = recap.profile.user

        # Build notification context (teaser only needs base fields)
        context = MonthlyRecapMessageService.build_base_context(recap)
        title = f"Your {context['month_name']} Recap is Ready! 🏆"
        message = MonthlyRecapMessageService.build_notification_message(recap)

        try:
            # Create notification with rich metadata
            NotificationService.create_notification(
                recipient=user,
                notification_type='monthly_recap',
                title=title,
                message=message,
                action_url=context['recap_url'],
                action_text='View Recap',
                icon='🏆',
                priority='normal',
                metadata=context,  # Store full context for potential future use
            )

            # Mark notification as sent
            recap.notification_sent = True
            recap.notification_sent_at = timezone.now()
            recap.save(update_fields=['notification_sent', 'notification_sent_at'])

            return True

        except Exception as e:
            logger.exception(f"Failed to send recap notification for recap {recap.id}: {e}")
            return False
