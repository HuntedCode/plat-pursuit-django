"""
Test command to verify email system is working correctly.

This sends a test email using the EmailService to verify:
- Email backend (SendGrid) is configured correctly
- HTML rendering works
- Email delivery succeeds

Usage:
    python manage.py test_email_system your.email@example.com
    python manage.py test_email_system your.email@example.com --recap-preview
    python manage.py test_email_system your.email@example.com --verification-preview
    python manage.py test_email_system your.email@example.com --password-reset-preview
    python manage.py test_email_system your.email@example.com --payment-failed-preview
    python manage.py test_email_system your.email@example.com --payment-failed-final-preview
    python manage.py test_email_system your.email@example.com --cancelled-preview
    python manage.py test_email_system your.email@example.com --welcome-preview
    python manage.py test_email_system your.email@example.com --payment-succeeded-preview
    python manage.py test_email_system your.email@example.com --payment-action-required-preview
    python manage.py test_email_system your.email@example.com --donation-receipt-preview
    python manage.py test_email_system your.email@example.com --badge-claim-preview
    python manage.py test_email_system your.email@example.com --artwork-complete-preview
    python manage.py test_email_system your.email@example.com --badge-earned-preview
    python manage.py test_email_system your.email@example.com --milestone-preview
    python manage.py test_email_system your.email@example.com --free-welcome-preview
    python manage.py test_email_system your.email@example.com --broadcast-preview
"""
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from core.services.email_service import EmailService


class Command(BaseCommand):
    help = 'Send a test email to verify email system is working'

    def add_arguments(self, parser):
        parser.add_argument(
            'recipient_email',
            type=str,
            help='Email address to send test email to'
        )
        parser.add_argument(
            '--recap-preview',
            action='store_true',
            help='Send a preview of the monthly recap email template'
        )
        parser.add_argument(
            '--verification-preview',
            action='store_true',
            help='Send a preview of the email verification template'
        )
        parser.add_argument(
            '--password-reset-preview',
            action='store_true',
            help='Send a preview of the password reset template'
        )
        parser.add_argument(
            '--payment-failed-preview',
            action='store_true',
            help='Send a preview of the payment failed email (first warning, friendly tone)'
        )
        parser.add_argument(
            '--payment-failed-final-preview',
            action='store_true',
            help='Send a preview of the payment failed email (final warning, urgent tone)'
        )
        parser.add_argument(
            '--cancelled-preview',
            action='store_true',
            help='Send a preview of the subscription cancelled farewell email'
        )
        parser.add_argument(
            '--welcome-preview',
            action='store_true',
            help='Send a preview of the subscription welcome email'
        )
        parser.add_argument(
            '--payment-succeeded-preview',
            action='store_true',
            help='Send a preview of the payment succeeded / renewal confirmation email'
        )
        parser.add_argument(
            '--payment-action-required-preview',
            action='store_true',
            help='Send a preview of the payment action required (3D Secure) email'
        )
        parser.add_argument(
            '--donation-receipt-preview',
            action='store_true',
            help='Send a preview of the fundraiser donation receipt email'
        )
        parser.add_argument(
            '--badge-claim-preview',
            action='store_true',
            help='Send a preview of the badge claim confirmation email'
        )
        parser.add_argument(
            '--artwork-complete-preview',
            action='store_true',
            help='Send a preview of the badge artwork complete notification email'
        )
        parser.add_argument(
            '--badge-earned-preview',
            action='store_true',
            help='Send a preview of the badge earned achievement email'
        )
        parser.add_argument(
            '--milestone-preview',
            action='store_true',
            help='Send a preview of the milestone achieved email'
        )
        parser.add_argument(
            '--free-welcome-preview',
            action='store_true',
            help='Send a preview of the free user welcome email (post first sync)'
        )
        parser.add_argument(
            '--broadcast-preview',
            action='store_true',
            help='Send a preview of the admin broadcast email template'
        )

    def handle(self, *args, **options):
        recipient_email = options['recipient_email']
        recap_preview = options.get('recap_preview', False)
        verification_preview = options.get('verification_preview', False)
        password_reset_preview = options.get('password_reset_preview', False)
        payment_failed_preview = options.get('payment_failed_preview', False)
        payment_failed_final_preview = options.get('payment_failed_final_preview', False)
        cancelled_preview = options.get('cancelled_preview', False)
        welcome_preview = options.get('welcome_preview', False)
        payment_succeeded_preview = options.get('payment_succeeded_preview', False)
        payment_action_required_preview = options.get('payment_action_required_preview', False)
        donation_receipt_preview = options.get('donation_receipt_preview', False)
        badge_claim_preview = options.get('badge_claim_preview', False)
        artwork_complete_preview = options.get('artwork_complete_preview', False)
        badge_earned_preview = options.get('badge_earned_preview', False)
        milestone_preview = options.get('milestone_preview', False)
        free_welcome_preview = options.get('free_welcome_preview', False)
        broadcast_preview = options.get('broadcast_preview', False)

        self.stdout.write("=" * 70)
        self.stdout.write("Email System Test")
        self.stdout.write("=" * 70)
        self.stdout.write(f"Recipient: {recipient_email}")
        self.stdout.write(f"From: {settings.DEFAULT_FROM_EMAIL}")
        self.stdout.write(f"Backend: {settings.EMAIL_BACKEND}")

        if recap_preview:
            self._send_recap_preview(recipient_email)
        elif verification_preview:
            self._send_verification_preview(recipient_email)
        elif password_reset_preview:
            self._send_password_reset_preview(recipient_email)
        elif payment_failed_preview:
            self._send_payment_failed_preview(recipient_email, is_final=False)
        elif payment_failed_final_preview:
            self._send_payment_failed_preview(recipient_email, is_final=True)
        elif cancelled_preview:
            self._send_cancelled_preview(recipient_email)
        elif welcome_preview:
            self._send_welcome_preview(recipient_email)
        elif payment_succeeded_preview:
            self._send_payment_succeeded_preview(recipient_email)
        elif payment_action_required_preview:
            self._send_payment_action_required_preview(recipient_email)
        elif donation_receipt_preview:
            self._send_donation_receipt_preview(recipient_email)
        elif badge_claim_preview:
            self._send_badge_claim_preview(recipient_email)
        elif artwork_complete_preview:
            self._send_artwork_complete_preview(recipient_email)
        elif badge_earned_preview:
            self._send_badge_earned_preview(recipient_email)
        elif milestone_preview:
            self._send_milestone_preview(recipient_email)
        elif free_welcome_preview:
            self._send_free_welcome_preview(recipient_email)
        elif broadcast_preview:
            self._send_broadcast_preview(recipient_email)
        else:
            self._send_simple_test(recipient_email)

    def _send_simple_test(self, recipient_email):
        """Send a simple test email."""
        from django.core.mail import send_mail

        self.stdout.write("\nSending simple test email...")

        try:
            send_mail(
                subject='PlatPursuit Email Test',
                message='This is a test email from PlatPursuit. If you received this, your email system is working correctly!',
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[recipient_email],
                fail_silently=False,
            )

            self.stdout.write(
                self.style.SUCCESS('✓ Test email sent successfully!')
            )
            self.stdout.write('\nCheck your inbox (and spam folder).')

        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'✗ Failed to send test email: {e}')
            )
            raise CommandError(f'Email sending failed: {e}')

    def _send_recap_preview(self, recipient_email):
        """Send a preview of the monthly recap email template."""
        from users.services.email_preference_service import EmailPreferenceService

        self.stdout.write("\nSending monthly recap email preview...")

        # Generate a sample preference token (using a dummy user ID for preview)
        # In real emails, this would be the actual user's ID
        sample_user_id = 1
        try:
            preference_token = EmailPreferenceService.generate_preference_token(sample_user_id)
            preference_url = f"{settings.SITE_URL}/users/email-preferences/?token={preference_token}"
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"Failed to generate preference token: {e}"))
            preference_url = f"{settings.SITE_URL}/users/email-preferences/"

        # Sample context data for preview
        context = {
            'username': 'TestUser',
            'month_name': 'January',
            'year': 2026,
            'active_days': 15,
            'trophy_tier': '100+',
            'games_started': 8,
            'total_trophies': 142,
            'platinums_earned': 2,
            'games_completed': 3,
            'badges_earned': 5,
            'has_streak': True,
            'recap_url': f'{settings.SITE_URL}/recap/2026/1/',
            'site_url': settings.SITE_URL,
            'preference_url': preference_url,
        }

        try:
            sent_count = EmailService.send_html_email(
                subject='[PREVIEW] Your January Monthly Rewind is Ready! 🏆',
                to_emails=[recipient_email],
                template_name='emails/monthly_recap.html',
                context=context,
                fail_silently=False,
            )

            if sent_count > 0:
                self.stdout.write(
                    self.style.SUCCESS('✓ Preview email sent successfully!')
                )
                self.stdout.write('\nCheck your inbox to see how the monthly recap email looks.')
                self.stdout.write('\nNote: This is sample data for preview purposes only.')
            else:
                self.stdout.write(
                    self.style.ERROR('✗ Email was not sent (no errors but send count is 0)')
                )

        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'✗ Failed to send preview email: {e}')
            )
            raise CommandError(f'Email sending failed: {e}')

    def _send_verification_preview(self, recipient_email):
        """Send a preview of the email verification template."""
        from users.services.email_preference_service import EmailPreferenceService

        self.stdout.write("\nSending email verification preview...")

        # Sample data
        sample_user_id = 1
        try:
            preference_token = EmailPreferenceService.generate_preference_token(sample_user_id)
            preference_url = f"{settings.SITE_URL}/users/email-preferences/?token={preference_token}"
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"Failed to generate preference token: {e}"))
            preference_url = f"{settings.SITE_URL}/users/email-preferences/"

        context = {
            'username': 'TestUser',
            'activate_url': f'{settings.SITE_URL}/accounts/confirm-email/sample-key-abc123/',
            'site_url': settings.SITE_URL,
            'preference_url': preference_url,
            'expiration_days': settings.ACCOUNT_EMAIL_CONFIRMATION_EXPIRE_DAYS,
        }

        try:
            sent_count = EmailService.send_html_email(
                subject='[PREVIEW] Welcome! Please Confirm Your Email Address',
                to_emails=[recipient_email],
                template_name='emails/email_verification.html',
                context=context,
                fail_silently=False,
            )

            if sent_count > 0:
                self.stdout.write(
                    self.style.SUCCESS('✓ Verification preview sent successfully!')
                )
                self.stdout.write('\nCheck your inbox to see how the email verification email looks.')
            else:
                self.stdout.write(
                    self.style.ERROR('✗ Email was not sent (no errors but send count is 0)')
                )

        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'✗ Failed to send verification preview: {e}')
            )
            raise CommandError(f'Email sending failed: {e}')

    def _send_password_reset_preview(self, recipient_email):
        """Send a preview of the password reset template."""
        from users.services.email_preference_service import EmailPreferenceService

        self.stdout.write("\nSending password reset preview...")

        # Sample data
        sample_user_id = 1
        try:
            preference_token = EmailPreferenceService.generate_preference_token(sample_user_id)
            preference_url = f"{settings.SITE_URL}/users/email-preferences/?token={preference_token}"
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"Failed to generate preference token: {e}"))
            preference_url = f"{settings.SITE_URL}/users/email-preferences/"

        context = {
            'username': 'TestUser',
            'password_reset_url': f'{settings.SITE_URL}/accounts/password/reset/key/sample-uid-abc123-set-password/',
            'site_url': settings.SITE_URL,
            'preference_url': preference_url,
        }

        try:
            sent_count = EmailService.send_html_email(
                subject='[PREVIEW] Reset Your Password',
                to_emails=[recipient_email],
                template_name='emails/password_reset.html',
                context=context,
                fail_silently=False,
            )

            if sent_count > 0:
                self.stdout.write(
                    self.style.SUCCESS('✓ Password reset preview sent successfully!')
                )
                self.stdout.write('\nCheck your inbox to see how the password reset email looks.')
            else:
                self.stdout.write(
                    self.style.ERROR('✗ Email was not sent (no errors but send count is 0)')
                )

        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'✗ Failed to send password reset preview: {e}')
            )
            raise CommandError(f'Email sending failed: {e}')

    def _send_payment_failed_preview(self, recipient_email, is_final=False):
        """Send a preview of the payment failed email template."""
        from users.services.email_preference_service import EmailPreferenceService

        variant = "final warning (urgent)" if is_final else "first warning (friendly)"
        self.stdout.write(f"\nSending payment failed email preview ({variant})...")

        sample_user_id = 1
        try:
            preference_token = EmailPreferenceService.generate_preference_token(sample_user_id)
            preference_url = f"{settings.SITE_URL}/users/email-preferences/?token={preference_token}"
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"Failed to generate preference token: {e}"))
            preference_url = f"{settings.SITE_URL}/users/email-preferences/"

        context = {
            'username': 'TestUser',
            'is_final_warning': is_final,
            'portal_url': f'{settings.SITE_URL}/users/subscription-management/',
            'tier_name': 'Premium Monthly',
            'site_url': settings.SITE_URL,
            'preference_url': preference_url,
        }

        subject = (
            "[PREVIEW] Action Required: Your PlatPursuit subscription is at risk"
            if is_final
            else "[PREVIEW] Heads up: We couldn't process your payment"
        )

        try:
            sent_count = EmailService.send_html_email(
                subject=subject,
                to_emails=[recipient_email],
                template_name='emails/payment_failed.html',
                context=context,
                fail_silently=False,
            )

            if sent_count > 0:
                self.stdout.write(
                    self.style.SUCCESS(f'✓ Payment failed preview ({variant}) sent successfully!')
                )
                self.stdout.write('\nCheck your inbox to see how the payment failed email looks.')
            else:
                self.stdout.write(
                    self.style.ERROR('✗ Email was not sent (no errors but send count is 0)')
                )

        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'✗ Failed to send payment failed preview: {e}')
            )
            raise CommandError(f'Email sending failed: {e}')

    def _send_cancelled_preview(self, recipient_email):
        """Send a preview of the subscription cancelled farewell email."""
        from users.services.email_preference_service import EmailPreferenceService

        self.stdout.write("\nSending subscription cancelled email preview...")

        sample_user_id = 1
        try:
            preference_token = EmailPreferenceService.generate_preference_token(sample_user_id)
            preference_url = f"{settings.SITE_URL}/users/email-preferences/?token={preference_token}"
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"Failed to generate preference token: {e}"))
            preference_url = f"{settings.SITE_URL}/users/email-preferences/"

        context = {
            'username': 'TestUser',
            'tier_name': 'Premium Monthly',
            'subscribe_url': f'{settings.SITE_URL}/users/subscribe/',
            'site_url': settings.SITE_URL,
            'preference_url': preference_url,
        }

        try:
            sent_count = EmailService.send_html_email(
                subject="[PREVIEW] We're sorry to see you go",
                to_emails=[recipient_email],
                template_name='emails/subscription_cancelled.html',
                context=context,
                fail_silently=False,
            )

            if sent_count > 0:
                self.stdout.write(
                    self.style.SUCCESS('✓ Subscription cancelled preview sent successfully!')
                )
                self.stdout.write('\nCheck your inbox to see how the cancellation farewell email looks.')
            else:
                self.stdout.write(
                    self.style.ERROR('✗ Email was not sent (no errors but send count is 0)')
                )

        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'✗ Failed to send cancelled preview: {e}')
            )
            raise CommandError(f'Email sending failed: {e}')

    def _send_welcome_preview(self, recipient_email):
        """Send a preview of the subscription welcome email."""
        from users.services.email_preference_service import EmailPreferenceService

        self.stdout.write("\nSending subscription welcome email preview...")

        sample_user_id = 1
        try:
            preference_token = EmailPreferenceService.generate_preference_token(sample_user_id)
            preference_url = f"{settings.SITE_URL}/users/email-preferences/?token={preference_token}"
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"Failed to generate preference token: {e}"))
            preference_url = f"{settings.SITE_URL}/users/email-preferences/"

        context = {
            'username': 'TestUser',
            'tier_name': 'Premium Monthly',
            'site_url': settings.SITE_URL,
            'profile_url': f'{settings.SITE_URL}/profiles/TestUser/',
            'preference_url': preference_url,
        }

        try:
            sent_count = EmailService.send_html_email(
                subject='[PREVIEW] Welcome to PlatPursuit Premium!',
                to_emails=[recipient_email],
                template_name='emails/subscription_welcome.html',
                context=context,
                fail_silently=False,
            )

            if sent_count > 0:
                self.stdout.write(
                    self.style.SUCCESS('✓ Welcome preview sent successfully!')
                )
                self.stdout.write('\nCheck your inbox to see how the welcome email looks.')
            else:
                self.stdout.write(
                    self.style.ERROR('✗ Email was not sent (no errors but send count is 0)')
                )

        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'✗ Failed to send welcome preview: {e}')
            )
            raise CommandError(f'Email sending failed: {e}')

    def _send_payment_succeeded_preview(self, recipient_email):
        """Send a preview of the payment succeeded / renewal confirmation email."""
        from users.services.email_preference_service import EmailPreferenceService

        self.stdout.write("\nSending payment succeeded email preview...")

        sample_user_id = 1
        try:
            preference_token = EmailPreferenceService.generate_preference_token(sample_user_id)
            preference_url = f"{settings.SITE_URL}/users/email-preferences/?token={preference_token}"
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"Failed to generate preference token: {e}"))
            preference_url = f"{settings.SITE_URL}/users/email-preferences/"

        context = {
            'username': 'TestUser',
            'tier_name': 'Premium Monthly',
            'next_billing_date': 'March 19, 2026',
            'manage_url': f'{settings.SITE_URL}/users/subscription-management/',
            'site_url': settings.SITE_URL,
            'preference_url': preference_url,
        }

        try:
            sent_count = EmailService.send_html_email(
                subject='[PREVIEW] Payment confirmed for your PlatPursuit subscription',
                to_emails=[recipient_email],
                template_name='emails/payment_succeeded.html',
                context=context,
                fail_silently=False,
            )

            if sent_count > 0:
                self.stdout.write(
                    self.style.SUCCESS('✓ Payment succeeded preview sent successfully!')
                )
                self.stdout.write('\nCheck your inbox to see how the payment confirmation email looks.')
            else:
                self.stdout.write(
                    self.style.ERROR('✗ Email was not sent (no errors but send count is 0)')
                )

        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'✗ Failed to send payment succeeded preview: {e}')
            )
            raise CommandError(f'Email sending failed: {e}')

    def _send_payment_action_required_preview(self, recipient_email):
        """Send a preview of the payment action required (3D Secure) email."""
        from users.services.email_preference_service import EmailPreferenceService

        self.stdout.write("\nSending payment action required email preview...")

        sample_user_id = 1
        try:
            preference_token = EmailPreferenceService.generate_preference_token(sample_user_id)
            preference_url = f"{settings.SITE_URL}/users/email-preferences/?token={preference_token}"
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"Failed to generate preference token: {e}"))
            preference_url = f"{settings.SITE_URL}/users/email-preferences/"

        context = {
            'username': 'TestUser',
            'tier_name': 'Premium Monthly',
            'invoice_url': f'{settings.SITE_URL}/users/subscription-management/',
            'site_url': settings.SITE_URL,
            'preference_url': preference_url,
        }

        try:
            sent_count = EmailService.send_html_email(
                subject='[PREVIEW] Complete your payment verification',
                to_emails=[recipient_email],
                template_name='emails/payment_action_required.html',
                context=context,
                fail_silently=False,
            )

            if sent_count > 0:
                self.stdout.write(
                    self.style.SUCCESS('✓ Payment action required preview sent successfully!')
                )
                self.stdout.write('\nCheck your inbox to see how the 3D Secure email looks.')
            else:
                self.stdout.write(
                    self.style.ERROR('✗ Email was not sent (no errors but send count is 0)')
                )

        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'✗ Failed to send payment action required preview: {e}')
            )
            raise CommandError(f'Email sending failed: {e}')

    def _send_donation_receipt_preview(self, recipient_email):
        """Send a preview of the fundraiser donation receipt email."""
        from users.services.email_preference_service import EmailPreferenceService

        self.stdout.write("\nSending donation receipt email preview...")

        sample_user_id = 1
        try:
            preference_token = EmailPreferenceService.generate_preference_token(sample_user_id)
            preference_url = f"{settings.SITE_URL}/users/email-preferences/?token={preference_token}"
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"Failed to generate preference token: {e}"))
            preference_url = f"{settings.SITE_URL}/users/email-preferences/"

        # Build a mock donation-like object for template rendering
        class MockDonation:
            amount = '25.00'
            provider = 'stripe'

        class MockFundraiser:
            name = 'Badge Artwork Fund'
            slug = 'badge-artwork-fund'

        context = {
            'user': type('User', (), {'first_name': 'TestUser', 'email': recipient_email})(),
            'donation': MockDonation(),
            'fundraiser': MockFundraiser(),
            'badge_picks_earned': 2,
            'claim_url': f'{settings.SITE_URL}/fundraiser/badge-artwork-fund/',
            'site_url': settings.SITE_URL,
            'preference_url': preference_url,
        }

        try:
            sent_count = EmailService.send_html_email(
                subject='[PREVIEW] Thank you for your donation!',
                to_emails=[recipient_email],
                template_name='emails/donation_receipt.html',
                context=context,
                fail_silently=False,
            )

            if sent_count > 0:
                self.stdout.write(
                    self.style.SUCCESS('✓ Donation receipt preview sent successfully!')
                )
                self.stdout.write('\nCheck your inbox to see how the donation receipt email looks.')
            else:
                self.stdout.write(
                    self.style.ERROR('✗ Email was not sent (no errors but send count is 0)')
                )

        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'✗ Failed to send donation receipt preview: {e}')
            )
            raise CommandError(f'Email sending failed: {e}')

    def _send_badge_claim_preview(self, recipient_email):
        """Send a preview of the badge claim confirmation email."""
        from users.services.email_preference_service import EmailPreferenceService

        self.stdout.write("\nSending badge claim confirmation email preview...")

        sample_user_id = 1
        try:
            preference_token = EmailPreferenceService.generate_preference_token(sample_user_id)
            preference_url = f"{settings.SITE_URL}/users/email-preferences/?token={preference_token}"
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"Failed to generate preference token: {e}"))
            preference_url = f"{settings.SITE_URL}/users/email-preferences/"

        class MockClaim:
            series_name = 'Trophy Hunter'
            series_slug = 'trophy-hunter'

        context = {
            'user': type('User', (), {'first_name': 'TestUser', 'email': recipient_email})(),
            'claim': MockClaim(),
            'badge_url': f'{settings.SITE_URL}/badges/trophy-hunter/',
            'site_url': settings.SITE_URL,
            'preference_url': preference_url,
        }

        try:
            sent_count = EmailService.send_html_email(
                subject='[PREVIEW] Badge claimed: Trophy Hunter',
                to_emails=[recipient_email],
                template_name='emails/badge_claim_confirmation.html',
                context=context,
                fail_silently=False,
            )

            if sent_count > 0:
                self.stdout.write(
                    self.style.SUCCESS('✓ Badge claim preview sent successfully!')
                )
                self.stdout.write('\nCheck your inbox to see how the badge claim email looks.')
            else:
                self.stdout.write(
                    self.style.ERROR('✗ Email was not sent (no errors but send count is 0)')
                )

        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'✗ Failed to send badge claim preview: {e}')
            )
            raise CommandError(f'Email sending failed: {e}')

    def _send_artwork_complete_preview(self, recipient_email):
        """Send a preview of the badge artwork complete notification email."""
        from users.services.email_preference_service import EmailPreferenceService

        self.stdout.write("\nSending artwork complete email preview...")

        sample_user_id = 1
        try:
            preference_token = EmailPreferenceService.generate_preference_token(sample_user_id)
            preference_url = f"{settings.SITE_URL}/users/email-preferences/?token={preference_token}"
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"Failed to generate preference token: {e}"))
            preference_url = f"{settings.SITE_URL}/users/email-preferences/"

        class MockClaim:
            series_name = 'Trophy Hunter'
            series_slug = 'trophy-hunter'

        context = {
            'user': type('User', (), {'first_name': 'TestUser', 'email': recipient_email})(),
            'claim': MockClaim(),
            'badge_url': f'{settings.SITE_URL}/badges/trophy-hunter/',
            'site_url': settings.SITE_URL,
            'preference_url': preference_url,
        }

        try:
            sent_count = EmailService.send_html_email(
                subject='[PREVIEW] New artwork is live: Trophy Hunter!',
                to_emails=[recipient_email],
                template_name='emails/artwork_complete.html',
                context=context,
                fail_silently=False,
            )

            if sent_count > 0:
                self.stdout.write(
                    self.style.SUCCESS('✓ Artwork complete preview sent successfully!')
                )
                self.stdout.write('\nCheck your inbox to see how the artwork notification email looks.')
            else:
                self.stdout.write(
                    self.style.ERROR('✗ Email was not sent (no errors but send count is 0)')
                )

        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'✗ Failed to send artwork complete preview: {e}')
            )
            raise CommandError(f'Email sending failed: {e}')

    def _send_badge_earned_preview(self, recipient_email):
        """Send a preview of the badge earned achievement email."""
        from users.services.email_preference_service import EmailPreferenceService

        self.stdout.write("\nSending badge earned email preview...")

        sample_user_id = 1
        try:
            preference_token = EmailPreferenceService.generate_preference_token(sample_user_id)
            preference_url = f"{settings.SITE_URL}/users/email-preferences/?token={preference_token}"
        except Exception:
            preference_url = f"{settings.SITE_URL}/users/email-preferences/"

        context = {
            'username': 'TestUser',
            'badges': [
                {
                    'badge_name': 'Souls Series',
                    'badge_series': 'Souls Series',
                    'badge_tier': 3,
                    'badge_tier_name': 'Gold',
                    'badge_description': 'Conquer the lands of Dark Souls, Bloodborne, and Elden Ring.',
                    'is_max_tier': False,
                    'next_tier_progress': {
                        'tier_name': 'Platinum',
                        'completed_stages': 3,
                        'required_stages': 4,
                        'progress_percentage': 75,
                    },
                },
                {
                    'badge_name': 'Final Fantasy',
                    'badge_series': 'Final Fantasy',
                    'badge_tier': 2,
                    'badge_tier_name': 'Silver',
                    'badge_description': 'Journey through the worlds of Final Fantasy.',
                    'is_max_tier': False,
                    'next_tier_progress': {
                        'tier_name': 'Gold',
                        'completed_stages': 2,
                        'required_stages': 3,
                        'progress_percentage': 67,
                    },
                },
            ],
            'badges_url': f'{settings.SITE_URL}/profiles/TestUser/?tab=badges',
            'site_url': settings.SITE_URL,
            'preference_url': preference_url,
        }

        try:
            sent_count = EmailService.send_html_email(
                subject='[PREVIEW] New badges earned!',
                to_emails=[recipient_email],
                template_name='emails/badge_earned.html',
                context=context,
                fail_silently=False,
            )
            if sent_count > 0:
                self.stdout.write(self.style.SUCCESS('Badge earned preview sent successfully!'))
            else:
                self.stdout.write(self.style.ERROR('Email was not sent'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Failed: {e}'))
            raise CommandError(f'Email sending failed: {e}')

    def _send_milestone_preview(self, recipient_email):
        """Send a preview of the milestone achieved email."""
        from users.services.email_preference_service import EmailPreferenceService

        self.stdout.write("\nSending milestone achieved email preview...")

        sample_user_id = 1
        try:
            preference_token = EmailPreferenceService.generate_preference_token(sample_user_id)
            preference_url = f"{settings.SITE_URL}/users/email-preferences/?token={preference_token}"
        except Exception:
            preference_url = f"{settings.SITE_URL}/users/email-preferences/"

        milestones_url = f'{settings.SITE_URL}/profiles/TestUser/?tab=milestones'

        # Sample milestone contexts (consolidated format)
        sample_milestones = [
            {
                'milestone_name': '50 Platinum Trophies',
                'milestone_description': 'Earn 50 Platinum trophies across any games.',
                'title_name': 'Veteran Hunter',
                'tier_text': 'Tier 3 of 5',
                'is_one_off': False,
                'is_max_tier': False,
                'next_milestone': {
                    'name': '100 Platinum Trophies',
                    'progress_value': 50,
                    'required_value': 100,
                    'progress_percentage': 50,
                },
                'milestones_url': milestones_url,
            },
            {
                'milestone_name': '1,000 Trophies Earned',
                'milestone_description': 'Collect 1,000 trophies of any type.',
                'title_name': '',
                'tier_text': 'Tier 2 of 4',
                'is_one_off': False,
                'is_max_tier': False,
                'next_milestone': {
                    'name': '2,500 Trophies Earned',
                    'progress_value': 1000,
                    'required_value': 2500,
                    'progress_percentage': 40,
                },
                'milestones_url': milestones_url,
            },
        ]

        context = {
            'username': 'TestUser',
            'milestones': sample_milestones,
            'milestone_count': len(sample_milestones),
            'is_single': len(sample_milestones) == 1,
            'profile_milestones_url': milestones_url,
            'site_url': settings.SITE_URL,
            'preference_url': preference_url,
        }

        try:
            sent_count = EmailService.send_html_email(
                subject='[PREVIEW] You achieved 2 milestones!',
                to_emails=[recipient_email],
                template_name='emails/milestone_achieved.html',
                context=context,
                fail_silently=False,
            )
            if sent_count > 0:
                self.stdout.write(self.style.SUCCESS('Milestone preview sent successfully!'))
            else:
                self.stdout.write(self.style.ERROR('Email was not sent'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Failed: {e}'))
            raise CommandError(f'Email sending failed: {e}')

    def _send_free_welcome_preview(self, recipient_email):
        """Send a preview of the free user welcome email."""
        from users.services.email_preference_service import EmailPreferenceService

        self.stdout.write("\nSending free user welcome email preview...")

        sample_user_id = 1
        try:
            preference_token = EmailPreferenceService.generate_preference_token(sample_user_id)
            preference_url = f"{settings.SITE_URL}/users/email-preferences/?token={preference_token}"
        except Exception:
            preference_url = f"{settings.SITE_URL}/users/email-preferences/"

        context = {
            'username': 'TestUser',
            'profile_url': f'{settings.SITE_URL}/profile/TestUser/',
            'discord_url': getattr(settings, 'DISCORD_INVITE_URL', 'https://discord.gg/example'),
            'site_url': settings.SITE_URL,
            'preference_url': preference_url,
        }

        try:
            sent_count = EmailService.send_html_email(
                subject='[PREVIEW] Welcome to PlatPursuit!',
                to_emails=[recipient_email],
                template_name='emails/welcome.html',
                context=context,
                fail_silently=False,
            )
            if sent_count > 0:
                self.stdout.write(self.style.SUCCESS('Free welcome preview sent successfully!'))
            else:
                self.stdout.write(self.style.ERROR('Email was not sent'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Failed: {e}'))
            raise CommandError(f'Email sending failed: {e}')

    def _send_broadcast_preview(self, recipient_email):
        """Send a preview of the admin broadcast email template."""
        from users.services.email_preference_service import EmailPreferenceService
        import markdown as md

        self.stdout.write("\nSending broadcast email preview...")

        sample_user_id = 1
        try:
            preference_token = EmailPreferenceService.generate_preference_token(sample_user_id)
            preference_url = f"{settings.SITE_URL}/users/email-preferences/?token={preference_token}"
        except Exception:
            preference_url = f"{settings.SITE_URL}/users/email-preferences/"

        sample_markdown = (
            "## What's New This Week\n\n"
            "We have been busy building some exciting features:\n\n"
            "- **Genre Challenge** is now live! Test your range across 15 genres.\n"
            "- The **Review Hub** has been overhauled with ratings, filters, and community highlights.\n"
            "- Bug fixes and performance improvements across the board.\n\n"
            "> \"No trophy can hide from us.\"\n\n"
            "### Discord Events\n\n"
            "Join us this Saturday for our monthly **Platinum Race** event! "
            "Details in the #events channel.\n\n"
            "Happy hunting!"
        )

        email_body_html = md.markdown(
            sample_markdown,
            extensions=['extra', 'nl2br', 'sane_lists'],
        )

        context = {
            'username': 'TestUser',
            'email_subject': 'PlatPursuit Weekly Update',
            'email_body_html': email_body_html,
            'cta_url': f'{settings.SITE_URL}/dashboard/',
            'cta_text': 'Visit Your Dashboard',
            'site_url': settings.SITE_URL,
            'preference_url': preference_url,
        }

        try:
            sent_count = EmailService.send_html_email(
                subject='[PREVIEW] PlatPursuit Weekly Update',
                to_emails=[recipient_email],
                template_name='emails/broadcast.html',
                context=context,
                fail_silently=False,
            )
            if sent_count > 0:
                self.stdout.write(self.style.SUCCESS('Broadcast preview sent successfully!'))
            else:
                self.stdout.write(self.style.ERROR('Email was not sent'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Failed: {e}'))
            raise CommandError(f'Email sending failed: {e}')
