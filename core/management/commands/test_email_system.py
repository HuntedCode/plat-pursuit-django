"""
Test command to verify email system is working correctly.

This sends a test email using the EmailService to verify:
- Email backend (SendGrid) is configured correctly
- HTML rendering works
- Email delivery succeeds

Usage:
    python manage.py test_email_system your.email@example.com
    python manage.py test_email_system your.email@example.com --recap-preview
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

    def handle(self, *args, **options):
        recipient_email = options['recipient_email']
        recap_preview = options.get('recap_preview', False)

        self.stdout.write("=" * 70)
        self.stdout.write("Email System Test")
        self.stdout.write("=" * 70)
        self.stdout.write(f"Recipient: {recipient_email}")
        self.stdout.write(f"From: {settings.DEFAULT_FROM_EMAIL}")
        self.stdout.write(f"Backend: {settings.EMAIL_BACKEND}")

        if recap_preview:
            self._send_recap_preview(recipient_email)
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
        self.stdout.write("\nSending monthly recap email preview...")

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
