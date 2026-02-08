"""
Integration tests for send_monthly_recap_emails management command.

Tests the full email and notification sending flow.
"""
from django.test import TestCase
from django.core.management import call_command
from django.utils import timezone
from io import StringIO
from trophies.models import MonthlyRecap, Profile
from users.models import CustomUser
from notifications.models import Notification


class SendMonthlyRecapCommandTests(TestCase):
    """Test send_monthly_recap_emails command notification and email logic."""

    def setUp(self):
        """Create test user, profile, and recap."""
        self.user = CustomUser.objects.create_user(
            email='test@example.com',
            password='testpass123'
        )
        self.profile = Profile.objects.create(
            psn_username='testuser',
            user=self.user,
            is_linked=True
        )
        self.recap = MonthlyRecap.objects.create(
            profile=self.profile,
            year=2026,
            month=1,
            is_finalized=True,
            total_trophies_earned=50,
            platinums_earned=1,
            games_started=3,
            games_completed=1,
            badges_earned_count=2,
            bronzes_earned=30,
            silvers_earned=15,
            golds_earned=4,
            activity_calendar={'total_active_days': 15, 'days': [], 'max_count': 5, 'first_day_weekday': 0},
            streak_data={'longest_streak': 5, 'total_active_days': 15},
            platinums_data=[],
            rarest_trophy_data={},
            most_active_day={},
            time_analysis_data={},
            comparison_data={},
            quiz_data=[],
            badges_data=[],
        )

    def test_notification_sent_with_email(self):
        """Test that notification is sent when email is sent."""
        out = StringIO()
        call_command('send_monthly_recap_emails', '--profile-id', str(self.profile.id), stdout=out)

        self.recap.refresh_from_db()
        self.assertTrue(self.recap.email_sent, "Email should be marked as sent")
        self.assertTrue(self.recap.notification_sent, "Notification should be marked as sent")
        self.assertIsNotNone(self.recap.email_sent_at)
        self.assertIsNotNone(self.recap.notification_sent_at)

        # Verify notification was created in database
        notification = Notification.objects.filter(
            recipient=self.user,
            notification_type='monthly_recap'
        ).first()
        self.assertIsNotNone(notification, "Notification should exist in database")
        self.assertIn('January', notification.title)
        self.assertIn('Recap is Ready', notification.title)

    def test_notification_sent_even_when_email_opted_out(self):
        """
        KEY TEST: Verify notification still sent when user opts out of emails.
        This is the critical test for the new behavior - notifications go to ALL users.
        """
        # Opt out of monthly recap emails
        self.user.email_preferences = {'monthly_recap': False, 'global_unsubscribe': False}
        self.user.save()

        out = StringIO()
        call_command('send_monthly_recap_emails', '--profile-id', str(self.profile.id), stdout=out)

        self.recap.refresh_from_db()

        # Email should be skipped
        self.assertFalse(self.recap.email_sent, "Email should NOT be sent (user opted out)")
        self.assertIsNone(self.recap.email_sent_at)

        # Notification should STILL be sent
        self.assertTrue(self.recap.notification_sent, "Notification should STILL be sent (no opt-out)")
        self.assertIsNotNone(self.recap.notification_sent_at)

        # Verify notification exists in database
        notification = Notification.objects.filter(
            recipient=self.user,
            notification_type='monthly_recap'
        ).first()
        self.assertIsNotNone(notification, "Notification should exist even when email opted out")

    def test_notification_content(self):
        """Test notification content is rich and personalized."""
        call_command('send_monthly_recap_emails', '--profile-id', str(self.profile.id), stdout=StringIO())

        notification = Notification.objects.filter(
            recipient=self.user,
            notification_type='monthly_recap'
        ).first()

        self.assertIsNotNone(notification)
        self.assertIn('January', notification.title)
        self.assertIn('🏆', notification.title)
        self.assertIn('trophy', notification.message.lower())
        self.assertEqual(notification.action_text, 'View Recap')
        self.assertIn('/recap/2026/1/', notification.action_url)
        self.assertEqual(notification.icon, '🏆')
        self.assertEqual(notification.priority, 'normal')

        # Check metadata contains context
        self.assertIsNotNone(notification.metadata)
        self.assertEqual(notification.metadata.get('year'), 2026)
        self.assertEqual(notification.metadata.get('month_name'), 'January')

    def test_dry_run_mode(self):
        """Test dry-run mode doesn't send emails or notifications."""
        out = StringIO()
        call_command('send_monthly_recap_emails', '--dry-run', stdout=out)

        self.recap.refresh_from_db()
        self.assertFalse(self.recap.email_sent, "Email should not be sent in dry-run")
        self.assertFalse(self.recap.notification_sent, "Notification should not be sent in dry-run")

        # No notifications should exist
        notification_count = Notification.objects.filter(
            recipient=self.user,
            notification_type='monthly_recap'
        ).count()
        self.assertEqual(notification_count, 0, "No notifications should be created in dry-run")

    def test_already_sent_skipped(self):
        """Test that recaps with email_sent=True are skipped."""
        # Mark as already sent
        self.recap.email_sent = True
        self.recap.email_sent_at = timezone.now()
        self.recap.notification_sent = True
        self.recap.notification_sent_at = timezone.now()
        self.recap.save()

        out = StringIO()
        call_command('send_monthly_recap_emails', '--profile-id', str(self.profile.id), stdout=out)

        # Should not create duplicate notification
        notification_count = Notification.objects.filter(
            recipient=self.user,
            notification_type='monthly_recap'
        ).count()
        self.assertEqual(notification_count, 0, "Should not create duplicate notification")

    def test_force_flag_resends(self):
        """Test --force flag resends even if already sent."""
        # Mark as already sent
        self.recap.email_sent = True
        self.recap.email_sent_at = timezone.now()
        self.recap.notification_sent = True
        self.recap.notification_sent_at = timezone.now()
        self.recap.save()

        out = StringIO()
        call_command('send_monthly_recap_emails', '--profile-id', str(self.profile.id), '--force', stdout=out)

        # Should create new notification (may create duplicate - that's expected with --force)
        notification_count = Notification.objects.filter(
            recipient=self.user,
            notification_type='monthly_recap'
        ).count()
        self.assertGreaterEqual(notification_count, 1, "Should create notification with --force")

    def test_unlinked_profile_skipped(self):
        """Test that unlinked profiles are skipped."""
        self.profile.is_linked = False
        self.profile.save()

        out = StringIO()
        call_command('send_monthly_recap_emails', stdout=out)

        self.recap.refresh_from_db()
        self.assertFalse(self.recap.email_sent, "Email should not be sent for unlinked profile")
        self.assertFalse(self.recap.notification_sent, "Notification should not be sent for unlinked profile")

    def test_no_email_user_skipped(self):
        """Test that users without email addresses are skipped."""
        self.user.email = None
        self.user.save()

        out = StringIO()
        call_command('send_monthly_recap_emails', stdout=out)

        self.recap.refresh_from_db()
        self.assertFalse(self.recap.email_sent, "Email should not be sent for user without email")
        # Note: Notification might still be sent since it doesn't require email

    def test_unfinaliz ed_recap_skipped(self):
        """Test that unfinalized recaps are skipped."""
        self.recap.is_finalized = False
        self.recap.save()

        out = StringIO()
        call_command('send_monthly_recap_emails', stdout=out)

        self.recap.refresh_from_db()
        self.assertFalse(self.recap.email_sent, "Email should not be sent for unfinalized recap")
        self.assertFalse(self.recap.notification_sent, "Notification should not be sent for unfinalized recap")

    def test_batch_processing(self):
        """Test batch processing with multiple recaps."""
        # Create additional test users and recaps
        users_and_recaps = []
        for i in range(5):
            user = CustomUser.objects.create_user(
                email=f'user{i}@example.com',
                password='testpass123'
            )
            profile = Profile.objects.create(
                psn_username=f'user{i}',
                user=user,
                is_linked=True
            )
            recap = MonthlyRecap.objects.create(
                profile=profile,
                year=2026,
                month=1,
                is_finalized=True,
                total_trophies_earned=20 + i,
                platinums_earned=i,
                games_started=2,
                games_completed=1,
                badges_earned_count=1,
                bronzes_earned=10,
                silvers_earned=5,
                golds_earned=3,
                activity_calendar={'total_active_days': 10, 'days': [], 'max_count': 3, 'first_day_weekday': 0},
                streak_data={'longest_streak': 3, 'total_active_days': 10},
                platinums_data=[],
                rarest_trophy_data={},
                most_active_day={},
                time_analysis_data={},
                comparison_data={},
                quiz_data=[],
                badges_data=[],
            )
            users_and_recaps.append((user, recap))

        out = StringIO()
        call_command('send_monthly_recap_emails', '--batch-size', '2', stdout=out)

        # All recaps should have notifications sent
        for user, recap in users_and_recaps:
            recap.refresh_from_db()
            self.assertTrue(recap.email_sent, f"Email should be sent for {user.email}")
            self.assertTrue(recap.notification_sent, f"Notification should be sent for {user.email}")

        # Verify all notifications created
        notification_count = Notification.objects.filter(
            notification_type='monthly_recap'
        ).count()
        self.assertEqual(notification_count, 6, "Should create 6 notifications (5 new + 1 original)")
