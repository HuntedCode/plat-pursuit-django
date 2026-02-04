"""
Notification caching service.

This module handles Redis caching for notification-related data to reduce
database load, including unread counts and recent notifications.
"""
from django.core.cache import cache
import logging

logger = logging.getLogger(__name__)


class NotificationCacheService:
    """Handles caching for notification data using Redis."""

    # Cache timeouts
    UNREAD_COUNT_TIMEOUT = 300  # 5 minutes
    RECENT_NOTIFICATIONS_TIMEOUT = 60  # 1 minute

    @staticmethod
    def get_cached_unread_count(user_id):
        """
        Get cached unread notification count for a user.

        Checks cache first, returns None if not cached (caller should query DB).

        Args:
            user_id: User ID to get count for

        Returns:
            int or None: Unread count if cached, None if not in cache

        Example:
            >>> count = NotificationCacheService.get_cached_unread_count(user.id)
            >>> if count is None:
            ...     count = Notification.objects.filter(recipient=user, is_read=False).count()
            ...     NotificationCacheService.cache_unread_count(user.id, count)
        """
        cache_key = f"notification:unread_count:{user_id}"
        return cache.get(cache_key)

    @staticmethod
    def cache_unread_count(user_id, count):
        """
        Cache unread notification count for a user.

        Args:
            user_id: User ID
            count: Unread notification count

        Example:
            >>> NotificationCacheService.cache_unread_count(user.id, 5)
        """
        cache_key = f"notification:unread_count:{user_id}"
        cache.set(cache_key, count, NotificationCacheService.UNREAD_COUNT_TIMEOUT)
        logger.debug(f"Cached unread count for user {user_id}: {count}")

    @staticmethod
    def invalidate_unread_count(user_id):
        """
        Invalidate cached unread count for a user.

        Call this when:
        - A new notification is created for the user
        - A notification is marked as read
        - Notifications are bulk marked as read

        Args:
            user_id: User ID to invalidate cache for

        Example:
            >>> NotificationCacheService.invalidate_unread_count(user.id)
        """
        cache_key = f"notification:unread_count:{user_id}"
        cache.delete(cache_key)
        logger.debug(f"Invalidated unread count cache for user {user_id}")

    @staticmethod
    def invalidate_unread_counts_bulk(user_ids):
        """
        Invalidate cached unread counts for multiple users.

        Used for bulk notification operations.

        Args:
            user_ids: List of user IDs

        Example:
            >>> NotificationCacheService.invalidate_unread_counts_bulk([1, 2, 3])
        """
        if not user_ids:
            return

        cache_keys = [f"notification:unread_count:{uid}" for uid in user_ids]
        cache.delete_many(cache_keys)
        logger.debug(f"Invalidated unread count cache for {len(user_ids)} users")

    @staticmethod
    def get_cached_notifications(user_id):
        """
        Get cached recent notifications for a user.

        Returns cached notification list if available.

        Args:
            user_id: User ID

        Returns:
            list or None: List of notification dicts if cached, None otherwise

        Example:
            >>> notifications = NotificationCacheService.get_cached_notifications(user.id)
        """
        cache_key = f"notification:recent:{user_id}"
        return cache.get(cache_key)

    @staticmethod
    def cache_notifications(user_id, notifications_data):
        """
        Cache recent notifications for a user.

        Args:
            user_id: User ID
            notifications_data: List of notification dicts

        Example:
            >>> NotificationCacheService.cache_notifications(user.id, notification_list)
        """
        cache_key = f"notification:recent:{user_id}"
        cache.set(cache_key, notifications_data, NotificationCacheService.RECENT_NOTIFICATIONS_TIMEOUT)
        logger.debug(f"Cached {len(notifications_data)} notifications for user {user_id}")

    @staticmethod
    def invalidate_notifications_cache(user_id):
        """
        Invalidate cached notification list for a user.

        Call this when:
        - A new notification is created for the user
        - A notification is deleted

        Args:
            user_id: User ID to invalidate cache for

        Example:
            >>> NotificationCacheService.invalidate_notifications_cache(user.id)
        """
        cache_key = f"notification:recent:{user_id}"
        cache.delete(cache_key)
        logger.debug(f"Invalidated notifications cache for user {user_id}")

    @staticmethod
    def invalidate_all_for_user(user_id):
        """
        Invalidate all cached notification data for a user.

        Convenience method to clear both unread count and notification list.

        Args:
            user_id: User ID

        Example:
            >>> NotificationCacheService.invalidate_all_for_user(user.id)
        """
        NotificationCacheService.invalidate_unread_count(user_id)
        NotificationCacheService.invalidate_notifications_cache(user_id)
        logger.debug(f"Invalidated all notification caches for user {user_id}")
