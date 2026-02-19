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

    def handle(self, *args, **options):
        recipient_email = options['recipient_email']
        recap_preview = options.get('recap_preview', False)
        verification_preview = options.get('verification_preview', False)
        password_reset_preview = options.get('password_reset_preview', False)
        payment_failed_preview = options.get('payment_failed_preview', False)
        payment_failed_final_preview = options.get('payment_failed_final_preview', False)
        cancelled_preview = options.get('cancelled_preview', False)

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
