"""
ScheduledNotificationService - Handles scheduling and processing of bulk notifications.
Follows the service layer pattern used throughout the project.
"""
from django.db import transaction
from django.utils import timezone
from django.contrib.auth import get_user_model
import logging

logger = logging.getLogger(__name__)
CustomUser = get_user_model()


class ScheduledNotificationService:
    """Service for managing scheduled notifications and extended user targeting."""

    @staticmethod
    def get_target_users_extended(target_type, criteria=None):
        """
        Get queryset of users based on target type with extended options.

        Args:
            target_type: Target type from ScheduledNotification.TARGET_TYPE_CHOICES
            criteria: Dict with filtering options like:
                - user_ids: List of user IDs (for 'individual' target_type)

        Returns:
            QuerySet of CustomUser instances
        """
        criteria = criteria or {}

        if target_type == 'all':
            return CustomUser.objects.filter(
                is_active=True,
                profile__isnull=False
            )

        elif target_type == 'premium_monthly':
            return CustomUser.objects.filter(
                is_active=True,
                premium_tier='premium_monthly'
            )

        elif target_type == 'premium_yearly':
            return CustomUser.objects.filter(
                is_active=True,
                premium_tier='premium_yearly'
            )

        elif target_type == 'premium_supporter':
            return CustomUser.objects.filter(
                is_active=True,
                premium_tier='supporter'
            )

        elif target_type == 'premium_all':
            return CustomUser.objects.filter(
                is_active=True,
                premium_tier__isnull=False
            ).exclude(premium_tier='')

        elif target_type == 'discord_verified':
            return CustomUser.objects.filter(
                is_active=True,
                profile__is_discord_verified=True
            )

        elif target_type == 'individual':
            user_ids = criteria.get('user_ids', [])
            return CustomUser.objects.filter(
                id__in=user_ids,
                is_active=True
            )

        return CustomUser.objects.none()

    @staticmethod
    def estimate_recipient_count(target_type, criteria=None):
        """
        Get estimated recipient count without loading full queryset.

        Args:
            target_type: Target type string
            criteria: Optional criteria dict

        Returns:
            int: Estimated number of recipients
        """
        return ScheduledNotificationService.get_target_users_extended(
            target_type, criteria
        ).count()

    @staticmethod
    @transaction.atomic
    def create_scheduled(
        notification_type, title, message, target_type, scheduled_at,
        created_by, criteria=None, **kwargs
    ):
        """
        Create a new scheduled notification.

        Args:
            notification_type: Type of notification
            title: Notification title
            message: Notification message
            target_type: Target audience type
            scheduled_at: DateTime when to send
            created_by: CustomUser who created this
            criteria: Optional targeting criteria dict
            **kwargs: Optional fields (detail, sections, banner_image, icon, action_url, action_text, priority)

        Returns:
            ScheduledNotification instance
        """
        from notifications.models import ScheduledNotification
        from notifications.validators import SectionValidator

        criteria = criteria or {}
        sections = kwargs.get('sections', [])

        # Validate sections if provided
        if sections:
            is_valid, error = SectionValidator.validate_sections(sections)
            if not is_valid:
                raise ValueError(f"Invalid sections: {error}")

        # Estimate recipient count
        recipient_count = ScheduledNotificationService.estimate_recipient_count(
            target_type, criteria
        )

        scheduled = ScheduledNotification.objects.create(
            notification_type=notification_type,
            title=title,
            message=message,
            detail=kwargs.get('detail', ''),
            sections=sections,
            banner_image=kwargs.get('banner_image'),
            icon=kwargs.get('icon', '📢'),
            action_url=kwargs.get('action_url'),
            action_text=kwargs.get('action_text', ''),
            priority=kwargs.get('priority', 'normal'),
            target_type=target_type,
            target_criteria=criteria,
            scheduled_at=scheduled_at,
            created_by=created_by,
            recipient_count=recipient_count,
            send_email=kwargs.get('send_email', False),
        )

        format_type = 'structured' if sections else 'markdown'
        logger.info(
            f"Scheduled notification created by {created_by.email}: "
            f"'{title}' for {scheduled_at}, ~{recipient_count} recipients ({format_type} format)"
        )

        return scheduled

    @staticmethod
    @transaction.atomic
    def send_immediate(
        notification_type, title, message, target_type,
        sent_by, criteria=None, **kwargs
    ):
        """
        Send notification immediately (not scheduled).

        Args:
            notification_type: Type of notification
            title: Notification title
            message: Notification message
            target_type: Target audience type
            sent_by: CustomUser who sent this
            criteria: Optional targeting criteria dict
            **kwargs: Optional fields (detail, sections, banner_image, icon, action_url, action_text, priority)

        Returns:
            tuple: (NotificationLog instance, count of recipients)
        """
        from notifications.models import NotificationLog
        from notifications.services.notification_service import NotificationService
        from notifications.validators import SectionValidator

        criteria = criteria or {}
        detail = kwargs.get('detail', '')
        sections = kwargs.get('sections', [])

        # Validate sections if provided
        if sections:
            is_valid, error = SectionValidator.validate_sections(sections)
            if not is_valid:
                raise ValueError(f"Invalid sections: {error}")

        recipients = ScheduledNotificationService.get_target_users_extended(
            target_type, criteria
        )
        recipient_count = recipients.count()

        if recipient_count == 0:
            logger.warning(
                f"Immediate notification by {sent_by.email} matched 0 recipients: "
                f"target_type={target_type}, criteria={criteria}"
            )
            return None, 0

        # Send bulk notification using existing service
        created_count = NotificationService.send_bulk_notification(
            recipients_queryset=recipients,
            notification_type=notification_type,
            title=title,
            message=message,
            detail=detail,
            sections=sections,
            banner_image=kwargs.get('banner_image'),
            icon=kwargs.get('icon', '📢'),
            action_url=kwargs.get('action_url'),
            action_text=kwargs.get('action_text', ''),
            priority=kwargs.get('priority', 'normal'),
        )

        # Send companion emails if enabled
        emails_sent = 0
        emails_suppressed = 0
        send_email = kwargs.get('send_email', False)
        if send_email:
            emails_sent, emails_suppressed = ScheduledNotificationService._send_broadcast_emails(
                recipients=recipients,
                title=title,
                message=message,
                icon=kwargs.get('icon', '\U0001F4E2'),
                priority=kwargs.get('priority', 'normal'),
                sections=kwargs.get('sections', []),
                detail=kwargs.get('detail', ''),
                banner_image=kwargs.get('banner_image'),
                action_url=kwargs.get('action_url'),
                action_text=kwargs.get('action_text', ''),
            )

        # Create log entry
        log = NotificationLog.objects.create(
            notification_type=notification_type,
            title=title,
            message=message,
            detail=detail,
            target_type=target_type,
            target_criteria=criteria,
            recipient_count=created_count,
            sent_by=sent_by,
            was_scheduled=False,
            emails_sent=emails_sent,
            emails_suppressed=emails_suppressed,
        )

        format_type = 'structured' if sections else 'markdown'
        email_info = f", {emails_sent} emails sent, {emails_suppressed} suppressed" if send_email else ""
        logger.info(
            f"Immediate notification sent by {sent_by.email}: "
            f"'{title}' to {created_count} recipients ({format_type} format){email_info}"
        )

        return log, created_count

    @staticmethod
    def process_pending():
        """
        Process all pending scheduled notifications that are due.
        Called by management command via Render cron.

        Returns:
            int: Number of notifications processed
        """
        from notifications.models import ScheduledNotification

        now = timezone.now()
        pending = ScheduledNotification.objects.filter(
            status='pending',
            scheduled_at__lte=now
        ).select_for_update(skip_locked=True)

        processed_count = 0

        for scheduled in pending:
            try:
                ScheduledNotificationService._process_single(scheduled)
                processed_count += 1
            except Exception as e:
                logger.exception(
                    f"Failed to process scheduled notification {scheduled.id}: {e}"
                )
                scheduled.status = 'failed'
                scheduled.error_message = str(e)
                scheduled.save(update_fields=['status', 'error_message'])

        return processed_count

    @staticmethod
    @transaction.atomic
    def _process_single(scheduled):
        """
        Process a single scheduled notification.

        Args:
            scheduled: ScheduledNotification instance
        """
        from notifications.models import NotificationLog
        from notifications.services.notification_service import NotificationService

        # Mark as processing
        scheduled.status = 'processing'
        scheduled.save(update_fields=['status'])

        # Get recipients
        recipients = ScheduledNotificationService.get_target_users_extended(
            scheduled.target_type,
            scheduled.target_criteria
        )

        # Send notifications
        created_count = NotificationService.send_bulk_notification(
            recipients_queryset=recipients,
            notification_type=scheduled.notification_type,
            title=scheduled.title,
            message=scheduled.message,
            detail=scheduled.detail,
            sections=scheduled.sections,
            banner_image=scheduled.banner_image,
            icon=scheduled.icon,
            action_url=scheduled.action_url,
            action_text=scheduled.action_text,
            priority=scheduled.priority,
        )

        # Send companion emails if enabled
        emails_sent = 0
        emails_suppressed = 0
        if scheduled.send_email:
            if scheduled.email_body_markdown:
                # Legacy path for in-flight scheduled notifications with separate email content
                emails_sent, emails_suppressed = ScheduledNotificationService._send_broadcast_emails_legacy(
                    recipients=recipients,
                    title=scheduled.title,
                    email_subject=scheduled.email_subject,
                    email_body_markdown=scheduled.email_body_markdown,
                    action_url=scheduled.action_url,
                    action_text=scheduled.action_text,
                    email_cta_url=scheduled.email_cta_url,
                    email_cta_text=scheduled.email_cta_text,
                )
            else:
                emails_sent, emails_suppressed = ScheduledNotificationService._send_broadcast_emails(
                    recipients=recipients,
                    title=scheduled.title,
                    message=scheduled.message,
                    icon=scheduled.icon,
                    priority=scheduled.priority,
                    sections=scheduled.sections,
                    detail=scheduled.detail,
                    banner_image=scheduled.banner_image,
                    action_url=scheduled.action_url,
                    action_text=scheduled.action_text,
                )

        # Update scheduled notification
        scheduled.status = 'sent'
        scheduled.sent_at = timezone.now()
        scheduled.recipient_count = created_count
        scheduled.save(update_fields=['status', 'sent_at', 'recipient_count'])

        # Create log entry
        NotificationLog.objects.create(
            scheduled_notification=scheduled,
            notification_type=scheduled.notification_type,
            title=scheduled.title,
            message=scheduled.message,
            detail=scheduled.detail,
            target_type=scheduled.target_type,
            target_criteria=scheduled.target_criteria,
            recipient_count=created_count,
            sent_by=scheduled.created_by,
            was_scheduled=True,
            emails_sent=emails_sent,
            emails_suppressed=emails_suppressed,
        )

        email_info = f", {emails_sent} emails sent, {emails_suppressed} suppressed" if scheduled.send_email else ""
        logger.info(
            f"Scheduled notification {scheduled.id} sent: "
            f"'{scheduled.title}' to {created_count} recipients{email_info}"
        )

    @staticmethod
    def _send_broadcast_emails(
        recipients, title, message, icon, priority,
        sections, detail, banner_image,
        action_url, action_text,
    ):
        """
        Send broadcast emails that mirror in-app notification content.

        Renders the same title, message, sections, banner, and action button
        into a styled email. No separate email body required.

        Args:
            recipients: QuerySet of CustomUser instances
            title: Notification title (also used as email subject)
            message: Notification message body
            icon: Notification icon (emoji)
            priority: Priority level (normal/high/urgent)
            sections: List of section dicts (structured content)
            detail: Legacy markdown detail (fallback when no sections)
            banner_image: ImageField instance or None
            action_url: CTA URL or None
            action_text: CTA button text

        Returns:
            tuple: (emails_sent, emails_suppressed)
        """
        from django.conf import settings
        from core.services.email_service import EmailService
        from users.services.email_preference_service import EmailPreferenceService
        from notifications.services.broadcast_email_renderer import build_broadcast_email_context

        emails_sent = 0
        emails_suppressed = 0

        for user in recipients.select_related('profile').iterator(chunk_size=200):
            if not user.email:
                continue

            if not EmailPreferenceService.should_send_email(user, 'admin_announcements'):
                EmailService.log_suppressed(
                    email_type='admin_announcement',
                    user=user,
                    subject=title,
                    triggered_by='admin_manual',
                )
                emails_suppressed += 1
                continue

            try:
                preference_token = EmailPreferenceService.generate_preference_token(user.id)
                preference_url = f"{settings.SITE_URL}/users/email-preferences/?token={preference_token}"

                username = (
                    getattr(user, 'profile', None) and (
                        user.profile.display_psn_username or user.profile.psn_username
                    ) or user.email
                )

                context = build_broadcast_email_context(
                    title=title,
                    message=message,
                    icon=icon,
                    priority=priority,
                    sections=sections,
                    detail=detail,
                    banner_image=banner_image,
                    action_url=action_url,
                    action_text=action_text,
                    username=username,
                    site_url=settings.SITE_URL,
                    preference_url=preference_url,
                )

                sent = EmailService.send_html_email(
                    subject=title,
                    to_emails=[user.email],
                    template_name='emails/broadcast.html',
                    context=context,
                    fail_silently=True,
                    log_email_type='admin_announcement',
                    log_user=user,
                    log_triggered_by='admin_manual',
                )

                if sent:
                    emails_sent += 1
            except Exception:
                logger.exception(f"Failed to send broadcast email to {user.email}")

        logger.info(f"Broadcast email complete: {emails_sent} sent, {emails_suppressed} suppressed")
        return emails_sent, emails_suppressed

    @staticmethod
    def _send_broadcast_emails_legacy(
        recipients, title, email_subject, email_body_markdown,
        action_url, action_text, email_cta_url, email_cta_text,
    ):
        """
        Legacy broadcast email sender for in-flight scheduled notifications
        that were composed with separate email_body_markdown content.

        This can be removed once all legacy scheduled notifications have been processed.
        """
        from django.conf import settings
        from core.services.email_service import EmailService
        from users.services.email_preference_service import EmailPreferenceService
        import markdown

        subject = email_subject or title
        cta_url = email_cta_url or action_url or ''
        cta_text = email_cta_text or action_text or ''

        email_body_html = ''
        if email_body_markdown:
            try:
                email_body_html = markdown.markdown(
                    email_body_markdown,
                    extensions=['extra', 'nl2br', 'sane_lists'],
                )
            except Exception:
                logger.exception("Failed to render broadcast email markdown")
                from django.utils.html import escape
                email_body_html = escape(email_body_markdown).replace('\n', '<br>')

        emails_sent = 0
        emails_suppressed = 0

        for user in recipients.select_related('profile').iterator(chunk_size=200):
            if not user.email:
                continue

            if not EmailPreferenceService.should_send_email(user, 'admin_announcements'):
                EmailService.log_suppressed(
                    email_type='admin_announcement',
                    user=user,
                    subject=subject,
                    triggered_by='admin_manual',
                )
                emails_suppressed += 1
                continue

            try:
                preference_token = EmailPreferenceService.generate_preference_token(user.id)
                preference_url = f"{settings.SITE_URL}/users/email-preferences/?token={preference_token}"

                context = {
                    'username': getattr(user, 'profile', None) and (
                        user.profile.display_psn_username or user.profile.psn_username
                    ) or user.email,
                    'subject': subject,
                    'message_html': email_body_html,
                    'icon': '\U0001F4E2',
                    'priority': 'normal',
                    'is_urgent': False,
                    'accent_color': '#667eea',
                    'accent_gradient_start': '#667eea',
                    'accent_gradient_end': '#764ba2',
                    'has_sections': False,
                    'sections_html': '',
                    'detail_html': '',
                    'banner_image_url': None,
                    'cta_url': cta_url,
                    'cta_text': cta_text,
                    'site_url': settings.SITE_URL,
                    'preference_url': preference_url,
                }

                sent = EmailService.send_html_email(
                    subject=subject,
                    to_emails=[user.email],
                    template_name='emails/broadcast.html',
                    context=context,
                    fail_silently=True,
                    log_email_type='admin_announcement',
                    log_user=user,
                    log_triggered_by='admin_manual',
                )

                if sent:
                    emails_sent += 1
            except Exception:
                logger.exception(f"Failed to send broadcast email to {user.email}")

        logger.info(f"Legacy broadcast email complete: {emails_sent} sent, {emails_suppressed} suppressed")
        return emails_sent, emails_suppressed

    @staticmethod
    def cancel(scheduled_id, user):
        """
        Cancel a pending scheduled notification.

        Args:
            scheduled_id: ID of the ScheduledNotification
            user: CustomUser performing the cancellation

        Returns:
            bool: True if successfully cancelled, False otherwise
        """
        from notifications.models import ScheduledNotification

        try:
            scheduled = ScheduledNotification.objects.get(
                id=scheduled_id,
                status='pending'
            )
            scheduled.status = 'cancelled'
            scheduled.save(update_fields=['status'])

            logger.info(
                f"Scheduled notification {scheduled_id} cancelled by {user.email}"
            )
            return True
        except ScheduledNotification.DoesNotExist:
            logger.warning(
                f"Failed to cancel notification {scheduled_id}: "
                f"not found or not pending"
            )
            return False
