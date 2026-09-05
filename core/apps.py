from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

# The admin app config lives in `core/admin_apps.py`, NOT here. Django treats every AppConfig
# subclass in an `apps` module as a candidate default, so importing `AdminConfig` to subclass it
# made this module declare two and refuse to load.
