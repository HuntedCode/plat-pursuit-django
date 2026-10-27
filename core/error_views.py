"""The site's error pages (Odds and ends review, OE-01).

Before these, only a 404 had a page. A refused request, a tripped rate limit and an expired form showed Django's
bare one line ("403 Forbidden", "Forbidden (403) CSRF verification failed. Request aborted.") with no site around
it and no way back.

WHO REACHES WHAT:
- `not_found` (handler404): any unknown URL or `Http404`.
- `permission_denied` (handler403): a `PermissionDenied` raised outside DRF. The common one is a tripped
  django-ratelimit limit with `block=True`: `Ratelimited` subclasses `PermissionDenied`, so without this handler a
  limit answered a bare 403. It is answered as what it is, a 429 with the cooldown page. DRF views never get here
  (DRF turns `PermissionDenied` into its own JSON 403).
- `throttled` (handler429): allauth's own limits (sign-in attempts, sign-up, password reset) call it through
  `allauth.core.ratelimit.respond_429`, which looks `handler429` up on the ROOT_URLCONF. Django itself never
  dispatches `handler429`.
- `csrf_failure` (settings.CSRF_FAILURE_VIEW): a form posted with a stale token, usually a sign-in page left open
  past its session.
- 400 and 500 are Django's own handlers rendering `400.html` / `500.html`. Those two templates stand alone (no
  `base.html`): a 500 renders with no context at all and may be the database being down, and a 400 is often a bad
  Host header, which `base.html`'s absolute URLs would raise on again.

JSON. A caller that wants JSON (an `/api/` path, or `Accept: application/json` from a fetch) gets `{'error': ...}`
with the same status instead of a page, because the site's fetch clients read `error`.
"""
from django.http import JsonResponse
from django.shortcuts import render
from django_ratelimit.exceptions import Ratelimited

THROTTLED_MESSAGE = 'Too many requests. Wait a minute and try again.'
FORBIDDEN_MESSAGE = "You don't have access to that."
CSRF_MESSAGE = 'This form expired. Refresh the page and try again.'

#: The pages these views render sit inside the site; errors/base_error.html falls back to the standalone shell
#: when nothing passes this (Django's own 400 / 500 renders).
SITE_SHELL = {'err_shell': 'base.html'}


def _wants_json(request):
    return request.path.startswith('/api/') or 'application/json' in request.headers.get('Accept', '')


def not_found(request, exception=None):
    return render(request, '404.html', SITE_SHELL, status=404)


def throttled(request, exception=None):
    if _wants_json(request):
        return JsonResponse({'error': THROTTLED_MESSAGE}, status=429)
    return render(request, '429.html', SITE_SHELL, status=429)


def permission_denied(request, exception=None):
    if isinstance(exception, Ratelimited):
        return throttled(request)
    if _wants_json(request):
        return JsonResponse({'error': FORBIDDEN_MESSAGE}, status=403)
    return render(request, '403.html', SITE_SHELL, status=403)


def csrf_failure(request, reason=''):
    if _wants_json(request):
        return JsonResponse({'error': CSRF_MESSAGE}, status=403)
    return render(request, '403_csrf.html', SITE_SHELL, status=403)
