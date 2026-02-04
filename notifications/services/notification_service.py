"""
NotificationService - Core notification service with static methods.
Follows the service layer pattern used in badge_service.py.
"""
from django.db import transaction
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.conf import settings
from notifications.models import Notification, NotificationTemplate
from notifications.services.notification_cache_service import NotificationCacheService

CustomUser = get_user_model()


class NotificationService:
    """Core notification service for creating and managing notifications."""

    @staticmethod
    @transaction.atomic
    def create_notification(recipient, notification_type, title, message, **kwargs):
        """
        Create a notification for a user.

        Args:
            recipient: CustomUser instance
            notification_type: Type of notification (from NOTIFICATION_TYPES choices)
            title: Notification title (max 255 chars)
            message: Notification message (max 1000 chars)
            **kwargs: Optional fields:
                - detail: Rich text detail content (max 2500 chars, supports markdown)
                - sections: List of structured section dicts
                - banner_image: ImageField instance for banner image
                - icon: Icon/emoji for notification (default: '🔔')
                - action_url: Optional URL for action button
                - action_text: Optional text for action button
                - priority: Priority level (default: 'normal')
                - metadata: JSONField for additional data
                - template: NotificationTemplate instance

        Returns:
            Notification instance
        """
        notification = Notification.objects.create(
            recipient=recipient,
            notification_type=notification_type,
            title=title[:255],  # Ensure max length
            message=message[:1000],  # Ensure max length
            detail=kwargs.get('detail', '')[:2500],  # Ensure max length
            sections=kwargs.get('sections', []),
            banner_image=kwargs.get('banner_image', None),
            icon=kwargs.get('icon', '🔔'),
            action_url=kwargs.get('action_url', None),
            action_text=kwargs.get('action_text', ''),
            priority=kwargs.get('priority', 'normal'),
            metadata=kwargs.get('metadata', {}),
            template=kwargs.get('template', None),
        )

        # Invalidate cache for recipient (if caching enabled)
        if getattr(settings, 'NOTIFICATION_CACHE_ENABLED', True):
            NotificationCacheService.invalidate_all_for_user(recipient.id)

        return notification

    @staticmethod
    def create_from_template(recipient, template, context):
        """
        Create notification from template with variable substitution.

        Args:
            recipient: CustomUser instance
            template: NotificationTemplate instance
            context: Dict with variables like {'username': 'John', 'game_name': 'Elden Ring'}

        Returns:
            Notification instance or None if template rendering fails
        """
        from notifications.services.template_service import TemplateService

        try:
            # Render template with context
            rendered = TemplateService.render_template(template, context)

            # Create notification with rendered content
            notification = NotificationService.create_notification(
                recipient=recipient,
                notification_type=template.notification_type,
                title=rendered['title'],
                message=rendered['message'],
                icon=template.icon,
                action_url=rendered.get('action_url'),
                action_text=template.action_text,
                priority=template.priority,
                metadata=context,  # Store original context in metadata
                template=template,
            )

            return notification
        except Exception as e:
            # Log error but don't crash
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Failed to create notification from template {template.name}: {e}")
            return None

    @staticmethod
    @transaction.atomic
    def send_bulk_notification(recipients_queryset, notification_type, title, message, **kwargs):
        """
        Create notifications for multiple users efficiently using bulk_create.

        Args:
            recipients_queryset: QuerySet of CustomUser instances
            notification_type: Type of notification
            title: Notification title
            message: Notification message
            **kwargs: Same optional fields as create_notification()
                Note: banner_image and sections will be shared across all notifications

        Returns:
            int: Number of notifications created
        """
        # Build list of Notification objects
        notifications = []
        detail = kwargs.get('detail', '')[:2500]
        sections = kwargs.get('sections', [])
        banner_image = kwargs.get('banner_image', None)
        icon = kwargs.get('icon', '🔔')
        action_url = kwargs.get('action_url', None)
        action_text = kwargs.get('action_text', '')
        priority = kwargs.get('priority', 'normal')
        metadata = kwargs.get('metadata', {})
        template = kwargs.get('template', None)

        for recipient in recipients_queryset:
            notifications.append(
                Notification(
                    recipient=recipient,
                    notification_type=notification_type,
                    title=title[:255],
                    message=message[:1000],
                    detail=detail,
                    sections=sections,
                    banner_image=banner_image,
                    icon=icon,
                    action_url=action_url,
                    action_text=action_text,
                    priority=priority,
                    metadata=metadata,
                    template=template,
                )
            )

        # Bulk create with batch size for performance
        created = Notification.objects.bulk_create(notifications, batch_size=500)

        # Invalidate cache for all recipients (if caching enabled)
        if getattr(settings, 'NOTIFICATION_CACHE_ENABLED', True):
            recipient_ids = [recipient.id for recipient in recipients_queryset]
            NotificationCacheService.invalidate_unread_counts_bulk(recipient_ids)

        return len(created)

    @staticmethod
    def mark_as_read(notification_id, user):
        """
        Mark notification as read.

        Args:
            notification_id: ID of notification to mark as read
            user: CustomUser instance (for permission check)

        Returns:
            bool: True if successful, False otherwise
        """
        try:
            notification = Notification.objects.get(
                id=notification_id,
                recipient=user
            )
            notification.mark_as_read()

            # Invalidate unread count cache (if caching enabled)
            if getattr(settings, 'NOTIFICATION_CACHE_ENABLED', True):
                NotificationCacheService.invalidate_unread_count(user.id)

            return True
        except Notification.DoesNotExist:
            return False

    @staticmethod
    @transaction.atomic
    def mark_all_as_read(user):
        """
        Mark all unread notifications as read for a user.

        Args:
            user: CustomUser instance

        Returns:
            int: Number of notifications marked as read
        """
        count = Notification.objects.filter(
            recipient=user,
            is_read=False
        ).update(
            is_read=True,
            read_at=timezone.now()
        )

        # Invalidate unread count cache (if caching enabled)
        if getattr(settings, 'NOTIFICATION_CACHE_ENABLED', True):
            NotificationCacheService.invalidate_unread_count(user.id)

        return count

    @staticmethod
    def get_user_notifications(user, unread_only=False, notification_type='', limit=10, offset=0):
        """
        Get paginated notifications for a user.

        Args:
            user: CustomUser instance
            unread_only: If True, only return unread notifications
            notification_type: Filter by notification type (optional)
            limit: Number of notifications to return (default: 10)
            offset: Offset for pagination (default: 0)

        Returns:
            tuple: (queryset, total_count)
        """
        queryset = Notification.objects.filter(recipient=user)

        if unread_only:
            queryset = queryset.filter(is_read=False)

        if notification_type:
            queryset = queryset.filter(notification_type=notification_type)

        total_count = queryset.count()

        # Apply pagination
        queryset = queryset.select_related('template')[offset:offset + limit]

        return queryset, total_count

    @staticmethod
    def get_unread_count(user):
        """
        Get count of unread notifications for a user with caching.

        Args:
            user: CustomUser instance

        Returns:
            int: Number of unread notifications
        """
        # Check if caching is enabled
        if not getattr(settings, 'NOTIFICATION_CACHE_ENABLED', True):
            # Caching disabled - query database directly
            return Notification.objects.filter(
                recipient=user,
                is_read=False
            ).count()

        # Try to get from cache first
        count = NotificationCacheService.get_cached_unread_count(user.id)

        if count is None:
            # Cache miss - query database
            count = Notification.objects.filter(
                recipient=user,
                is_read=False
            ).count()

            # Cache the result
            NotificationCacheService.cache_unread_count(user.id, count)

        return count

    @staticmethod
    def get_target_users(target_type, **kwargs):
        """
        Get queryset of users based on target type.

        Args:
            target_type: One of 'all', 'premium', 'premium_plus', 'role', 'individual'
            **kwargs: Additional filtering options:
                - user_ids: List of user IDs (for 'individual' target_type)
                - role: Role name (for 'role' target_type)

        Returns:
            QuerySet of CustomUser instances
        """
        if target_type == 'all':
            return CustomUser.objects.filter(is_active=True)

        elif target_type == 'premium':
            # Users with any active subscription
            return CustomUser.objects.filter(
                is_active=True,
                subscription__status='active'
            ).distinct()

        elif target_type == 'premium_plus':
            # Users with Premium Plus subscription
            return CustomUser.objects.filter(
                is_active=True,
                subscription__status='active',
                subscription__plan__name='Premium Plus'
            ).distinct()

        elif target_type == 'individual':
            # Specific users by ID
            user_ids = kwargs.get('user_ids', [])
            return CustomUser.objects.filter(
                id__in=user_ids,
                is_active=True
            )

        elif target_type == 'role':
            # Users with specific role (staff, superuser, etc.)
            role = kwargs.get('role', '')
            if role == 'staff':
                return CustomUser.objects.filter(is_staff=True, is_active=True)
            elif role == 'superuser':
                return CustomUser.objects.filter(is_superuser=True, is_active=True)
            else:
                return CustomUser.objects.none()

        else:
            return CustomUser.objects.none()
