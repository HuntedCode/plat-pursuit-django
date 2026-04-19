import re

from django.http import HttpResponsePermanentRedirect
from django.utils import timezone
from django.conf import settings
import pytz
import threading

# Thread-local storage for current request
_thread_locals = threading.local()

def get_current_request():
    """Get the current request from thread-local storage."""
    return getattr(_thread_locals, 'request', None)


_BOT_UA_RE = re.compile(
    r'('
    r'meta-webindexer|meta-externalagent|facebookexternalhit|'
    r'googlebot|google-extended|bingbot|duckduckbot|oai-searchbot|'
    r'bytespider|tiktokspider|'
    r'claudebot|claude-searchbot|anthropic-ai|gptbot|ccbot|perplexitybot|amazonbot|'
    r'semrushbot|ahrefsbot|mj12bot|dotbot|serankingbacklinksbot'
    r')',
    re.IGNORECASE,
)

_PROFILE_SCOPED_PATH_RE = re.compile(
    r'^/(games/[^/]+|my-pursuit/badges/[^/]+)/[^/]+/?$'
)


class BotCanonicalRedirectMiddleware:
    """301-redirect bot requests for profile-scoped variants to canonical URLs.

    static/robots.txt already Disallows /games/*/* and /my-pursuit/badges/*/*
    for everyone. This middleware enforces that policy for bots that ignore
    robots.txt (meta-webindexer, etc.) by sending them to the canonical URL.
    Profile-scoped pages are expensive (per-profile EarnedTrophy queries,
    milestone dicts) while serving og/twitter metadata that is profile-
    independent, so link-preview crawlers render identically either way and
    crawl budget consolidates on the canonical URL.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        ua = request.META.get('HTTP_USER_AGENT', '')
        if ua and _BOT_UA_RE.search(ua):
            match = _PROFILE_SCOPED_PATH_RE.match(request.path)
            if match:
                canonical = f'/{match.group(1)}/'
                qs = request.META.get('QUERY_STRING', '')
                if qs:
                    canonical = f'{canonical}?{qs}'
                return HttpResponsePermanentRedirect(canonical)
        return self.get_response(request)


class TimezoneMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Store request in thread-local storage
        _thread_locals.request = request

        tzname = settings.TIME_ZONE
        if request.user.is_authenticated:
            tzname = request.user.user_timezone
        timezone.activate(pytz.timezone(tzname))

        response = self.get_response(request)

        # Clean up thread-local storage
        if hasattr(_thread_locals, 'request'):
            del _thread_locals.request

        return response