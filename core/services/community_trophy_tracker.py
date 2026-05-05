"""
Community Trophy Tracker: daily aggregate of trophy activity from
Discord-linked profiles, posted to Discord at ~12:30 PM ET via webhook.

Compute is invoked by the post_community_trophy_tracker management command
(once per day for the previous ET day) and by the /api/community-stats/today/
endpoint (live, 60s-cached). Records detection compares against historical
CommunityTrophyDay rows.

PP Score formula: trophies + (5 * platinums) + (3 * ultra_rares).
Weights are applied at compute time and frozen on the row, so retroactive
weight changes do not affect stored history.
"""
import logging
from datetime import date as date_cls, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db.models import Count, Max, Q
from django.utils import timezone

from core.models import CommunityTrophyDay
from trophies.models import EarnedTrophy, Profile

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

PP_SCORE_TROPHY_WEIGHT = 1
PP_SCORE_PLATINUM_WEIGHT = 5
PP_SCORE_ULTRA_RARE_WEIGHT = 3

EMBED_COLOR_DEFAULT = 0x003791  # Platinum brand blue
EMBED_COLOR_RECORD = 0xFFD700   # Gold (any new record)

DATA_FRESHNESS_NOTE = (
    "Counts may lag up to ~12h as Discord-linked profiles sync. "
    "Yesterday's daily summary is the canonical record."
)


def et_day_bounds(target_date: date_cls) -> tuple[datetime, datetime]:
    """Return (lo, hi) timezone-aware datetimes covering the ET calendar day.

    The bounds are the inclusive lower and exclusive upper edges in ET. Django's
    ORM converts these to UTC at query time, so DST transitions (23h or 25h
    days) are handled correctly without manual offset math.
    """
    lo_et = datetime.combine(target_date, time.min, tzinfo=ET)
    hi_et = lo_et + timedelta(days=1)
    return lo_et, hi_et


def eligible_profile_count() -> int:
    """How many profiles currently have Discord linked."""
    return Profile.objects.filter(discord_id__isnull=False).count()


def compute_day_stats(target_date: date_cls) -> dict:
    """Compute (don't store) the 4 stats for the given ET date.

    Returns a dict with total_trophies, total_platinums, total_ultra_rares,
    pp_score. One DB query with three conditional Count aggregates.
    """
    lo, hi = et_day_bounds(target_date)

    aggregates = EarnedTrophy.objects.filter(
        earned=True,
        earned_date_time__gte=lo,
        earned_date_time__lt=hi,
        profile__discord_id__isnull=False,
        trophy__game__shovelware_status='clean',
    ).aggregate(
        total_trophies=Count('id'),
        total_platinums=Count('id', filter=Q(trophy__trophy_type='platinum')),
        total_ultra_rares=Count('id', filter=Q(trophy__trophy_rarity=0)),
    )

    total_trophies = aggregates['total_trophies'] or 0
    total_platinums = aggregates['total_platinums'] or 0
    total_ultra_rares = aggregates['total_ultra_rares'] or 0

    pp_score = (
        PP_SCORE_TROPHY_WEIGHT * total_trophies
        + PP_SCORE_PLATINUM_WEIGHT * total_platinums
        + PP_SCORE_ULTRA_RARE_WEIGHT * total_ultra_rares
    )

    return {
        'total_trophies': total_trophies,
        'total_platinums': total_platinums,
        'total_ultra_rares': total_ultra_rares,
        'pp_score': pp_score,
    }


def get_current_records(exclude_pk: int | None = None) -> dict:
    """Return the historical maxima for each tracked stat.

    If exclude_pk is provided, that row is excluded from the comparison. This
    is used at post time so a new row that breaks records can be compared
    against prior history rather than itself.

    Each value is None when no qualifying historical rows exist (i.e. day 1).
    """
    qs = CommunityTrophyDay.objects.all()
    if exclude_pk is not None:
        qs = qs.exclude(pk=exclude_pk)

    return qs.aggregate(
        max_trophies=Max('total_trophies'),
        max_platinums=Max('total_platinums'),
        max_ultra_rares=Max('total_ultra_rares'),
        max_pp_score=Max('pp_score'),
    )


def detect_new_records(day: CommunityTrophyDay, prior_records: dict) -> dict:
    """Compare a row's stats against prior maxima.

    Returns a dict of booleans keyed by stat name. A stat is a new record if
    its value strictly exceeds the prior max, OR if there is no prior max
    (day 1, all stats default to NEW RECORD per project decision).
    """
    def beats(value: int, prior: int | None) -> bool:
        if prior is None:
            return True
        return value > prior

    return {
        'trophies': beats(day.total_trophies, prior_records.get('max_trophies')),
        'platinums': beats(day.total_platinums, prior_records.get('max_platinums')),
        'ultra_rares': beats(day.total_ultra_rares, prior_records.get('max_ultra_rares')),
        'pp_score': beats(day.pp_score, prior_records.get('max_pp_score')),
    }


def build_embed_payload(day: CommunityTrophyDay, prior_records: dict | None = None) -> dict:
    """Build the Discord webhook payload (embed JSON) for a daily post.

    `prior_records` is the output of get_current_records() with the day row
    excluded. If None, no NEW RECORD badges are shown. Pass an empty dict to
    explicitly compare against an empty history (day 1: all stats flagged).
    """
    if prior_records is None:
        records = {'trophies': False, 'platinums': False, 'ultra_rares': False, 'pp_score': False}
    else:
        records = detect_new_records(day, prior_records)

    record_badge = " 🆕 **NEW RECORD**"

    platinum_emoji = (
        f"<:Platinum_Trophy:{settings.PLATINUM_EMOJI_ID}>"
        if getattr(settings, 'PLATINUM_EMOJI_ID', None) else "🏆"
    )
    plat_pursuit_emoji = (
        f"<:PlatPursuit:{settings.PLAT_PURSUIT_EMOJI_ID}>"
        if getattr(settings, 'PLAT_PURSUIT_EMOJI_ID', None) else "🏆"
    )

    pretty_date = f"{day.date.strftime('%A, %B')} {day.date.day}, {day.date.year}"

    description_lines = [
        f"{plat_pursuit_emoji} **Yesterday, the Pursuit community earned:**",
        "",
        f"🏆 **Trophies:** `{day.total_trophies:,}`{record_badge if records['trophies'] else ''}",
        f"{platinum_emoji} **Platinums:** `{day.total_platinums:,}`{record_badge if records['platinums'] else ''}",
        f"🌟 **Ultra Rares:** `{day.total_ultra_rares:,}`{record_badge if records['ultra_rares'] else ''}",
        "",
        f"📊 **PP Score:** `{day.pp_score:,}`{record_badge if records['pp_score'] else ''}",
        "",
        "-# *Counts include only Discord-linked Pursuers and exclude shovelware.*",
    ]

    color = EMBED_COLOR_RECORD if any(records.values()) else EMBED_COLOR_DEFAULT

    embed_data = {
        'title': f"🏆 Daily Trophy Tracker: {pretty_date}",
        'description': "\n".join(description_lines),
        'color': color,
        'footer': {
            'text': (
                f"PP Score = trophies + ({PP_SCORE_PLATINUM_WEIGHT} × plats) + "
                f"({PP_SCORE_ULTRA_RARE_WEIGHT} × URs) | Powered by Plat Pursuit"
            ),
        },
    }

    return {'embeds': [embed_data]}


def build_today_payload(target_date: date_cls | None = None) -> dict:
    """Build the JSON response for /api/community-stats/today/.

    Computes live stats for `target_date` (defaults to today in ET) and
    annotates with the freshness note + a UTC computed_at timestamp.
    Caller is responsible for caching.
    """
    if target_date is None:
        target_date = timezone.now().astimezone(ET).date()

    stats = compute_day_stats(target_date)

    return {
        'date': target_date.isoformat(),
        'total_trophies': stats['total_trophies'],
        'total_platinums': stats['total_platinums'],
        'total_ultra_rares': stats['total_ultra_rares'],
        'pp_score': stats['pp_score'],
        'data_freshness_note': DATA_FRESHNESS_NOTE,
        'computed_at': timezone.now().isoformat(),
    }
