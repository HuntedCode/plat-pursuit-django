import logging
import re
import time

from django.http import HttpResponseRedirect, HttpResponsePermanentRedirect
from django.utils import timezone
from django.conf import settings
import pytz
import threading

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Per-request memory observability.
#
# Two complementary signals:
#
# 1. HEAVY_REQUEST (post-response) — fires when a single request grew RSS
#    by more than HEAVY_REQUEST_MB. Tells us which view caused the spike,
#    *if* the request finishes. Misses the killing request itself because
#    SIGKILL skips Python's `finally`.
#
# 2. REQUEST_START_HOT (pre-response) — fires when the worker is already
#    above DANGER_RSS_MB at request entry. Once the worker has crossed
#    that threshold, every subsequent request's path is logged on entry,
#    so the last line before a gunicorn restart points at the request
#    that pushed the worker over the OOM ceiling — the one (1) misses.
#
# Both read RSS from /proc/self/status (Linux only); on dev OSes that
# lack /proc the read returns 0 and the middleware is a silent no-op.
# Below DANGER_RSS_MB the start logger stays quiet (zero per-request
# overhead beyond the /proc read), so steady-state traffic doesn't flood
# logs.
# ──────────────────────────────────────────────────────────────────────

_HEAVY_REQUEST_MB = 50
_DANGER_RSS_MB = 300


def _read_rss_kb():
    """Return this process's resident-set size in KB, or 0 on non-Linux."""
    try:
        with open('/proc/self/status', 'r') as f:
            for line in f:
                if line.startswith('VmRSS:'):
                    return int(line.split()[1])
    except (FileNotFoundError, OSError, ValueError):
        pass
    return 0


class MemoryDeltaMiddleware:
    """Logs memory observability lines for OOM forensics.

    HEAVY_REQUEST fires post-response for requests that allocated >50 MB:
        HEAVY_REQUEST path=/foo/bar/ method=GET delta_mb=287.4 duration_ms=44824

    REQUEST_START_HOT fires pre-response when worker RSS is already above
    300 MB at request entry — i.e., the worker is in the danger zone and
    every following request is potentially the OOM trigger:
        REQUEST_START_HOT path=/foo/bar/ method=GET rss_mb=412.7

    Search Render logs for both tags after any OOM event. The last
    REQUEST_START_HOT line before a `Starting gunicorn` event identifies
    the request that pushed the worker over its memory ceiling.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        rss_before_kb = _read_rss_kb()
        if rss_before_kb and (rss_before_kb / 1024.0) >= _DANGER_RSS_MB:
            logger.info(
                'REQUEST_START_HOT path=%s method=%s rss_mb=%.1f',
                request.path,
                request.method,
                rss_before_kb / 1024.0,
            )
        start = time.monotonic()
        try:
            return self.get_response(request)
        finally:
            rss_after_kb = _read_rss_kb()
            if rss_before_kb and rss_after_kb:
                delta_mb = (rss_after_kb - rss_before_kb) / 1024.0
                if delta_mb >= _HEAVY_REQUEST_MB:
                    duration_ms = int((time.monotonic() - start) * 1000)
                    logger.info(
                        'HEAVY_REQUEST path=%s method=%s delta_mb=%.1f duration_ms=%d',
                        request.path,
                        request.method,
                        delta_mb,
                        duration_ms,
                    )

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


# Paths that the Cloudflare origin guard protects. Scoped narrowly to the
# profile-scoped detail views that dominate crash-time traffic in Render logs;
# other routes (home, static assets, health checks on `/`) are intentionally
# left alone so a misconfigured proxy cannot lock us out of our own site.
_CLOUDFLARE_GUARDED_PATH_RE = re.compile(
    r'^/(?:games/[^/]+|(?:my-pursuit/badges|badges|achievements/badges)/[^/]+)/[^/]+/?$'
)

# Public front door. Direct-origin requests for guarded paths are bounced
# back to this host so they re-enter through Cloudflare's proxy, where Bot
# Fight Mode and WAF rules can evaluate them.
_CLOUDFLARE_PUBLIC_ORIGIN = 'https://platpursuit.com'


class CloudflareOriginGuardMiddleware:
    """Bounce direct-origin requests for guarded paths back through Cloudflare.

    Every request that actually transits Cloudflare carries a `CF-Ray` header.
    A request for a guarded path that *lacks* this header reached Django by
    skipping the proxy entirely — typically a scraper that cached the origin
    IP during the window when the public Render subdomain exposed it. We
    redirect those hits to the public hostname so Cloudflare has a chance to
    inspect them (Bot Fight Mode, Managed Rules, etc.) before they reach the
    expensive view code.

    The guard is deliberately narrow (profile-scoped detail URLs only): we do
    not want a CF outage to lock legitimate users out of the rest of the
    site, and Render's own health checks hit `/` internally without a
    CF-Ray header.

    Fires an INFO log per catch with a grep-friendly `CF_BYPASS_BLOCKED`
    prefix so operators can spot-check how much direct-origin traffic is
    being funneled back through the proxy.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Dev-local requests never transit Cloudflare, so the CF-Ray check
        # would always fail and bounce localhost traffic to production. Skip
        # the guard entirely when DEBUG is on.
        if settings.DEBUG:
            return self.get_response(request)
        if (
            _CLOUDFLARE_GUARDED_PATH_RE.match(request.path)
            and not request.META.get('HTTP_CF_RAY')
        ):
            ua = request.META.get('HTTP_USER_AGENT', '')
            logger.info(
                'CF_BYPASS_BLOCKED path=%s ip=%s ua=%r',
                request.path,
                request.META.get('REMOTE_ADDR', ''),
                ua[:120],
            )
            target = f'{_CLOUDFLARE_PUBLIC_ORIGIN}{request.path}'
            qs = request.META.get('QUERY_STRING', '')
            if qs:
                target = f'{target}?{qs}'
            # 302, not 301: we don't want scrapers or caches to memorize this
            # as a permanent move — behavior depends on runtime CF-Ray state.
            return HttpResponseRedirect(target)
        return self.get_response(request)


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