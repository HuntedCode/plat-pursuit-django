from django.apps import AppConfig


class NotificationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "notifications"

    def ready(self):
        """Register signal handlers.

        UNGUARDED ON PURPOSE, matching `trophies/apps.py`. This used to sit in a broad try/except that
        logged and continued -- so if `notifications.signals` (or anything it imports at module level)
        ever failed to import, EVERY notification signal silently unregistered and the site booted
        green. `manage.py check` passed, the suite passed, and platinum notifications simply stopped
        being created, with the only evidence one ERROR line in a startup log.

        A signal module that cannot import is a deploy error. Failing to boot is the correct, loud
        outcome; a running site that quietly does less is not.
        """
        import notifications.signals  # noqa: F401
