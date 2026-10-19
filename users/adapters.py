from allauth.account.adapter import DefaultAccountAdapter
from django.conf import settings
from django.core.exceptions import PermissionDenied
import logging

from core.client_ip import client_ip

logger = logging.getLogger(__name__)

class CustomAccountAdapter(DefaultAccountAdapter):
    def get_client_ip(self, request):
        # The IP allauth's rate limits key on. Its default reads REMOTE_ADDR, which behind
        # Render is the proxy: every visitor would share one bucket (core/client_ip.py).
        ip = client_ip(request)
        if not ip:
            raise PermissionDenied('Unable to determine client IP address')
        return ip

    def save_user(self, request, user, form, commit=True):
        """A new account takes its browser's timezone (the signup form's `browser_timezone`, already validated),
        in the same save, so a hunter who never opens Settings still reads their own clock. Not a confirmation:
        `timezone_confirmed_at` stays null, and the Recap's prompt still asks them to confirm the guess."""
        user = super().save_user(request, user, form, commit=False)
        zone = getattr(form, 'cleaned_data', {}).get('browser_timezone') if form is not None else ''
        if zone:
            user.user_timezone = zone
        if commit:
            user.save()
        return user

    def confirm_email(self, request, email_address):
        logger.debug(f"Confirming email: {email_address.email} for user {email_address.user}")
        # The RETURN is load-bearing and was missing for the override's whole life: allauth
        # treats a falsy result as "confirmation failed", so the address got verified in the
        # DB while the flow reported failure -- which silently disabled login-on-confirmation
        # and bounced every fresh signup to the login form.
        confirmed = super().confirm_email(request, email_address)
        logger.debug(f"Email verified: {email_address.verified}")
        return confirmed

    def send_confirmation_mail(self, request, emailconfirmation, signup):
        """
        Override to send custom HTML email verification using EmailService.
        Matches the styling of our monthly recap emails for brand consistency.
        """
        from core.services.email_service import EmailService

        user = emailconfirmation.email_address.user
        email = emailconfirmation.email_address.email

        # Generate activation URL using allauth's built-in method
        activate_url = self.get_email_confirmation_url(request, emailconfirmation)

        # Build template context. (The preference-token construction that used to live here
        # was dropped 2026-08-23: the preferences page is parked and no kept email template
        # consumes preference_url -- it was a wasted token per send.)
        context = {
            'username': user.username or email.split('@')[0],  # Fallback to email prefix
            'activate_url': activate_url,
            'site_url': settings.SITE_URL,
            'expiration_days': settings.ACCOUNT_EMAIL_CONFIRMATION_EXPIRE_DAYS,
        }

        # Detect if this is an admin-triggered resend
        triggered_by = 'system'
        if request and hasattr(request, 'user') and request.user.is_staff:
            triggered_by = 'admin'

        # Send using EmailService (same as monthly recap emails)
        try:
            EmailService.send_html_email(
                subject='Welcome! Please Confirm Your Email Address',
                to_emails=[email],
                template_name='emails/email_verification.html',
                context=context,
                fail_silently=False,
                log_email_type='email_verification',
                log_user=user,
                log_triggered_by=triggered_by,
            )
            logger.info(f"Email verification sent to {email} for user {user.id} (triggered_by={triggered_by})")
        except Exception as e:
            logger.exception(f"Failed to send verification email to {email}: {e}")
            raise  # Re-raise so allauth knows sending failed

    def send_password_reset_mail(self, user, email, context):
        """
        Override to send custom HTML password reset email using EmailService.
        Matches the styling of our monthly recap emails for brand consistency.
        """
        from core.services.email_service import EmailService

        # password_reset_url is inside the context dict (allauth 65.x API)
        password_reset_url = context.get('password_reset_url', '')

        # Build template context (preference_url dropped 2026-08-23, same reason as above).
        template_context = {
            'username': user.username or email.split('@')[0],  # Fallback to email prefix
            'password_reset_url': password_reset_url,
            'site_url': settings.SITE_URL,
        }

        # Send using EmailService (same as monthly recap emails)
        try:
            EmailService.send_html_email(
                subject='Reset Your Password',
                to_emails=[email],
                template_name='emails/password_reset.html',
                context=template_context,
                fail_silently=False,
                log_email_type='password_reset',
                log_user=user,
                log_triggered_by='system',
            )
            logger.info(f"Password reset email sent to {email} for user {user.id}")
        except Exception as e:
            logger.exception(f"Failed to send password reset email to {email}: {e}")
            raise  # Re-raise so allauth knows sending failed