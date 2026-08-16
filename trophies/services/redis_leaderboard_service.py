"""
Redis Sorted Set Leaderboard Service.

Provides O(log n) rank lookups, O(1) pagination, and incremental updates
via Redis sorted sets, replacing the old batch-recompute-and-cache approach.

Architecture:
- Each leaderboard has two Redis keys: a sorted set (scores) and a hash (display data)
- Sorted set members are profile IDs (as strings), scores are composite values
- Display data hashes map profile_id -> JSON blob with rendering info
- Updates are incremental via Django signals, with periodic full rebuilds for reconciliation

Key patterns (raw Redis, DB 0):
    lb:xp:scores            - XP leaderboard sorted set
    lb:xp:data              - XP display data hash
    lb:earners:{slug}:scores - Per-series earners sorted set
    lb:earners:{slug}:data   - Per-series earners display data
    lb:meta:last_rebuild      - Rebuild timestamps per leaderboard

RETIRED (leaderboards rebuild, step 2): the PROGRESS boards -- global and per-series -- are gone from
here entirely. They are served by services/badge_leaderboards over indexed standing columns, and their
rebuild was the single most expensive thing in this module: four filtered COUNTs plus a MAX over
EarnedTrophy for EVERY linked profile, every 6 hours.

What remains is still load-bearing and CANNOT be deleted until the badge cutover:
  - earners  -> frame_service reads it for the legacy badge frame (Badge/UserBadge)
  - xp       -> profile_card_service + the dashboard providers rank the LEGACY
                ProfileGamification.total_badge_xp; ranking that against the new ProfileBadgeStanding
                would print a figure next to a rank computed from a different number
  - country  -> same consumers, same reason
Retire those with the badge cutover, which repoints the XP source. See
docs/design/rebuild/leaderboards-rebuild.md.
"""
import json
import logging
import math

from django.utils import timezone

from trophies.util_modules.cache import redis_client

logger = logging.getLogger(__name__)

# Max timestamp for inverting dates (year ~33658, well beyond any real date)
MAX_TIMESTAMP = 10**12


# ---------------------------------------------------------------------------
# Template-compatible paginator shims
# ---------------------------------------------------------------------------

class RedisPaginator:
    """Lightweight paginator compatible with Django templates, backed by Redis ZCARD."""

    def __init__(self, total_count, per_page):
        self.count = total_count
        self.per_page = per_page
        self.num_pages = max(1, math.ceil(total_count / per_page))


class RedisPage:
    """Lightweight page object compatible with Django templates."""

    def __init__(self, object_list, number, paginator):
        self.object_list = object_list
        self.number = number
        self.paginator = paginator

    def __iter__(self):
        return iter(self.object_list)

    def __len__(self):
        return len(self.object_list)

    @property
    def has_previous(self):
        return self.number > 1

    @property
    def has_next(self):
        return self.number < self.paginator.num_pages

    @property
    def previous_page_number(self):
        return self.number - 1

    @property
    def next_page_number(self):
        return self.number + 1


# ---------------------------------------------------------------------------
# Key helpers
# ---------------------------------------------------------------------------

def _xp_scores_key():
    return 'lb:xp:scores'


def _xp_data_key():
    return 'lb:xp:data'


def _earners_scores_key(slug):
    return f'lb:earners:{slug}:scores'


def _earners_data_key(slug):
    return f'lb:earners:{slug}:data'


def _country_xp_scores_key(country_code):
    return f'lb:xp:country:{country_code}:scores'


def _country_xp_data_key(country_code):
    return f'lb:xp:country:{country_code}:data'


def _country_xp_index_key():
    return 'lb:xp:country:index'


def _community_xp_key(slug):
    return f'lb:community_xp:{slug}'


def _member(profile_id):
    """Convert profile ID to sorted set member string."""
    return str(profile_id)


# ---------------------------------------------------------------------------
# Generic read helpers
# ---------------------------------------------------------------------------

def _get_page(scores_key, data_key, page, page_size=50):
    """
    Fetch a page of leaderboard entries from a sorted set.

    Returns list of dicts with display data + computed rank.
    """
    page = max(1, page)
    start = (page - 1) * page_size
    end = start + page_size - 1

    # ZREVRANGE returns members in descending score order
    members = redis_client.zrevrange(scores_key, start, end)
    if not members:
        return []

    # Batch-fetch display data
    raw_data = redis_client.hmget(data_key, *members)

    entries = []
    for i, (member, raw) in enumerate(zip(members, raw_data)):
        if raw is None:
            continue
        try:
            entry = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        entry['rank'] = start + i + 1
        entries.append(entry)

    return entries


def _get_rank(scores_key, profile_id):
    """
    Get 1-indexed rank for a profile. Returns None if not on leaderboard.
    """
    rank = redis_client.zrevrank(scores_key, _member(profile_id))
    if rank is None:
        return None
    return rank + 1  # Convert 0-indexed to 1-indexed


def _get_count(scores_key):
    """Get total number of entries in a leaderboard."""
    return redis_client.zcard(scores_key)


def _get_neighborhood(scores_key, data_key, profile_id, above=2, below=2):
    """
    Get entries around a profile's rank for dashboard-style display.

    Returns list of dicts with display data + rank.
    """
    rank_0 = redis_client.zrevrank(scores_key, _member(profile_id))
    if rank_0 is None:
        return []

    start = max(0, rank_0 - above)
    end = rank_0 + below

    members = redis_client.zrevrange(scores_key, start, end)
    if not members:
        return []

    raw_data = redis_client.hmget(data_key, *members)

    entries = []
    for i, (member, raw) in enumerate(zip(members, raw_data)):
        if raw is None:
            continue
        try:
            entry = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        entry['rank'] = start + i + 1
        entries.append(entry)

    return entries


def _update_entry(scores_key, data_key, profile_id, score, display_data, pipeline=None):
    """
    Update a single leaderboard entry (ZADD + HSET).

    Args:
        pipeline: Optional Redis pipeline for batched writes.
    """
    member = _member(profile_id)
    data_json = json.dumps(display_data)
    pipe = pipeline or redis_client.pipeline()
    pipe.zadd(scores_key, {member: score})
    pipe.hset(data_key, member, data_json)
    if pipeline is None:
        pipe.execute()


def _remove_entry(scores_key, data_key, profile_id, pipeline=None):
    """Remove a single leaderboard entry (ZREM + HDEL)."""
    member = _member(profile_id)
    pipe = pipeline or redis_client.pipeline()
    pipe.zrem(scores_key, member)
    pipe.hdel(data_key, member)
    if pipeline is None:
        pipe.execute()


def _rebuild_leaderboard(scores_key, data_key, entries, pipeline=None):
    """
    Full rebuild of a leaderboard from a list of (profile_id, score, display_data) tuples.

    Atomically replaces the sorted set and hash contents.
    """
    pipe = pipeline or redis_client.pipeline()

    # Clear existing data
    pipe.delete(scores_key)
    pipe.delete(data_key)

    # Batch insert
    if entries:
        score_mapping = {}
        for profile_id, score, display_data in entries:
            member = _member(profile_id)
            score_mapping[member] = score
            pipe.hset(data_key, member, json.dumps(display_data))

        if score_mapping:
            pipe.zadd(scores_key, score_mapping)

    # Record rebuild time
    pipe.hset('lb:meta:last_rebuild', scores_key, timezone.now().isoformat())

    if pipeline is None:
        pipe.execute()


# ---------------------------------------------------------------------------
# XP Leaderboard
# ---------------------------------------------------------------------------

def compute_xp_score(total_xp, total_badges):
    """Composite score: XP desc, then badges desc as tiebreaker."""
    return total_xp * 10**4 + total_badges


def _build_xp_display_data(profile, total_xp, total_badges):
    """Build the display data dict for an XP leaderboard entry."""
    return {
        'psn_username': profile.display_psn_username,
        'avatar_url': profile.avatar_url or '',
        'flag': profile.flag or '',
        'is_premium': profile.user_is_premium,
        'displayed_title': profile.displayed_title() or '',
        'total_xp': total_xp,
        'total_badges': total_badges,
    }


def update_xp_entry(profile, total_xp, total_badges, pipeline=None):
    """Update a profile's XP leaderboard position."""
    if total_xp <= 0:
        # Remove from leaderboard if no XP
        _remove_entry(_xp_scores_key(), _xp_data_key(), profile.id, pipeline=pipeline)
        return

    score = compute_xp_score(total_xp, total_badges)
    display_data = _build_xp_display_data(profile, total_xp, total_badges)
    _update_entry(_xp_scores_key(), _xp_data_key(), profile.id, score, display_data, pipeline=pipeline)


def remove_xp_entry(profile_id, pipeline=None):
    """Remove a profile from the XP leaderboard."""
    _remove_entry(_xp_scores_key(), _xp_data_key(), profile_id, pipeline=pipeline)


def get_xp_page(page, page_size=50):
    """Get a page of XP leaderboard entries."""
    return _get_page(_xp_scores_key(), _xp_data_key(), page, page_size)


def get_xp_rank(profile_id):
    """Get a profile's XP leaderboard rank (1-indexed), or None."""
    return _get_rank(_xp_scores_key(), profile_id)


def get_xp_count():
    """Get total number of profiles on the XP leaderboard."""
    return _get_count(_xp_scores_key())


def get_xp_neighborhood(profile_id, above=2, below=2):
    """Get entries around a profile's rank on the XP leaderboard."""
    return _get_neighborhood(_xp_scores_key(), _xp_data_key(), profile_id, above, below)


def get_xp_top(n=5):
    """Get top N entries from the XP leaderboard."""
    return _get_page(_xp_scores_key(), _xp_data_key(), page=1, page_size=n)


def rebuild_xp_leaderboard():
    """Full rebuild of XP leaderboard from ProfileGamification."""
    from trophies.models import ProfileGamification

    queryset = ProfileGamification.objects.filter(
        total_badge_xp__gt=0,
        profile__is_linked=True
    ).select_related('profile')

    entries = []
    for gamification in queryset.iterator(chunk_size=500):
        profile = gamification.profile
        total_xp = gamification.total_badge_xp
        total_badges = gamification.total_badges_earned
        score = compute_xp_score(total_xp, total_badges)
        display_data = _build_xp_display_data(profile, total_xp, total_badges)
        entries.append((profile.id, score, display_data))

    _rebuild_leaderboard(_xp_scores_key(), _xp_data_key(), entries)
    logger.info(f"Rebuilt XP leaderboard with {len(entries)} entries")
    return len(entries)


# ---------------------------------------------------------------------------
# Earners Leaderboard
# ---------------------------------------------------------------------------

def compute_earner_score(tier, earned_at):
    """
    Composite score: tier desc, then date asc (earlier = higher score within same tier).

    Inverting the timestamp makes earlier dates produce higher scores.
    """
    ts = int(earned_at.timestamp()) if earned_at else 0
    return tier * 10**12 + (MAX_TIMESTAMP - ts)


def _build_earner_display_data(profile, tier, earned_at):
    """Build display data dict for an earners leaderboard entry."""
    return {
        'psn_username': profile.display_psn_username,
        'avatar_url': profile.avatar_url or '',
        'flag': profile.flag or '',
        'is_premium': profile.user_is_premium,
        'displayed_title': profile.displayed_title() or '',
        'highest_tier': tier,
        'earn_date': earned_at.isoformat() if earned_at else 'Unknown',
    }


def update_earner_entry(series_slug, profile, tier, earned_at, pipeline=None):
    """Update a profile's earners leaderboard position for a badge series."""
    score = compute_earner_score(tier, earned_at)
    display_data = _build_earner_display_data(profile, tier, earned_at)
    _update_entry(
        _earners_scores_key(series_slug),
        _earners_data_key(series_slug),
        profile.id, score, display_data, pipeline=pipeline
    )


def remove_earner_entry(series_slug, profile_id, pipeline=None):
    """Remove a profile from a series earners leaderboard."""
    _remove_entry(
        _earners_scores_key(series_slug),
        _earners_data_key(series_slug),
        profile_id, pipeline=pipeline
    )


def get_earners_page(series_slug, page, page_size=50):
    """Get a page of earners leaderboard entries for a series."""
    return _get_page(_earners_scores_key(series_slug), _earners_data_key(series_slug), page, page_size)


def get_earners_rank(series_slug, profile_id):
    """Get a profile's earners leaderboard rank for a series (1-indexed), or None."""
    return _get_rank(_earners_scores_key(series_slug), profile_id)


def get_earners_ranks(series_slugs, profile_id):
    """Batch version of get_earners_rank: {series_slug: rank|None} for many series in ONE
    Redis pipeline round-trip (one ZREVRANK per series, same member). For surfaces that
    render many badges at once (the collection album) so rank lookups don't fan out to N
    round-trips."""
    slugs = list(dict.fromkeys(series_slugs))  # de-dupe, preserve order
    if not slugs:
        return {}
    member = _member(profile_id)
    pipe = redis_client.pipeline()
    for slug in slugs:
        pipe.zrevrank(_earners_scores_key(slug), member)
    results = pipe.execute()
    return {
        slug: (rank + 1 if rank is not None else None)
        for slug, rank in zip(slugs, results)
    }


def get_earners_count(series_slug):
    """Get total earners count for a series."""
    return _get_count(_earners_scores_key(series_slug))


def rebuild_earners_leaderboard(series_slug):
    """Full rebuild of earners leaderboard for a series from UserBadge records."""
    from django.db.models import Window, F
    from django.db.models.functions import RowNumber
    from trophies.models import UserBadge

    earners = UserBadge.objects.filter(
        badge__series_slug=series_slug,
        profile__is_linked=True
    ).select_related('profile', 'badge').annotate(
        row_number=Window(
            RowNumber(),
            partition_by=F('profile'),
            order_by=[F('badge__tier').desc(), F('earned_at').asc()]
        )
    ).filter(row_number=1)

    entries = []
    for earner in earners:
        profile = earner.profile
        tier = earner.badge.tier
        earned_at = earner.earned_at
        score = compute_earner_score(tier, earned_at)
        display_data = _build_earner_display_data(profile, tier, earned_at)
        entries.append((profile.id, score, display_data))

    _rebuild_leaderboard(
        _earners_scores_key(series_slug),
        _earners_data_key(series_slug),
        entries
    )
    logger.info(f"Rebuilt earners leaderboard for {series_slug} with {len(entries)} entries")
    return len(entries)


# ---------------------------------------------------------------------------
# Progress Leaderboard
# ---------------------------------------------------------------------------

def update_earner_leaderboards_for_profile(profile):
    """
    Update earner leaderboard entries for a profile across all series
    they have earned badges in.

    Called at sync-complete time after bulk_gamification_update() exits,
    since individual signal handlers skip earner updates during bulk mode.
    """
    from trophies.models import UserBadge

    if not profile.is_linked:
        return

    # Get the highest tier badge per series (ordered by tier desc, earned_at asc)
    user_badges = UserBadge.objects.filter(
        profile=profile
    ).select_related('badge').order_by('badge__series_slug', '-badge__tier', 'earned_at')

    # Group by series_slug, keeping only the first (highest tier, earliest earned) per series
    best_per_series = {}
    for ub in user_badges:
        slug = ub.badge.series_slug
        if slug and slug not in best_per_series:
            best_per_series[slug] = ub

    if not best_per_series:
        return

    pipe = redis_client.pipeline()
    for slug, ub in best_per_series.items():
        update_earner_entry(slug, profile, ub.badge.tier, ub.earned_at, pipeline=pipe)
    pipe.execute()

    logger.debug(f"Updated earner leaderboards for {profile.display_psn_username} across {len(best_per_series)} series")


def update_community_xp_deltas(deltas, pipeline=None):
    """
    Apply per-series XP deltas to community XP totals via INCRBY.

    Args:
        deltas: dict mapping series_slug to XP delta (positive or negative int)
        pipeline: Optional Redis pipeline for batched writes.
    """
    pipe = pipeline or redis_client.pipeline()
    for slug, delta in deltas.items():
        if delta != 0:
            pipe.incrby(_community_xp_key(slug), delta)
    if pipeline is None:
        pipe.execute()


def get_community_xp(series_slug):
    """Get total community XP for a series from raw Redis. Returns 0 if not set."""
    val = redis_client.get(_community_xp_key(series_slug))
    if val is None:
        return 0
    try:
        return int(val)
    except (ValueError, TypeError):
        return 0


def rebuild_community_xp(series_slug):
    """Full recompute of community XP for a series from ProfileGamification."""
    from trophies.services.leaderboard_service import compute_community_series_xp
    total = compute_community_series_xp(series_slug)
    redis_client.set(_community_xp_key(series_slug), total)
    logger.info(f"Rebuilt community XP for {series_slug}: {total:,}")
    return total


# ---------------------------------------------------------------------------
# Country XP Leaderboard
# ---------------------------------------------------------------------------

def update_country_xp_entry(country_code, profile, total_xp, total_badges, pipeline=None):
    """Update a profile's country XP leaderboard position."""
    if total_xp <= 0:
        remove_country_xp_entry(country_code, profile.id, pipeline=pipeline)
        return

    score = compute_xp_score(total_xp, total_badges)
    display_data = _build_xp_display_data(profile, total_xp, total_badges)
    pipe = pipeline or redis_client.pipeline()
    _update_entry(
        _country_xp_scores_key(country_code),
        _country_xp_data_key(country_code),
        profile.id, score, display_data, pipeline=pipe
    )
    pipe.sadd(_country_xp_index_key(), country_code)
    if pipeline is None:
        pipe.execute()


def remove_country_xp_entry(country_code, profile_id, pipeline=None):
    """Remove a profile from a country XP leaderboard."""
    _remove_entry(
        _country_xp_scores_key(country_code),
        _country_xp_data_key(country_code),
        profile_id, pipeline=pipeline
    )


def get_country_xp_page(country_code, page, page_size=50):
    """Get a page of country XP leaderboard entries."""
    return _get_page(
        _country_xp_scores_key(country_code),
        _country_xp_data_key(country_code),
        page, page_size
    )


def get_country_xp_rank(country_code, profile_id):
    """Get a profile's country XP leaderboard rank (1-indexed), or None."""
    return _get_rank(_country_xp_scores_key(country_code), profile_id)


def get_country_xp_count(country_code):
    """Get total number of profiles on a country XP leaderboard."""
    return _get_count(_country_xp_scores_key(country_code))


def get_country_xp_neighborhood(country_code, profile_id, above=2, below=2):
    """Get entries around a profile's rank on a country XP leaderboard."""
    return _get_neighborhood(
        _country_xp_scores_key(country_code),
        _country_xp_data_key(country_code),
        profile_id, above, below
    )


def get_country_xp_top(country_code, n=5):
    """Get top N entries from a country XP leaderboard."""
    return _get_page(
        _country_xp_scores_key(country_code),
        _country_xp_data_key(country_code),
        page=1, page_size=n
    )


def get_active_country_codes():
    """Get all country codes that have active XP leaderboards."""
    codes = redis_client.smembers(_country_xp_index_key())
    return {c.decode() if isinstance(c, bytes) else c for c in codes}


def rebuild_country_xp_leaderboard(country_code):
    """Full rebuild of country XP leaderboard for a single country."""
    from trophies.models import ProfileGamification

    queryset = ProfileGamification.objects.filter(
        total_badge_xp__gt=0,
        profile__is_linked=True,
        profile__country_code=country_code,
    ).select_related('profile')

    entries = []
    for gamification in queryset.iterator(chunk_size=500):
        profile = gamification.profile
        total_xp = gamification.total_badge_xp
        total_badges = gamification.total_badges_earned
        score = compute_xp_score(total_xp, total_badges)
        display_data = _build_xp_display_data(profile, total_xp, total_badges)
        entries.append((profile.id, score, display_data))

    _rebuild_leaderboard(
        _country_xp_scores_key(country_code),
        _country_xp_data_key(country_code),
        entries
    )
    logger.info(f"Rebuilt country XP leaderboard for {country_code} with {len(entries)} entries")
    return len(entries)


def rebuild_country_xp_leaderboards():
    """
    Full rebuild of all country XP leaderboards from ProfileGamification.

    Groups profiles by country_code and rebuilds each country's sorted set.
    Also rebuilds the country index SET.

    Returns:
        dict: {country_code: entry_count}
    """
    from collections import defaultdict
    from trophies.models import ProfileGamification

    queryset = ProfileGamification.objects.filter(
        total_badge_xp__gt=0,
        profile__is_linked=True,
        profile__country_code__isnull=False,
    ).exclude(
        profile__country_code=''
    ).select_related('profile')

    # Group entries by country
    country_entries = defaultdict(list)
    for gamification in queryset.iterator(chunk_size=500):
        profile = gamification.profile
        cc = profile.country_code
        total_xp = gamification.total_badge_xp
        total_badges = gamification.total_badges_earned
        score = compute_xp_score(total_xp, total_badges)
        display_data = _build_xp_display_data(profile, total_xp, total_badges)
        country_entries[cc].append((profile.id, score, display_data))

    results = {}
    pipe = redis_client.pipeline()

    # Rebuild index: clear and repopulate
    pipe.delete(_country_xp_index_key())
    for cc in country_entries:
        pipe.sadd(_country_xp_index_key(), cc)
    pipe.execute()

    # Rebuild each country's leaderboard
    for cc, entries in country_entries.items():
        _rebuild_leaderboard(
            _country_xp_scores_key(cc),
            _country_xp_data_key(cc),
            entries
        )
        results[cc] = len(entries)

    logger.info(
        f"Rebuilt country XP leaderboards: {len(results)} countries, "
        f"{sum(results.values())} total entries"
    )
    return results


# ---------------------------------------------------------------------------
# Aggregate rebuild helpers
# ---------------------------------------------------------------------------

def rebuild_series_leaderboards(series_slug):
    """Rebuild a badge series' remaining sorted sets (earners + community XP).

    The per-series PROGRESS set is gone -- nothing reads it now that the board is served from
    SeriesBadgeStanding. The second return value is kept as a constant 0 so the callers' tuple unpacking
    and their log lines keep working through the transition; it goes with the rest of this module at the
    badge cutover.
    """
    earners_count = rebuild_earners_leaderboard(series_slug)
    community_xp = rebuild_community_xp(series_slug)
    return earners_count, 0


def rebuild_all_leaderboards():
    """Full rebuild of all leaderboards. Used by management command for reconciliation."""
    from trophies.models import Badge

    xp_count = rebuild_xp_leaderboard()
    country_results = rebuild_country_xp_leaderboards()

    unique_slugs = list(
        Badge.objects.filter(is_live=True)
        .values_list('series_slug', flat=True)
        .distinct()
        .order_by('series_slug')
    )

    series_results = {}
    for slug in unique_slugs:
        try:
            earners_count, progress_count = rebuild_series_leaderboards(slug)
            series_results[slug] = {'earners': earners_count, 'progress': progress_count}
        except Exception:
            logger.exception(f"Failed rebuilding leaderboards for series {slug}")
            series_results[slug] = {'error': True}

    logger.info(
        f"Full leaderboard rebuild complete: {xp_count} XP entries, "
        f"{global_progress_count} global progress entries, "
        f"{len(country_results)} countries, "
        f"{len(unique_slugs)} series processed"
    )

    return {
        'xp': xp_count,
        'country_xp': country_results,
        'series': series_results,
    }
