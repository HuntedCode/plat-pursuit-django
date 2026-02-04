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
        )

        format_type = 'structured' if sections else 'markdown'
        logger.info(
            f"Immediate notification sent by {sent_by.email}: "
            f"'{title}' to {created_count} recipients ({format_type} format)"
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
                logger.error(
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
        )

        logger.info(
            f"Scheduled notification {scheduled.id} sent: "
            f"'{scheduled.title}' to {created_count} recipients"
        )

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
