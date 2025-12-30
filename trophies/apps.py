from django.apps import AppConfig
import logging

logger = logging.getLogger(__name__)

class TrophiesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "trophies"

    def ready(self):
        from trophies.discord_utils.discord_notifications import webhook_sender_worker
        import threading
        import trophies.signals

        threading.Thread(target=webhook_sender_worker, daemon=True).start()
        logger.info("Started Discord webhook sender worker")