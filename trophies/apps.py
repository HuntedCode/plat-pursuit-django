from django.apps import AppConfig


class TrophiesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "trophies"

    def ready(self):
        import trophies.signals