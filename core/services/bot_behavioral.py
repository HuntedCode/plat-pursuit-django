"""
Behavioral bot detection — catches UA-spoofing bots that survived the regex
classifier in core.services.bot_detection.

Three rules, each pure: takes a candidate queryset of unflagged sessions plus
a (start, end) window, returns a list of session_ids to flag. The management
command flag_behavioral_bots runs them in order and reports the counts.

Rules:
    Rule 1 (no_ref_bounce): anonymous + no referrer + page_count <= 1
        The classic scraper "drive-by" pattern. Conservative-aggressive — also
        catches real users who hit the homepage via bookmark and bounce, but
        the false-positive volume is small relative to the signal.

    Rule 2 (ip_burst): same IP, > N sessions, < M distinct UAs in window
        Catches scrapers that rotate UA strings from a single IP. Real shared
        IPs (corporate / school NAT) are protected by the diversity check —
        many users behind one IP have many different UAs.

    Rule 3 (ua_spoofer): UA appears in > X anonymous + no-ref + bounced sessions
        Catches scraper farms using a spoofed UA across many IPs. Pattern
        detection requires anon + no-ref + bounced as supporting evidence so
        a legitimate-but-popular UA isn't accidentally flagged.

Sessions younger than SESSION_AGE_BUFFER_MIN are skipped — page_count is only
final after the 30-min session timeout, so flagging in-progress sessions
would race with the middleware's page-view writers.
"""
from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone

# --- Tunable thresholds ----------------------------------------------------

RULE2_MIN_SESSIONS_PER_IP = 20
RULE2_MAX_UAS_PER_IP = 3

RULE3_MIN_SESSIONS_PER_UA = 100

# Don't classify sessions younger than this; page_count isn't final yet.
SESSION_AGE_BUFFER_MIN = 30


# --- Rule implementations --------------------------------------------------

def _no_referrer_q():
    """Match referrer IS NULL OR referrer = ''."""
    return Q(referrer__isnull=True) | Q(referrer="")


def find_rule1_no_ref_bounce(candidates_qs):
    """Anonymous + no referrer + page_count <= 1, within the candidate set.

    Returns a queryset (not materialized) so the caller can count or update
    it server-side without ever pulling session_ids into Python memory.
    """
    return (
        candidates_qs
        .filter(page_count__lte=1, user_id__isnull=True)
        .filter(_no_referrer_q())
    )


def find_rule2_ip_burst(candidates_qs, window_start, window_end):
    """
    IPs with bot-like burst patterns in the window. Pattern detection looks
    at ALL sessions (including already-flagged) — that's evidence of how
    the IP behaves overall. Flagging only touches the candidate set.

    Returns a queryset; the suspicious-IPs subquery becomes a SQL subquery
    in the IN clause, no Python materialization at any layer.
    """
    from core.models import AnalyticsSession

    suspicious_ips = (
        AnalyticsSession.objects
        .filter(
            created_at__gte=window_start,
            created_at__lt=window_end,
            ip_address__isnull=False,
        )
        .values("ip_address")
        .annotate(
            session_count=Count("session_id"),
            ua_count=Count("user_agent", distinct=True),
        )
        .filter(
            session_count__gt=RULE2_MIN_SESSIONS_PER_IP,
            ua_count__lt=RULE2_MAX_UAS_PER_IP,
        )
        .values("ip_address")
    )
    return candidates_qs.filter(ip_address__in=suspicious_ips)


def find_rule3_ua_spoofer(candidates_qs, window_start, window_end):
    """
    UAs that show up repeatedly in anon + no-ref + bounced sessions. Pattern
    detection over all sessions in the window (regardless of is_bot), but
    the supporting-evidence filter (anon + no-ref + bounced) is what keeps
    legit-but-popular UAs from being flagged.

    Returns a queryset; the suspicious-UAs subquery becomes a SQL subquery.
    """
    from core.models import AnalyticsSession

    suspicious_uas = (
        AnalyticsSession.objects
        .filter(
            created_at__gte=window_start,
            created_at__lt=window_end,
            user_id__isnull=True,
            page_count__lte=1,
        )
        .filter(_no_referrer_q())
        .values("user_agent")
        .annotate(c=Count("session_id"))
        .filter(c__gt=RULE3_MIN_SESSIONS_PER_UA)
        .values("user_agent")
    )
    return candidates_qs.filter(user_agent__in=suspicious_uas)


# --- Orchestration ---------------------------------------------------------

def run_behavioral_classification(lookback_hours, dry_run=False):
    """
    Run all three rules over the lookback window. Returns
    {rule_name: count_flagged}.

    Each rule produces a queryset of sessions to flag; we either count it
    (dry_run) or update is_bot=True on it (live). All work happens in
    Postgres — no session_id list is ever materialized in Python, so this
    is safe to run over months of data on a constrained webserver.

    The candidate queryset is rebuilt between rules (it's lazy), so a
    session flagged by rule 1 won't be re-evaluated by rules 2 or 3.
    Pattern detection in rules 2 and 3 still sees those sessions as
    evidence of the IP/UA's bot-like behavior because pattern queries
    don't filter on is_bot.
    """
    from core.models import AnalyticsSession

    now = timezone.now()
    window_end = now - timedelta(minutes=SESSION_AGE_BUFFER_MIN)
    window_start = now - timedelta(hours=lookback_hours)

    def _candidates():
        return AnalyticsSession.objects.filter(
            is_bot=False,
            created_at__gte=window_start,
            created_at__lt=window_end,
        )

    rules = [
        ("rule1_no_ref_bounce", lambda: find_rule1_no_ref_bounce(_candidates())),
        ("rule2_ip_burst", lambda: find_rule2_ip_burst(_candidates(), window_start, window_end)),
        ("rule3_ua_spoofer", lambda: find_rule3_ua_spoofer(_candidates(), window_start, window_end)),
    ]

    counts = {}
    for name, finder in rules:
        target_qs = finder()
        if dry_run:
            counts[name] = target_qs.count()
        else:
            counts[name] = target_qs.update(is_bot=True)
    return counts
