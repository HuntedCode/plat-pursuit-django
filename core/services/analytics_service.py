"""
Staff analytics dashboard aggregation service.

Reads existing AnalyticsSession / PageView / SiteEvent data; no schema changes,
no client-side beacons. Powers /staff/analytics/.

Still deferred (would need schema or client changes):
- Time-on-page / scroll depth (needs visibilitychange beacon + new dwell_ms field)
- Geo from IP (needs MaxMind DB or external API)
- Funnel builder UI (real feature work)
- Cohort retention matrix (heavy query layer)
- Real-time / live view (needs websockets or polling endpoint)
"""
import logging
from datetime import timedelta
from urllib.parse import urlparse

from django.core.cache import cache
from django.db.models import Count, OuterRef, Q, Subquery, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from core.services.bot_detection import is_bot_user_agent

CACHE_TTL = 300  # 5 minutes
CACHE_PREFIX = "staff_analytics_dashboard"

logger = logging.getLogger("psn_api")

RANGE_OPTIONS = {
    "7d": 7,
    "30d": 30,
    "90d": 90,
    "all": None,
}
DEFAULT_RANGE = "30d"

PAGE_TYPE_CHOICES = [
    ("profile", "Profile"),
    ("game", "Game"),
    ("guide", "Guide"),
    ("badge", "Badge Series"),
    ("index", "Home"),
    ("profiles_list", "Profiles List"),
    ("games_list", "Games List"),
    ("trophies_list", "Trophies List"),
    ("badges_list", "Badges List"),
    ("guides_list", "Guides List"),
    ("milestones_list", "Milestones List"),
    ("badge_leaderboard", "Badge Leaderboard"),
    ("overall_leaderboard", "Overall Leaderboard"),
    ("trophy_case", "Trophy Case"),
    ("my_guides", "My Guides"),
    ("my_shareables", "My Shareables"),
    ("guide_edit", "Guide Edit"),
    ("settings", "Settings"),
    ("subscription", "Subscription"),
    ("email_prefs", "Email Prefs"),
    ("notifications", "Notifications"),
    ("recap", "Monthly Recap"),
    ("game_list", "Game List"),
    ("game_lists_browse", "Game Lists Browse"),
    ("my_lists", "My Lists"),
    ("game_list_edit", "Game List Edit"),
    ("challenges_browse", "Challenges Browse"),
    ("my_challenges", "My Challenges"),
    ("az_challenge", "A-Z Challenge"),
    ("az_challenge_setup", "A-Z Setup"),
    ("az_challenge_edit", "A-Z Edit"),
]


DEFAULT_LAG_HOURS = 24
MAX_LAG_HOURS = 168  # 7 days; sanity ceiling


def resolve_range(range_key, exclude_recent_hours=DEFAULT_LAG_HOURS):
    """Map a range querystring value to a window dict.

    `exclude_recent_hours` shifts the entire window backward by that many hours,
    so the recent un-classified bot tail (sessions that haven't run through the
    behavioral cron yet) doesn't pollute the report. The full N-day window is
    preserved; it just ends `exclude_recent_hours` ago instead of `now`. The
    prior comparison window also shifts so the delta math stays apples-to-apples.

    Clamped to [0, MAX_LAG_HOURS]. 0 disables the buffer (shows today's data).
    """
    range_key = range_key if range_key in RANGE_OPTIONS else DEFAULT_RANGE
    try:
        lag_hours = int(exclude_recent_hours)
    except (TypeError, ValueError):
        lag_hours = DEFAULT_LAG_HOURS
    lag_hours = max(0, min(MAX_LAG_HOURS, lag_hours))

    now = timezone.now()
    end = now - timedelta(hours=lag_hours)
    days = RANGE_OPTIONS[range_key]

    suffix = f" (ending {lag_hours}h ago)" if lag_hours else ""

    if days is None:
        return {
            "key": range_key,
            "label": f"All time{suffix}",
            "start": None,
            "end": end,
            "prior_start": None,
            "prior_end": None,
            "exclude_recent_hours": lag_hours,
        }
    start = end - timedelta(days=days)
    return {
        "key": range_key,
        "label": f"Last {days} days{suffix}",
        "start": start,
        "end": end,
        "prior_start": start - timedelta(days=days),
        "prior_end": start,
        "exclude_recent_hours": lag_hours,
    }


def _session_qs(start, end, include_bots=False):
    from core.models import AnalyticsSession
    qs = AnalyticsSession.objects.all()
    if start is not None:
        qs = qs.filter(created_at__gte=start)
    if end is not None:
        qs = qs.filter(created_at__lt=end)
    if not include_bots:
        qs = qs.filter(is_bot=False)
    return qs


def _pageview_qs(start, end, include_bots=False):
    """
    PageView queryset for the dashboard window.

    Going forward, bot sessions never produce PageView rows
    (track_page_view skips them). So is_bot filtering only matters for
    historical pollution. We filter to PageViews whose session is non-bot
    via a subquery; the human-session set is the smaller list (bots
    dominate by count), which keeps the IN clause tight.
    """
    from core.models import AnalyticsSession, PageView
    qs = PageView.objects.all()
    if start is not None:
        qs = qs.filter(viewed_at__gte=start)
    if end is not None:
        qs = qs.filter(viewed_at__lt=end)
    if not include_bots:
        human_session_ids = AnalyticsSession.objects.filter(
            is_bot=False
        ).values("session_id")
        qs = qs.filter(session_id__in=human_session_ids)
    return qs


def _siteevent_qs(start, end, include_bots=False):
    """
    SiteEvent has no session_id link, so we can't filter by bot status.
    track_site_event already skips bots going forward; historical
    pollution from before the deploy is minimal because events require
    explicit user actions (challenge create, recap share, etc.) that
    bots typically can't trigger. The include_bots argument is accepted
    for API consistency with the other querysets but has no effect.
    """
    from core.models import SiteEvent
    qs = SiteEvent.objects.all()
    if start is not None:
        qs = qs.filter(occurred_at__gte=start)
    if end is not None:
        qs = qs.filter(occurred_at__lt=end)
    return qs


def _compute_totals(start, end, include_bots=False):
    """Headline metrics for a given window.

    Always computes bot_session_count too, regardless of include_bots, so the
    dashboard can always show "X bot sessions filtered out": that signal is
    useful even when bots are excluded from headline numbers.
    """
    sessions = _session_qs(start, end, include_bots=include_bots)
    pageview_count = _pageview_qs(start, end, include_bots=include_bots).count()

    # Independent bot-session count for transparency. Indexed, cheap.
    bot_qs = _session_qs(start, end, include_bots=True).filter(is_bot=True)
    bot_session_count = bot_qs.count()

    agg = sessions.aggregate(
        total=Count("session_id"),
        bounced=Count("session_id", filter=Q(page_count__lte=1)),
        authed=Count("session_id", filter=Q(user_id__isnull=False)),
        anon=Count("session_id", filter=Q(user_id__isnull=True)),
        page_sum=Sum("page_count"),
    )
    total = agg["total"] or 0
    bounced = agg["bounced"] or 0
    authed = agg["authed"] or 0
    anon = agg["anon"] or 0
    page_sum = agg["page_sum"] or 0

    bounce_rate = (bounced / total * 100) if total else 0.0
    avg_pages = (page_sum / total) if total else 0.0
    authed_pct = (authed / total * 100) if total else 0.0
    anon_pct = (anon / total * 100) if total else 0.0

    return {
        "sessions": total,
        "pageviews": pageview_count,
        "bounce_rate_pct": round(bounce_rate, 1),
        "avg_pages_per_session": round(avg_pages, 2),
        "authed_count": authed,
        "anon_count": anon,
        "authed_pct": round(authed_pct, 1),
        "anon_pct": round(anon_pct, 1),
        "bot_session_count": bot_session_count,
    }


def _compute_delta(current, prior):
    """Return signed percentage change from prior to current, or None if prior is missing/zero."""
    if not prior:
        return None
    if prior == 0:
        return None
    delta = (current - prior) / prior * 100
    return round(delta, 1)


def _build_trend(start, end, include_bots=False):
    """Daily session and pageview counts. Returns list of {date, sessions, pageviews}."""
    if start is None:
        # All-time: derive a span from the earliest record so we don't blow up the chart.
        from core.models import AnalyticsSession
        earliest_qs = AnalyticsSession.objects.all()
        if not include_bots:
            earliest_qs = earliest_qs.filter(is_bot=False)
        earliest = earliest_qs.order_by("created_at").values_list("created_at", flat=True).first()
        if not earliest:
            return []
        start = earliest

    sessions_by_day = (
        _session_qs(start, end, include_bots=include_bots)
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(c=Count("session_id"))
        .order_by("day")
    )
    pageviews_by_day = (
        _pageview_qs(start, end, include_bots=include_bots)
        .annotate(day=TruncDate("viewed_at"))
        .values("day")
        .annotate(c=Count("id"))
        .order_by("day")
    )

    s_map = {row["day"]: row["c"] for row in sessions_by_day}
    p_map = {row["day"]: row["c"] for row in pageviews_by_day}

    # Build a continuous series across the full range (zeros for empty days).
    out = []
    cur = start.date()
    last = end.date()
    while cur <= last:
        out.append({
            "date": cur.isoformat(),
            "sessions": s_map.get(cur, 0),
            "pageviews": p_map.get(cur, 0),
        })
        cur += timedelta(days=1)
    return out


def _top_pages(start, end, page_type_filter=None, limit=20, include_bots=False):
    """
    Top pages by view count.

    Note: we don't compute COUNT(DISTINCT session_id) here. The PageView dedup
    layer enforces one row per session per page per 30-min window, so views
    and unique sessions are effectively the same number on this table. The
    DISTINCT count was very expensive and added no signal.
    """
    qs = _pageview_qs(start, end, include_bots=include_bots)
    if page_type_filter:
        qs = qs.filter(page_type=page_type_filter)

    rows = list(
        qs.values("page_type", "object_id")
        .annotate(views=Count("id"))
        .order_by("-views")[:limit]
    )
    return _attach_object_labels(rows)


def _attach_object_labels(rows):
    """Resolve object_id -> human-readable label for the page_types that have a parent model."""
    if not rows:
        return rows

    # Group object_ids by page_type for batched lookups
    grouped = {}
    for r in rows:
        grouped.setdefault(r["page_type"], set()).add(r["object_id"])

    label_map = {}  # (page_type, object_id) -> label

    if "profile" in grouped:
        from trophies.models import Profile
        ids = [int(x) for x in grouped["profile"] if str(x).isdigit()]
        for p in Profile.objects.filter(id__in=ids).only("id", "psn_username", "display_psn_username"):
            label_map[("profile", str(p.id))] = p.display_psn_username or p.psn_username or f"Profile {p.id}"

    if "game" in grouped:
        from trophies.models import Game
        ids = [int(x) for x in grouped["game"] if str(x).isdigit()]
        for g in Game.objects.filter(id__in=ids).only("id", "title_name"):
            label_map[("game", str(g.id))] = g.title_name or f"Game {g.id}"

    if "guide" in grouped:
        from trophies.models import Checklist
        ids = [int(x) for x in grouped["guide"] if str(x).isdigit()]
        for c in Checklist.objects.filter(id__in=ids).only("id", "title"):
            label_map[("guide", str(c.id))] = c.title or f"Guide {c.id}"

    if "badge" in grouped:
        from trophies.models import Badge
        slugs = list(grouped["badge"])
        for b in Badge.objects.filter(series_slug__in=slugs, tier=1).only("series_slug", "name"):
            label_map[("badge", b.series_slug)] = b.name or b.series_slug

    if "game_list" in grouped:
        from trophies.models import GameList
        ids = [int(x) for x in grouped["game_list"] if str(x).isdigit()]
        for gl in GameList.objects.filter(id__in=ids).only("id", "name"):
            label_map[("game_list", str(gl.id))] = gl.name or f"List {gl.id}"

    if "az_challenge" in grouped:
        from trophies.models import Challenge
        ids = [int(x) for x in grouped["az_challenge"] if str(x).isdigit()]
        for ch in Challenge.objects.filter(id__in=ids).only("id", "name"):
            label_map[("az_challenge", str(ch.id))] = ch.name or f"Challenge {ch.id}"

    if "index" in grouped:
        for oid in grouped["index"]:
            label_map[("index", oid)] = "Home"

    for r in rows:
        key = (r["page_type"], r["object_id"])
        r["object_label"] = label_map.get(key, r["object_id"])
    return rows


def _top_referrers(start, end, limit=15, include_bots=False):
    """
    Group sessions by referrer hostname.

    Performance: GROUP BY in SQL on raw referrer URL first, THEN parse the host
    in Python only over the deduplicated URL list. A site with millions of
    sessions typically has only a few hundred unique referrer URLs, so we
    iterate hundreds of rows in Python instead of millions.
    """
    rows = (
        _session_qs(start, end, include_bots=include_bots)
        .values("referrer")
        .annotate(c=Count("session_id"))
        .order_by("-c")
    )

    host_counts = {}
    total = 0
    for row in rows.iterator():
        host = _referrer_host(row["referrer"])
        host_counts[host] = host_counts.get(host, 0) + row["c"]
        total += row["c"]

    sorted_hosts = sorted(host_counts.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    denom = total or 1
    return [
        {"host": host, "sessions": count, "pct": round(count / denom * 100, 1)}
        for host, count in sorted_hosts
    ]


def _referrer_host(ref):
    """Extract a clean hostname from a referrer URL. Returns 'Direct / unknown' if absent."""
    if not ref:
        return "Direct / unknown"
    try:
        parsed = urlparse(ref)
        host = (parsed.netloc or "").lower()
        if not host:
            return "Direct / unknown"
        # Strip "www."
        if host.startswith("www."):
            host = host[4:]
        return host
    except Exception:
        return "Direct / unknown"


def _site_events_summary(start, end, include_bots=False):
    qs = _siteevent_qs(start, end, include_bots=include_bots)
    rows = list(
        qs.values("event_type")
        .annotate(c=Count("id"))
        .order_by("-c")
    )
    return rows


def _recap_funnel(start, end, include_bots=False):
    """Recap-specific funnel: page_view -> share_generate -> image_download.

    Single query with conditional aggregates instead of three separate counts.
    """
    agg = _siteevent_qs(start, end, include_bots=include_bots).aggregate(
        page_views=Count("id", filter=Q(event_type="recap_page_view")),
        shares=Count("id", filter=Q(event_type="recap_share_generate")),
        downloads=Count("id", filter=Q(event_type="recap_image_download")),
    )
    page_views = agg["page_views"] or 0
    shares = agg["shares"] or 0
    downloads = agg["downloads"] or 0

    def pct(num, den):
        if not den:
            return None
        return round(num / den * 100, 1)

    return {
        "page_views": page_views,
        "share_generate": shares,
        "image_download": downloads,
        "pv_to_share_pct": pct(shares, page_views),
        "share_to_dl_pct": pct(downloads, shares),
        "pv_to_dl_pct": pct(downloads, page_views),
    }


def _bounce_by_page_type(start, end, limit=20, include_bots=False):
    """
    For each entry page_type, how many sessions started there and how many bounced.
    Bounce = AnalyticsSession.page_count <= 1. The entry page is the earliest
    PageView for that session within the window.

    One SQL query: correlated Subquery picks the first PageView's page_type per
    session, GROUP BY aggregates session counts and bounce counts.
    """
    from core.models import AnalyticsSession, PageView

    first_pv_subq = (
        PageView.objects.filter(session_id=OuterRef("session_id"))
        .order_by("viewed_at")
        .values("page_type")[:1]
    )

    sessions = AnalyticsSession.objects.all()
    if start is not None:
        sessions = sessions.filter(created_at__gte=start)
    if end is not None:
        sessions = sessions.filter(created_at__lt=end)
    if not include_bots:
        sessions = sessions.filter(is_bot=False)

    rows = list(
        sessions.annotate(entry_pt=Subquery(first_pv_subq))
        .filter(entry_pt__isnull=False)
        .values("entry_pt")
        .annotate(
            sessions=Count("session_id"),
            bounced=Count("session_id", filter=Q(page_count__lte=1)),
        )
        .order_by("-sessions")[:limit]
    )

    for r in rows:
        s = r["sessions"] or 0
        r["page_type"] = r.pop("entry_pt")
        r["bounce_rate_pct"] = round(r["bounced"] / s * 100, 1) if s else 0.0
    return rows


def _top_entry_or_exit_pages(start, end, *, exit_page=False, limit=15, include_bots=False):
    """
    Top first/last pages per session. Uses Postgres DISTINCT ON over
    (session_id) ordered by viewed_at ASC (entry) or DESC (exit), then
    aggregates by (page_type, object_id) in Python (small dataset,
    keeps the query simple).
    """
    qs = _pageview_qs(start, end, include_bots=include_bots)
    if exit_page:
        qs = qs.order_by("session_id", "-viewed_at")
    else:
        qs = qs.order_by("session_id", "viewed_at")

    pairs = qs.distinct("session_id").values_list("page_type", "object_id")

    counts = {}
    for pt, oid in pairs.iterator():
        key = (pt, oid)
        counts[key] = counts.get(key, 0) + 1

    sorted_pairs = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    rows = [
        {"page_type": pt, "object_id": oid, "views": c, "unique_sessions": c}
        for (pt, oid), c in sorted_pairs
    ]
    return _attach_object_labels(rows)


# --- User-agent parsing -----------------------------------------------------
#
# Lightweight regex-based UA categorization. No external dependency.
# Order matters: Edge UA contains "Chrome", Opera UA contains "Chrome", so the
# more-specific browser must be checked first. Same for tablet vs mobile.
#
# Bot detection itself lives in core.services.bot_detection (shared with
# the middleware, backfill command, and tracking writers).


def _parse_user_agent(ua):
    """Categorize a UA string into device + browser + is_bot."""
    is_bot = is_bot_user_agent(ua)
    if not ua:
        return {"device": "Bot" if is_bot else "Unknown",
                "browser": "Bot" if is_bot else "Unknown",
                "is_bot": is_bot}

    ua_l = ua.lower()

    if is_bot:
        device = "Bot"
        browser = "Bot"
    else:
        if "ipad" in ua_l or "tablet" in ua_l or ("android" in ua_l and "mobile" not in ua_l):
            device = "Tablet"
        elif "iphone" in ua_l or "android" in ua_l or "mobile" in ua_l:
            device = "Mobile"
        else:
            device = "Desktop"

        if "edg/" in ua_l or "edge/" in ua_l:
            browser = "Edge"
        elif "opr/" in ua_l or "opera" in ua_l:
            browser = "Opera"
        elif "firefox" in ua_l or "fxios" in ua_l:
            browser = "Firefox"
        elif "chrome" in ua_l or "crios" in ua_l:
            browser = "Chrome"
        elif "safari" in ua_l:
            browser = "Safari"
        else:
            browser = "Other"

    return {"device": device, "browser": browser, "is_bot": is_bot}


def _device_browser_breakdown(start, end, include_bots=False):
    """
    Aggregate device + browser categories across sessions in window.

    Performance: GROUP BY user_agent in SQL first, then parse each unique UA
    string ONCE in Python and multiply by the row count. Most sessions share
    a UA with thousands of others, so this is O(unique UAs) instead of
    O(sessions).

    When include_bots=False (default), the bot_count surfaced here will be
    zero because the underlying queryset has already filtered them out. To
    see bot share, the caller passes include_bots=True (i.e. the
    "include bots" toggle on the dashboard).
    """
    rows = (
        _session_qs(start, end, include_bots=include_bots)
        .values("user_agent")
        .annotate(c=Count("session_id"))
    )

    device_counts = {}
    browser_counts = {}
    bot_count = 0
    total = 0
    for row in rows.iterator():
        parsed = _parse_user_agent(row["user_agent"])
        c = row["c"]
        device_counts[parsed["device"]] = device_counts.get(parsed["device"], 0) + c
        browser_counts[parsed["browser"]] = browser_counts.get(parsed["browser"], 0) + c
        if parsed["is_bot"]:
            bot_count += c
        total += c

    def _format(counts):
        items = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
        return [
            {"name": name, "count": c, "pct": round(c / total * 100, 1) if total else 0}
            for name, c in items
        ]

    return {
        "devices": _format(device_counts),
        "browsers": _format(browser_counts),
        "bot_count": bot_count,
        "bot_pct": round(bot_count / total * 100, 1) if total else 0,
        "human_sessions": total - bot_count,
        "total_sessions": total,
    }


def get_dashboard_data(range_key=DEFAULT_RANGE, page_type_filter=None,
                       include_bots=False, exclude_recent_hours=DEFAULT_LAG_HOURS,
                       force_refresh=False):
    """
    Top-level entry point. Returns the full payload for the analytics dashboard.

    Cached in Redis for 5 minutes per (range, page_type_filter, include_bots,
    exclude_recent_hours) tuple. Pass `force_refresh=True` (e.g. via
    `?refresh=1` on the page) to bypass.
    """
    cache_key = (
        f"{CACHE_PREFIX}:{range_key}:{page_type_filter or 'all'}"
        f":bots={'1' if include_bots else '0'}"
        f":lag={exclude_recent_hours}"
    )
    if not force_refresh:
        cached = cache.get(cache_key)
        if cached is not None:
            cached["from_cache"] = True
            return cached

    data = _compute_dashboard_data(
        range_key, page_type_filter, include_bots, exclude_recent_hours,
    )
    try:
        cache.set(cache_key, data, CACHE_TTL)
    except Exception:
        # If the cache backend rejects the payload (e.g. window has datetimes
        # that don't pickle in some configs), just skip caching this call.
        logger.exception("Failed to cache analytics dashboard payload")
    data["from_cache"] = False
    return data


def _compute_dashboard_data(range_key, page_type_filter, include_bots, exclude_recent_hours):
    """Uncached compute path. Separate from the public function so the cache
    wrapper stays trivial."""
    window = resolve_range(range_key, exclude_recent_hours=exclude_recent_hours)
    totals = _compute_totals(window["start"], window["end"], include_bots=include_bots)

    delta_keys = ("sessions", "pageviews", "bounce_rate_pct", "avg_pages_per_session", "authed_pct")
    if window["prior_start"] is not None:
        prior_totals = _compute_totals(
            window["prior_start"], window["prior_end"], include_bots=include_bots
        )
        deltas = {k: _compute_delta(totals[k], prior_totals[k]) for k in delta_keys}
    else:
        # All-time has no prior period. Populate with explicit Nones so template
        # `is not None` checks resolve correctly (Django turns missing dict keys
        # into "" by default, which would falsely pass the is-not-None check).
        prior_totals = None
        deltas = {k: None for k in delta_keys}

    return {
        "window": window,
        "totals": totals,
        "prior_totals": prior_totals,
        "deltas": deltas,
        "include_bots": include_bots,
        "trend": _build_trend(window["start"], window["end"], include_bots=include_bots),
        "top_pages": _top_pages(
            window["start"], window["end"],
            page_type_filter=page_type_filter, include_bots=include_bots,
        ),
        "top_referrers": _top_referrers(window["start"], window["end"], include_bots=include_bots),
        "site_events": _site_events_summary(window["start"], window["end"], include_bots=include_bots),
        "recap_funnel": _recap_funnel(window["start"], window["end"], include_bots=include_bots),
        "bounce_by_section": _bounce_by_page_type(
            window["start"], window["end"], include_bots=include_bots,
        ),
        "top_entry_pages": _top_entry_or_exit_pages(
            window["start"], window["end"], exit_page=False, include_bots=include_bots,
        ),
        "top_exit_pages": _top_entry_or_exit_pages(
            window["start"], window["end"], exit_page=True, include_bots=include_bots,
        ),
        "device_browser": _device_browser_breakdown(
            window["start"], window["end"], include_bots=include_bots,
        ),
        "page_type_filter": page_type_filter,
        "page_type_choices": PAGE_TYPE_CHOICES,
        "range_options": [
            {"key": "7d", "label": "Last 7 days"},
            {"key": "30d", "label": "Last 30 days"},
            {"key": "90d", "label": "Last 90 days"},
            {"key": "all", "label": "All time"},
        ],
    }
