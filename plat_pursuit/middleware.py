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
    r'claudebot|claude-searchbot|anthropic-ai|gptbot|ccbot|perplexitybot|'
    r'amazonbot|amzn-searchbot|'
    r'semrushbot|ahrefsbot|mj12bot|dotbot|serankingbacklinksbot|barkrowler'
    r')',
    re.IGNORECASE,
)

# Each rule maps a profile-scoped URL shape to its canonical target. The badge
# rule intentionally covers the legacy /badges/<slug>/ and /achievements/badges/
# /<slug>/ prefixes alongside the canonical /my-pursuit/badges/<slug>/, because
# crawlers often still follow old backlinks to those prefixes. Short-circuiting
# to the canonical target in one hop avoids a two-hop redirect chain through
# the existing legacy 301s in plat_pursuit/urls.py.
_BOT_REDIRECT_RULES = (
    (re.compile(r'^/games/([^/]+)/[^/]+/?$'), '/games/{slug}/'),
    (
        re.compile(r'^/(?:my-pursuit/badges|badges|achievements/badges)/([^/]+)/[^/]+/?$'),
        '/my-pursuit/badges/{slug}/',
    ),
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
            for pattern, canonical_template in _BOT_REDIRECT_RULES:
                match = pattern.match(request.path)
                if match:
                    canonical = canonical_template.format(slug=match.group(1))
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