"""Django admin, narrowed to superusers.

Django's default gate is `is_staff`, and the role split (2026-08) wires `is_staff` to `role='admin'`
through `CustomUser.save()`. So every Administrator could reach `/admin/` -- and `/admin/` is by some
distance the most dangerous surface on the site: roughly ninety bulk actions that mutate live data,
most of which write no audit entry at all, plus raw edit access to every model.

The owner's call (2026-09): Django admin is for the site owner, not for the admin team. Everyone
else uses the Admin Hub, whose actions are logged with a name and a reason.

`is_superuser` is the lever rather than a hardcoded username or id. It is Django-native, it is
explicit, it is grantable and revocable from the admin itself, and it does not rot when an account
is renamed or an email changes. A test would pass just as happily against a hardcoded id and the
site would be one database restore away from locking its owner out.

WHAT THIS DOES NOT CHANGE. `is_staff` still means "reaches the Admin Hub at /staff/", the role
lockstep is untouched, and moderators still have neither. The one thing that moved is the meaning of
`is_staff` with respect to `/admin/`, which `docs/features/marks-and-roles.md` records.

Wired via `AdminConfig.default_site` (see `core/apps.py` and INSTALLED_APPS) rather than by
reassigning `admin.site`. Every `@admin.register` in the project registers against `admin.site`, so
swapping the instance Django builds is the only way to do this without touching all of them -- and
the only way that cannot be bypassed by a ModelAdmin registered somewhere I did not look.
"""
from django.contrib.admin import AdminSite


class SuperuserOnlyAdminSite(AdminSite):
    """The Django admin, for superusers only."""

    def has_permission(self, request):
        """The single gate every admin view goes through.

        Overriding HERE rather than in middleware or per-ModelAdmin: `AdminSite.admin_view` wraps
        every view this site serves, including the ones a third-party app adds later. A middleware
        matching on the URL prefix would be a second place to keep in step, and a per-ModelAdmin
        `has_view_permission` would have to be repeated on every one of the fifty-odd classes and
        would still leave the index, the login page and the auth views open.
        """
        user = request.user
        return bool(user.is_active and user.is_superuser)
