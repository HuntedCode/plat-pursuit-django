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
    lb:progress:{slug}:scores - Per-series progress sorted set
    lb:progress:{slug}:data   - Per-series progress display data
    lb:progress:global:scores - Global progress sorted set
    lb:progress:global:data   - Global progress display data
    lb:meta:last_rebuild      - Rebuild timestamps per leaderboard
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


def _progress_scores_key(slug=None):
    if slug:
        return f'lb:progress:{slug}:scores'
    return 'lb:progress:global:scores'


def _community_xp_key(slug):
    return f'lb:community_xp:{slug}'


def _progress_data_key(slug=None):
    if slug:
        return f'lb:progress:{slug}:data'
    return 'lb:progress:global:data'


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

def compute_progress_score(plats, golds, silvers, bronzes):
    """
    Composite score: plats desc > golds desc > silvers desc > bronzes desc.

    Date tiebreaker is stored in display data only (not in score).
    """
    return plats * 10**9 + golds * 10**6 + silvers * 10**3 + bronzes


def _build_progress_display_data(profile, plats, golds, silvers, bronzes, last_earned_date):
    """Build display data dict for a progress leaderboard entry."""
    return {
        'psn_username': profile.display_psn_username,
        'avatar_url': profile.avatar_url or '',
        'flag': profile.flag or '',
        'is_premium': profile.user_is_premium,
        'trophy_totals': {
            'plats': plats,
            'golds': golds,
            'silvers': silvers,
            'bronzes': bronzes,
        },
        'last_earned_date': last_earned_date.isoformat() if last_earned_date else 'Unknown',
    }


def update_progress_entry(slug, profile, plats, golds, silvers, bronzes, last_earned_date, pipeline=None):
    """
    Update a profile's progress leaderboard position.

    Args:
        slug: Series slug, or None for global progress leaderboard.
    """
    score = compute_progress_score(plats, golds, silvers, bronzes)
    if score <= 0:
        _remove_entry(_progress_scores_key(slug), _progress_data_key(slug), profile.id, pipeline=pipeline)
        return

    display_data = _build_progress_display_data(profile, plats, golds, silvers, bronzes, last_earned_date)
    _update_entry(
        _progress_scores_key(slug),
        _progress_data_key(slug),
        profile.id, score, display_data, pipeline=pipeline
    )


def get_progress_page(slug, page, page_size=50):
    """Get a page of progress leaderboard entries. slug=None for global."""
    return _get_page(_progress_scores_key(slug), _progress_data_key(slug), page, page_size)


def get_progress_rank(slug, profile_id):
    """Get a profile's progress leaderboard rank. slug=None for global."""
    return _get_rank(_progress_scores_key(slug), profile_id)


def get_progress_count(slug):
    """Get total progress leaderboard participants. slug=None for global."""
    return _get_count(_progress_scores_key(slug))


def compute_profile_progress_for_series(profile, series_slug):
    """
    Compute a single profile's trophy counts for a specific badge series.

    Used for incremental updates at sync-complete time. Scoped to one profile
    so it uses FK indexes and is fast.

    Returns:
        tuple: (plats, golds, silvers, bronzes, last_earned_date) or None if no trophies
    """
    from trophies.models import EarnedTrophy, Game

    games = Game.objects.filter(
        concept__stages__series_slug=series_slug
    ).distinct()

    trophies = EarnedTrophy.objects.filter(
        profile=profile,
        trophy__game__in=games,
        earned=True
    )

    if not trophies.exists():
        return None

    from django.db.models import Count, Q, Max

    counts = trophies.aggregate(
        plats=Count('id', filter=Q(trophy__trophy_type='platinum')),
        golds=Count('id', filter=Q(trophy__trophy_type='gold')),
        silvers=Count('id', filter=Q(trophy__trophy_type='silver')),
        bronzes=Count('id', filter=Q(trophy__trophy_type='bronze')),
        last_earned=Max('earned_date_time'),
    )

    return (
        counts['plats'],
        counts['golds'],
        counts['silvers'],
        counts['bronzes'],
        counts['last_earned'],
    )


def compute_profile_progress_global(profile):
    """
    Compute a single profile's trophy counts across all badge-related games.

    Returns:
        tuple: (plats, golds, silvers, bronzes, last_earned_date) or None if no trophies
    """
    from trophies.models import EarnedTrophy, Game

    games = Game.objects.filter(
        concept__stages__isnull=False
    ).distinct()

    trophies = EarnedTrophy.objects.filter(
        profile=profile,
        trophy__game__in=games,
        earned=True
    )

    if not trophies.exists():
        return None

    from django.db.models import Count, Q, Max

    counts = trophies.aggregate(
        plats=Count('id', filter=Q(trophy__trophy_type='platinum')),
        golds=Count('id', filter=Q(trophy__trophy_type='gold')),
        silvers=Count('id', filter=Q(trophy__trophy_type='silver')),
        bronzes=Count('id', filter=Q(trophy__trophy_type='bronze')),
        last_earned=Max('earned_date_time'),
    )

    return (
        counts['plats'],
        counts['golds'],
        counts['silvers'],
        counts['bronzes'],
        counts['last_earned'],
    )


def update_progress_leaderboards_for_profile(profile):
    """
    Recompute and update progress leaderboard entries for a profile across all
    series they participate in, plus the global leaderboard.

    Called at sync-complete time after bulk_gamification_update() exits.
    """
    from trophies.models import Stage

    # Find all series this profile might have progress in
    series_slugs = list(
        Stage.objects.filter(
            concepts__game__played_by_profiles=profile
        ).values_list('series_slug', flat=True).distinct()
    )

    pipe = redis_client.pipeline()

    for slug in series_slugs:
        result = compute_profile_progress_for_series(profile, slug)
        if result:
            plats, golds, silvers, bronzes, last_earned = result
            update_progress_entry(slug, profile, plats, golds, silvers, bronzes, last_earned, pipeline=pipe)
        else:
            _remove_entry(_progress_scores_key(slug), _progress_data_key(slug), profile.id, pipeline=pipe)

    # Global progress
    global_result = compute_profile_progress_global(profile)
    if global_result:
        plats, golds, silvers, bronzes, last_earned = global_result
        update_progress_entry(None, profile, plats, golds, silvers, bronzes, last_earned, pipeline=pipe)
    else:
        _remove_entry(_progress_scores_key(None), _progress_data_key(None), profile.id, pipeline=pipe)

    pipe.execute()
    logger.debug(f"Updated progress leaderboards for {profile.display_psn_username} across {len(series_slugs)} series")


def rebuild_progress_leaderboard(series_slug):
    """Full rebuild of progress leaderboard for a series."""
    from trophies.models import Game, UserBadgeProgress, Profile, EarnedTrophy
    from django.db.models import Count, Q, Max, OuterRef, Exists

    games = Game.objects.filter(
        concept__stages__series_slug=series_slug
    ).distinct()

    badge_sub = UserBadgeProgress.objects.filter(
        profile=OuterRef('pk'), badge__series_slug=series_slug
    )

    trophy_sub = EarnedTrophy.objects.filter(
        profile=OuterRef('pk'), trophy__game__in=games, earned=True
    )

    game_filter = Q(
        earned_trophy_entries__earned=True,
        earned_trophy_entries__trophy__game__in=games,
    )

    profiles = Profile.objects.filter(
        Q(is_linked=True) & (Exists(badge_sub) | Exists(trophy_sub))
    ).annotate(
        plats=Count('earned_trophy_entries__id',
                     filter=game_filter & Q(earned_trophy_entries__trophy__trophy_type='platinum')),
        golds=Count('earned_trophy_entries__id',
                     filter=game_filter & Q(earned_trophy_entries__trophy__trophy_type='gold')),
        silvers=Count('earned_trophy_entries__id',
                      filter=game_filter & Q(earned_trophy_entries__trophy__trophy_type='silver')),
        bronzes=Count('earned_trophy_entries__id',
                      filter=game_filter & Q(earned_trophy_entries__trophy__trophy_type='bronze')),
        max_earn_date=Max('earned_trophy_entries__earned_date_time', filter=game_filter)
    ).only('id', 'display_psn_username', 'flag', 'avatar_url', 'user_is_premium')

    entries = []
    for p in profiles:
        score = compute_progress_score(p.plats, p.golds, p.silvers, p.bronzes)
        if score <= 0:
            continue
        display_data = _build_progress_display_data(
            p, p.plats, p.golds, p.silvers, p.bronzes, p.max_earn_date
        )
        entries.append((p.id, score, display_data))

    _rebuild_leaderboard(
        _progress_scores_key(series_slug),
        _progress_data_key(series_slug),
        entries
    )
    logger.info(f"Rebuilt progress leaderboard for {series_slug} with {len(entries)} entries")
    return len(entries)


def rebuild_global_progress_leaderboard():
    """Full rebuild of global progress leaderboard."""
    from trophies.models import Profile, EarnedTrophy, Game
    from django.db.models import Count, Q, Max, OuterRef, Exists

    games = Game.objects.filter(concept__stages__isnull=False).distinct()

    trophy_sub = EarnedTrophy.objects.filter(
        profile=OuterRef('pk'), trophy__game__in=games, earned=True
    )

    game_filter = Q(
        earned_trophy_entries__earned=True,
        earned_trophy_entries__trophy__game__in=games,
    )

    profiles = Profile.objects.filter(
        Q(is_linked=True) & Exists(trophy_sub)
    ).annotate(
        plats=Count('earned_trophy_entries__id',
                     filter=game_filter & Q(earned_trophy_entries__trophy__trophy_type='platinum')),
        golds=Count('earned_trophy_entries__id',
                     filter=game_filter & Q(earned_trophy_entries__trophy__trophy_type='gold')),
        silvers=Count('earned_trophy_entries__id',
                      filter=game_filter & Q(earned_trophy_entries__trophy__trophy_type='silver')),
        bronzes=Count('earned_trophy_entries__id',
                      filter=game_filter & Q(earned_trophy_entries__trophy__trophy_type='bronze')),
        max_earn_date=Max('earned_trophy_entries__earned_date_time', filter=game_filter)
    ).only('id', 'display_psn_username', 'flag', 'avatar_url', 'user_is_premium')

    entries = []
    for p in profiles:
        score = compute_progress_score(p.plats, p.golds, p.silvers, p.bronzes)
        if score <= 0:
            continue
        display_data = _build_progress_display_data(
            p, p.plats, p.golds, p.silvers, p.bronzes, p.max_earn_date
        )
        entries.append((p.id, score, display_data))

    _rebuild_leaderboard(
        _progress_scores_key(None),
        _progress_data_key(None),
        entries
    )
    logger.info(f"Rebuilt global progress leaderboard with {len(entries)} entries")
    return len(entries)


# ---------------------------------------------------------------------------
# Community XP
# ---------------------------------------------------------------------------

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
# Aggregate rebuild helpers
# ---------------------------------------------------------------------------

def rebuild_series_leaderboards(series_slug):
    """Rebuild all leaderboards for a specific badge series (earners + progress + community XP)."""
    earners_count = rebuild_earners_leaderboard(series_slug)
    progress_count = rebuild_progress_leaderboard(series_slug)
    community_xp = rebuild_community_xp(series_slug)
    return earners_count, progress_count


def rebuild_all_leaderboards():
    """Full rebuild of all leaderboards. Used by management command for reconciliation."""
    from trophies.models import Badge

    xp_count = rebuild_xp_leaderboard()
    global_progress_count = rebuild_global_progress_leaderboard()

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
        f"{len(unique_slugs)} series processed"
    )

    return {
        'xp': xp_count,
        'global_progress': global_progress_count,
        'series': series_results,
    }
