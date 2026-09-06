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

Wired via `AdminConfig.default_site` (see `core/admin_apps.py` and INSTALLED_APPS) rather than by
reassigning `admin.site`. Every `@admin.register` in the project registers against `admin.site`, so
swapping the instance Django builds is the only way to do this without touching all of them -- and
the only way that cannot be bypassed by a ModelAdmin registered somewhere I did not look.
"""
from django.contrib.admin import AdminSite
from django.contrib.auth.views import redirect_to_login
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme


class SuperuserOnlyAdminSite(AdminSite):
    """The Django admin, for superusers only, with no login form of its own."""

    def has_permission(self, request):
        """The gate every admin view goes through -- every one except `login/`, which is why the
        method below exists.

        Overriding HERE rather than in middleware or per-ModelAdmin: `AdminSite.admin_view` wraps
        every other view this site serves, including the ones a third-party app adds later (djstripe
        contributes 36 custom action URLs, all correctly wrapped). Middleware matching a URL prefix
        would be a second place to keep in step, and a per-ModelAdmin `has_view_permission` would
        have to be repeated on every one of ~140 registrations and would still leave the index, the
        app lists and the auth views open.

        `is_staff` as well as `is_superuser`: Django's admin LOGIN FORM refuses a non-staff account
        outright, so without this an account with `is_superuser` and no `is_staff` would be admitted
        here and rejected there -- a corner where the code, the form and the docs each said something
        different. `create_superuser` sets both, and `CustomUser.save()`'s demotion branches all
        short-circuit on `is_superuser`, so the owner cannot lose either by a role edit.
        """
        user = request.user
        return bool(user.is_active and user.is_staff and user.is_superuser)

    def login(self, request, extra_context=None):
        """There is no Django-admin login form on this site. Sign in at the front door.

        `login/` is the ONE entry in `AdminSite.get_urls()` that is not wrapped in `admin_view`
        (Django marks it `@login_not_required`), so narrowing `has_permission` did nothing to it and
        the docstring above used to claim otherwise. Left alone it is:

          A SECOND, UNTHROTTLED CREDENTIAL ENDPOINT. `ACCOUNT_RATE_LIMITS` throttles allauth's login
          view; this one is Django's own and nothing rate-limits it. It accepts any account's real
          password and mints the same session cookie the rest of the site uses, so it is a
          rate-limit bypass for password guessing against the entire user base. It is also outside
          `CloudflareOriginGuardMiddleware`'s path list, so an attacker who knows the origin reaches
          it with no WAF in front.

          A STAFF ORACLE. It answers 200 for a staff account and 200-with-an-error for everyone
          else, and redirects an already-privileged GET -- enough to confirm "this address is staff"
          in one unauthenticated request.

          AND THE EXACT EXPERIENCE THIS CHANGE EXISTS TO PREVENT: an Administrator with `/admin/`
          bookmarked would log in SUCCESSFULLY here and land back on the login form with no message.

        Redirecting instead of rendering removes all three. One login form on the site, one place
        that throttles, one place to reason about.

        WHO IS ASKING decides where they go, and getting that wrong is a redirect LOOP rather than a
        cosmetic slip -- which is what the first cut of this method did. Sending an authenticated
        visitor to the login page with `next=/admin/` bounces them to a form that redirects an
        already-signed-in user straight back to `next`, forever. Signing in again cannot grant a
        permission they do not have, so:

          anonymous     -> the site's login, with `next` so the owner lands where they meant to
          signed in     -> back to a page that is actually theirs, once. No loop, no dead form.

        `next` is filtered to our own paths under `/admin/`, so this cannot be turned into an open
        redirect or used to bounce somebody somewhere surprising after signing in.
        """
        user = request.user
        if user.is_authenticated:
            # `is_staff` rather than "is an admin": the Admin Hub is where their tools are, and
            # anyone else has no business being pointed at a staff surface at all.
            return redirect(reverse('admin_hub') if user.is_staff else '/')

        requested = request.GET.get('next') or ''
        safe = (requested.startswith('/admin/')
                and url_has_allowed_host_and_scheme(
                    requested, allowed_hosts={request.get_host()},
                    require_https=request.is_secure()))
        return redirect_to_login(requested if safe else reverse('admin:index'),
                                 reverse('account_login'))
