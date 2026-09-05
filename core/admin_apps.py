"""The app config that swaps Django's admin site for the superuser-only one.

Its own module rather than `core/apps.py`, and that is not tidiness: Django treats every
`AppConfig` subclass found in an `apps` module as a candidate default config, so importing
`AdminConfig` there in order to subclass it makes the module declare two defaults and refuse to
load. The failure is immediate and loud, but the fix is non-obvious enough to be worth a file and
this paragraph.
"""
from django.contrib.admin.apps import AdminConfig


class SuperuserOnlyAdminConfig(AdminConfig):
    """Replaces `django.contrib.admin` in INSTALLED_APPS, so `admin.site` IS the narrowed site.

    Django builds `admin.site` from this config's `default_site`, and every `@admin.register` in the
    project registers against `admin.site`. Pointing it here narrows the entire admin at once --
    including any ModelAdmin added later by somebody who has never read `core/admin_site.py`, which
    is the whole reason to do it at the site rather than per model.
    """
    default_site = 'core.admin_site.SuperuserOnlyAdminSite'
