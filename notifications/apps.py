from django.apps import AppConfig
import logging

logger = logging.getLogger(__name__)


class NotificationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "notifications"

    def ready(self):
        """
        Register signal handlers when the app is ready.
        This ensures signals are connected when Django starts.
        """
        try:
            logger.info("[NOTIFICATIONS] NotificationsConfig.ready() called")
            import notifications.signals  # noqa: F401
            logger.info("[NOTIFICATIONS] Signal handlers imported successfully")
        except Exception as e:
            logger.error(f"[NOTIFICATIONS] Failed to import signal handlers: {e}", exc_info=True)
