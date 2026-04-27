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
    """Anonymous + no referrer + page_count <= 1, within the candidate set."""
    return list(
        candidates_qs
        .filter(page_count__lte=1, user_id__isnull=True)
        .filter(_no_referrer_q())
        .values_list("session_id", flat=True)
    )


def find_rule2_ip_burst(candidates_qs, window_start, window_end):
    """
    IPs with bot-like burst patterns in the window. Pattern detection looks
    at ALL sessions (including already-flagged) — that's evidence of how
    the IP behaves overall. Flagging only touches the candidate set.
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
        .values_list("ip_address", flat=True)
    )
    ips = list(suspicious_ips)
    if not ips:
        return []

    return list(
        candidates_qs
        .filter(ip_address__in=ips)
        .values_list("session_id", flat=True)
    )


def find_rule3_ua_spoofer(candidates_qs, window_start, window_end):
    """
    UAs that show up repeatedly in anon + no-ref + bounced sessions. Pattern
    detection over all sessions in the window (regardless of is_bot), but
    the supporting-evidence filter (anon + no-ref + bounced) is what keeps
    legit-but-popular UAs from being flagged.
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
        .values_list("user_agent", flat=True)
    )
    uas = list(suspicious_uas)
    if not uas:
        return []

    return list(
        candidates_qs
        .filter(user_agent__in=uas)
        .values_list("session_id", flat=True)
    )


# --- Orchestration ---------------------------------------------------------

def run_behavioral_classification(lookback_hours, dry_run=False, batch_size=5000):
    """
    Run all three rules over the lookback window. Returns
    {rule_name: count_flagged}.

    The candidate queryset is rebuilt between rules (it's lazy), so a session
    flagged by rule 1 won't be re-evaluated by rules 2 or 3. Pattern detection
    in rules 2 and 3 still sees those sessions as evidence of the IP/UA's
    bot-like behavior because pattern queries don't filter on is_bot.
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
        ids = finder()
        counts[name] = _flag(ids, dry_run=dry_run, batch_size=batch_size)
    return counts


def _flag(session_ids, *, dry_run, batch_size):
    """Mark sessions is_bot=True in batches. Returns total flagged."""
    if not session_ids:
        return 0
    if dry_run:
        return len(session_ids)

    from core.models import AnalyticsSession
    total = 0
    for i in range(0, len(session_ids), batch_size):
        chunk = session_ids[i:i + batch_size]
        total += AnalyticsSession.objects.filter(session_id__in=chunk).update(is_bot=True)
    return total
