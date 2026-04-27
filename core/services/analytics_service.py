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
import re
from datetime import timedelta
from urllib.parse import urlparse

from django.db.models import Count, OuterRef, Q, Subquery, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

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


def resolve_range(range_key):
    """Map a range querystring value to (start_dt, end_dt, label, prior_start_dt)."""
    range_key = range_key if range_key in RANGE_OPTIONS else DEFAULT_RANGE
    now = timezone.now()
    days = RANGE_OPTIONS[range_key]
    if days is None:
        return {
            "key": range_key,
            "label": "All time",
            "start": None,
            "end": now,
            "prior_start": None,
            "prior_end": None,
        }
    start = now - timedelta(days=days)
    return {
        "key": range_key,
        "label": f"Last {days} days",
        "start": start,
        "end": now,
        "prior_start": start - timedelta(days=days),
        "prior_end": start,
    }


def _session_qs(start, end):
    from core.models import AnalyticsSession
    qs = AnalyticsSession.objects.all()
    if start is not None:
        qs = qs.filter(created_at__gte=start)
    if end is not None:
        qs = qs.filter(created_at__lt=end)
    return qs


def _pageview_qs(start, end):
    from core.models import PageView
    qs = PageView.objects.all()
    if start is not None:
        qs = qs.filter(viewed_at__gte=start)
    if end is not None:
        qs = qs.filter(viewed_at__lt=end)
    return qs


def _siteevent_qs(start, end):
    from core.models import SiteEvent
    qs = SiteEvent.objects.all()
    if start is not None:
        qs = qs.filter(occurred_at__gte=start)
    if end is not None:
        qs = qs.filter(occurred_at__lt=end)
    return qs


def _compute_totals(start, end):
    """Headline metrics for a given window."""
    sessions = _session_qs(start, end)
    pageview_count = _pageview_qs(start, end).count()

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
    }


def _compute_delta(current, prior):
    """Return signed percentage change from prior to current, or None if prior is missing/zero."""
    if not prior:
        return None
    if prior == 0:
        return None
    delta = (current - prior) / prior * 100
    return round(delta, 1)


def _build_trend(start, end):
    """Daily session and pageview counts. Returns list of {date, sessions, pageviews}."""
    if start is None:
        # All-time: derive a span from the earliest record so we don't blow up the chart.
        from core.models import AnalyticsSession
        earliest = AnalyticsSession.objects.order_by("created_at").values_list("created_at", flat=True).first()
        if not earliest:
            return []
        start = earliest

    sessions_by_day = (
        _session_qs(start, end)
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(c=Count("session_id"))
        .order_by("day")
    )
    pageviews_by_day = (
        _pageview_qs(start, end)
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


def _top_pages(start, end, page_type_filter=None, limit=20):
    qs = _pageview_qs(start, end)
    if page_type_filter:
        qs = qs.filter(page_type=page_type_filter)

    rows = list(
        qs.values("page_type", "object_id")
        .annotate(views=Count("id"), unique_sessions=Count("session_id", distinct=True))
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


def _top_referrers(start, end, limit=15):
    """Group sessions by referrer hostname. Direct = no referrer."""
    qs = _session_qs(start, end).values_list("referrer", flat=True)

    host_counts = {}
    for ref in qs.iterator():
        host = _referrer_host(ref)
        host_counts[host] = host_counts.get(host, 0) + 1

    rows = sorted(host_counts.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    total = sum(host_counts.values()) or 1
    return [
        {"host": host, "sessions": count, "pct": round(count / total * 100, 1)}
        for host, count in rows
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


def _site_events_summary(start, end):
    qs = _siteevent_qs(start, end)
    rows = list(
        qs.values("event_type")
        .annotate(c=Count("id"))
        .order_by("-c")
    )
    return rows


def _recap_funnel(start, end):
    """Recap-specific funnel: page_view -> share_generate -> image_download."""
    qs = _siteevent_qs(start, end)
    page_views = qs.filter(event_type="recap_page_view").count()
    shares = qs.filter(event_type="recap_share_generate").count()
    downloads = qs.filter(event_type="recap_image_download").count()

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


def _bounce_by_page_type(start, end, limit=20):
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


def _top_entry_or_exit_pages(start, end, *, exit_page=False, limit=15):
    """
    Top first/last pages per session. Uses Postgres DISTINCT ON over
    (session_id) ordered by viewed_at ASC (entry) or DESC (exit), then
    aggregates by (page_type, object_id) in Python — small dataset,
    keeps the query simple.
    """
    qs = _pageview_qs(start, end)
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

_BOT_PATTERNS = re.compile(
    r"bot|spider|crawler|slurp|scrape|fetch|preview|lighthouse|pingdom|"
    r"facebookexternalhit|twitterbot|linkedinbot|discordbot|telegrambot|"
    r"whatsapp|skypeuripreview|curl|wget|python-|http-client|java/|go-http-client|"
    r"headlesschrome|phantomjs|httpx",
    re.IGNORECASE,
)


def _parse_user_agent(ua):
    """Categorize a UA string into device + browser + is_bot. Empty/missing -> Unknown."""
    if not ua:
        return {"device": "Unknown", "browser": "Unknown", "is_bot": False}

    is_bot = bool(_BOT_PATTERNS.search(ua))
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


def _device_browser_breakdown(start, end):
    """Aggregate device + browser categories across sessions in window."""
    qs = _session_qs(start, end).values_list("user_agent", flat=True)

    device_counts = {}
    browser_counts = {}
    bot_count = 0
    total = 0
    for ua in qs.iterator():
        parsed = _parse_user_agent(ua)
        device_counts[parsed["device"]] = device_counts.get(parsed["device"], 0) + 1
        browser_counts[parsed["browser"]] = browser_counts.get(parsed["browser"], 0) + 1
        if parsed["is_bot"]:
            bot_count += 1
        total += 1

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


def get_dashboard_data(range_key=DEFAULT_RANGE, page_type_filter=None):
    """Top-level entry point. Returns the full payload for the analytics dashboard."""
    window = resolve_range(range_key)
    totals = _compute_totals(window["start"], window["end"])

    delta_keys = ("sessions", "pageviews", "bounce_rate_pct", "avg_pages_per_session", "authed_pct")
    if window["prior_start"] is not None:
        prior_totals = _compute_totals(window["prior_start"], window["prior_end"])
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
        "trend": _build_trend(window["start"], window["end"]),
        "top_pages": _top_pages(window["start"], window["end"], page_type_filter=page_type_filter),
        "top_referrers": _top_referrers(window["start"], window["end"]),
        "site_events": _site_events_summary(window["start"], window["end"]),
        "recap_funnel": _recap_funnel(window["start"], window["end"]),
        "bounce_by_section": _bounce_by_page_type(window["start"], window["end"]),
        "top_entry_pages": _top_entry_or_exit_pages(window["start"], window["end"], exit_page=False),
        "top_exit_pages": _top_entry_or_exit_pages(window["start"], window["end"], exit_page=True),
        "device_browser": _device_browser_breakdown(window["start"], window["end"]),
        "page_type_filter": page_type_filter,
        "page_type_choices": PAGE_TYPE_CHOICES,
        "range_options": [
            {"key": "7d", "label": "Last 7 days"},
            {"key": "30d", "label": "Last 30 days"},
            {"key": "90d", "label": "Last 90 days"},
            {"key": "all", "label": "All time"},
        ],
    }
