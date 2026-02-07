"""
EmailService - Reusable service for sending HTML emails via SendGrid.

Provides a consistent interface for sending transactional emails across the application.
"""
import logging
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.html import strip_tags

logger = logging.getLogger(__name__)


class EmailService:
    """Service for sending HTML emails with fallback to plain text."""

    @staticmethod
    def send_html_email(
        subject,
        to_emails,
        template_name,
        context,
        from_email=None,
        fail_silently=False
    ):
        """
        Send an HTML email using a Django template.

        Args:
            subject: Email subject line
            to_emails: List of recipient email addresses (or single string)
            template_name: Path to HTML email template (e.g., 'emails/monthly_recap.html')
            context: Dictionary of template context variables
            from_email: Sender email (defaults to DEFAULT_FROM_EMAIL)
            fail_silently: If True, don't raise exceptions on send failure

        Returns:
            int: Number of emails successfully sent (0 or 1)

        Example:
            EmailService.send_html_email(
                subject="Your Monthly Recap",
                to_emails=['user@example.com'],
                template_name='emails/monthly_recap.html',
                context={'username': 'John', 'month': 'January'},
            )
        """
        if from_email is None:
            from_email = settings.DEFAULT_FROM_EMAIL

        # Ensure to_emails is a list
        if isinstance(to_emails, str):
            to_emails = [to_emails]

        try:
            # Render HTML template
            html_content = render_to_string(template_name, context)

            # Generate plain text version by stripping HTML tags
            text_content = strip_tags(html_content)

            # Create email message
            email = EmailMultiAlternatives(
                subject=subject,
                body=text_content,
                from_email=from_email,
                to=to_emails,
            )

            # Attach HTML version
            email.attach_alternative(html_content, "text/html")

            # Send email
            sent_count = email.send()

            logger.info(
                f"Email sent successfully: '{subject}' to {to_emails[0]}"
                + (f" (+{len(to_emails)-1} more)" if len(to_emails) > 1 else "")
            )

            return sent_count

        except Exception as e:
            logger.exception(f"Failed to send email '{subject}' to {to_emails}: {e}")
            if not fail_silently:
                raise
            return 0

    @staticmethod
    def send_bulk_html_email(
        subject,
        recipients,
        template_name,
        context_fn,
        from_email=None,
        batch_size=100
    ):
        """
        Send personalized HTML emails to multiple recipients in batches.

        Args:
            subject: Email subject line (can include {variable} for per-user substitution)
            recipients: List of recipient dicts with 'email' key and optional context vars
            template_name: Path to HTML email template
            context_fn: Function that takes a recipient dict and returns template context
            from_email: Sender email (defaults to DEFAULT_FROM_EMAIL)
            batch_size: Number of emails to send per batch

        Returns:
            tuple: (success_count, failure_count)

        Example:
            recipients = [
                {'email': 'user1@example.com', 'username': 'John'},
                {'email': 'user2@example.com', 'username': 'Jane'},
            ]

            def get_context(recipient):
                return {
                    'username': recipient['username'],
                    'month': 'January',
                }

            EmailService.send_bulk_html_email(
                subject="Your Monthly Recap",
                recipients=recipients,
                template_name='emails/monthly_recap.html',
                context_fn=get_context,
            )
        """
        if from_email is None:
            from_email = settings.DEFAULT_FROM_EMAIL

        success_count = 0
        failure_count = 0

        for i in range(0, len(recipients), batch_size):
            batch = recipients[i:i + batch_size]

            for recipient in batch:
                try:
                    email_address = recipient.get('email')
                    if not email_address:
                        logger.warning(f"Skipping recipient with no email: {recipient}")
                        failure_count += 1
                        continue

                    # Get personalized context
                    context = context_fn(recipient)

                    # Send email
                    sent = EmailService.send_html_email(
                        subject=subject,
                        to_emails=[email_address],
                        template_name=template_name,
                        context=context,
                        from_email=from_email,
                        fail_silently=True,
                    )

                    if sent:
                        success_count += 1
                    else:
                        failure_count += 1

                except Exception as e:
                    logger.exception(f"Failed to send email to {recipient.get('email')}: {e}")
                    failure_count += 1

        logger.info(
            f"Bulk email complete: {success_count} sent, {failure_count} failed"
        )

        return success_count, failure_count
