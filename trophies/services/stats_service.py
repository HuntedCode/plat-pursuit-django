"""
Stats page service - Computes all stats for the /my-stats/ page.

Organized by section, with one private helper per section. Premium stats are
computed together and cached as a single dict for 4 hours, invalidated on sync.
"""
import calendar
import logging
from collections import Counter, defaultdict
from datetime import timedelta

import pytz
from django.core.cache import cache
from django.db.models import Avg, Count, Max, Min, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

logger = logging.getLogger(__name__)

STATS_CACHE_TTL = 14400  # 4 hours


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_career_overview(profile):
    """Free section: denormalized Profile fields + 1 query for first trophy date."""
    from trophies.models import EarnedTrophy

    total = profile.total_trophies or 0
    unearned = profile.total_unearned or 0
    games = profile.total_games or 0
    plats = profile.total_plats or 0
    completes = profile.total_completes or 0
    total_possible = total + unearned
    now = timezone.now()

    # Career age based on first trophy earned (not PP account creation)
    first_trophy_date = (
        EarnedTrophy.objects.filter(
            profile=profile, earned=True, earned_date_time__isnull=False,
        )
        .order_by('earned_date_time')
        .values_list('earned_date_time', flat=True)
        .first()
    )
    career_start = first_trophy_date or profile.created_at
    career_days = (now - career_start).days if career_start else 0
    career_months = career_days / 30.44 if career_days > 0 else 0

    return {
        'total_trophies': total,
        'total_bronzes': profile.total_bronzes or 0,
        'total_silvers': profile.total_silvers or 0,
        'total_golds': profile.total_golds or 0,
        'total_plats': plats,
        'bronze_pct': round(profile.total_bronzes / total * 100, 1) if total else 0,
        'silver_pct': round(profile.total_silvers / total * 100, 1) if total else 0,
        'gold_pct': round(profile.total_golds / total * 100, 1) if total else 0,
        'plat_pct': round(plats / total * 100, 1) if total else 0,
        'total_unearned': unearned,
        'overall_earn_rate': round(total / total_possible * 100, 1) if total_possible else 0,
        'total_games': games,
        'total_completes': completes,
        'backlog': games - completes,
        'avg_progress': round(profile.avg_progress or 0, 1),
        'completion_rate': round(completes / games * 100, 1) if games else 0,
        'platinum_rate': round(plats / games * 100, 1) if games else 0,
        'trophies_per_game': round(total / games, 1) if games else 0,
        'trophies_per_day': round(total / career_days, 2) if career_days else 0,
        'plats_per_month': round(plats / career_months, 2) if career_months else 0,
        'career_days': career_days,
        'career_years': round(career_days / 365.25, 1) if career_days else 0,
        'trophy_level': profile.trophy_level or 0,
        'level_progress': profile.progress or 0,
        'tier': profile.tier or 0,
        'is_plus': profile.is_plus,
        'hidden_games': profile.total_hiddens or 0,
        'country': profile.country or '',
        'country_code': profile.country_code or '',
    }


def get_teaser_records(profile):
    """3-4 cheap personal records for the free user teaser."""
    from trophies.models import EarnedTrophy, ProfileGame

    records = []

    # First trophy ever
    first = (
        EarnedTrophy.objects.filter(
            profile=profile, earned=True, earned_date_time__isnull=False
        )
        .select_related('trophy__game__concept')
        .order_by('earned_date_time')
        .first()
    )
    if first:
        name, icon = _game_context(first.trophy.game)
        records.append({
            'label': 'First Trophy Ever',
            'value': first.trophy.trophy_name,
            'subtitle': name,
            'icon': icon,
            'date': first.earned_date_time,
        })

    # First platinum
    first_plat = (
        EarnedTrophy.objects.filter(
            profile=profile, earned=True, trophy__trophy_type='platinum',
            earned_date_time__isnull=False,
        )
        .select_related('trophy__game__concept')
        .order_by('earned_date_time')
        .first()
    )
    if first_plat:
        name, icon = _game_context(first_plat.trophy.game)
        records.append({
            'label': 'First Platinum',
            'value': name,
            'subtitle': None,
            'icon': icon,
            'date': first_plat.earned_date_time,
        })

    # Shortest playtime (platinum game)
    fastest = (
        ProfileGame.objects.filter(
            profile=profile, has_plat=True, play_duration__isnull=False,
        )
        .select_related('game__concept')
        .order_by('play_duration')
        .first()
    )
    if fastest and fastest.play_duration:
        name, icon = _game_context(fastest.game)
        records.append({
            'label': 'Shortest Playtime (Plat)',
            'value': _format_duration(fastest.play_duration),
            'subtitle': name,
            'icon': icon,
            'date': None,
        })

    # Most trophies in one game
    most_in_game = (
        ProfileGame.objects.filter(profile=profile, earned_trophies_count__gt=0)
        .select_related('game__concept')
        .order_by('-earned_trophies_count')
        .first()
    )
    if most_in_game:
        name, icon = _game_context(most_in_game.game)
        records.append({
            'label': 'Most Trophies in One Game',
            'value': str(most_in_game.earned_trophies_count),
            'subtitle': name,
            'icon': icon,
            'date': None,
        })

    return records


def get_premium_stats(profile, exclude_shovelware=False, exclude_hidden=False):
    """Full premium stats dict, cached for 4 hours per filter combo."""
    cache_key = f'stats_page:{profile.id}:{int(exclude_shovelware)}:{int(exclude_hidden)}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    data = _compute_all_premium_stats(profile, exclude_shovelware, exclude_hidden)
    try:
        cache.set(cache_key, data, STATS_CACHE_TTL)
    except Exception:
        logger.debug("Could not cache stats page data for profile %s", profile.id)
    return data


def invalidate_stats_cache(profile_id):
    """Clear all cached stats combos. Called after sync completion."""
    for sw in (0, 1):
        for hid in (0, 1):
            cache.delete(f'stats_page:{profile_id}:{sw}:{hid}')


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def _compute_all_premium_stats(profile, exclude_shovelware=False, exclude_hidden=False):
    """Compute every premium section together for efficient query batching."""
    from trophies.models import (
        EarnedTrophy, ProfileGame, ProfileGamification,
        IGDBMatch, ConceptCompany, ConceptGenre, ConceptTheme, ConceptEngine,
    )

    # Build game filter Q for queries that join through game
    game_filter_q = _build_game_filter_q(exclude_shovelware, exclude_hidden)

    # Shared fetch 1: all earned timestamps + trophy type (for streaks, time, records)
    # Not filtered by shovelware/hidden: a trophy is a trophy regardless of game status
    earned_timestamps = list(
        EarnedTrophy.objects.filter(
            profile=profile, earned=True, earned_date_time__isnull=False,
        ).values_list('earned_date_time', 'trophy__trophy_type')
    )

    # Shared fetch 2: profile games with game+concept (filtered by toggles)
    pg_qs = ProfileGame.objects.filter(profile=profile).select_related('game__concept')
    if game_filter_q:
        pg_qs = pg_qs.filter(game_filter_q)
    profile_games = list(pg_qs)

    # Shared fetch 3: IGDB data for concepts in the user's library
    concept_ids = {pg.game.concept_id for pg in profile_games if pg.game and pg.game.concept_id}
    igdb_lookup = {}
    if concept_ids:
        # Only trusted matches feed the stats page. Pending/rejected matches
        # carry populated TTB/category fields but haven't been reviewed.
        igdb_lookup = {
            m.concept_id: m for m in
            IGDBMatch.objects.filter(
                concept_id__in=concept_ids,
                status__in=('accepted', 'auto_accepted'),
            )
            .only('concept_id', 'game_category', 'franchise_names',
                  'time_to_beat_completely', 'igdb_first_release_date',
                  'raw_response')
        }

    # Shared fetch 4: developer + publisher data for concepts
    dev_lookup = defaultdict(list)
    pub_lookup = defaultdict(list)  # concept_id -> [company_name, ...]
    if concept_ids:
        for cc in (ConceptCompany.objects.filter(concept_id__in=concept_ids)
                   .filter(Q(is_developer=True) | Q(is_publisher=True))
                   .select_related('company')):
            if cc.is_developer:
                dev_lookup[cc.concept_id].append(cc.company)
            if cc.is_publisher:
                pub_lookup[cc.concept_id].append(cc.company.name)

    # Shared fetch 5: normalized genre/theme/engine lookups (M2M)
    genre_lookup = defaultdict(list)   # concept_id -> [genre_name, ...]
    theme_lookup = defaultdict(list)   # concept_id -> [theme_name, ...]
    engine_lookup = defaultdict(list)  # concept_id -> [engine_name, ...]
    if concept_ids:
        for cg in ConceptGenre.objects.filter(concept_id__in=concept_ids).select_related('genre'):
            genre_lookup[cg.concept_id].append(cg.genre.name)
        for ct in ConceptTheme.objects.filter(concept_id__in=concept_ids).select_related('theme'):
            theme_lookup[ct.concept_id].append(ct.theme.name)
        for ce in ConceptEngine.objects.filter(concept_id__in=concept_ids).select_related('engine'):
            engine_lookup[ce.concept_id].append(ce.engine.name)

    gamification = ProfileGamification.objects.filter(profile=profile).first()
    user_tz = _get_user_timezone(profile)

    return {
        'records': _compute_personal_records(profile, earned_timestamps, user_tz, exclude_shovelware, exclude_hidden, igdb_lookup),
        'rarity': _compute_rarity_profile(profile),
        'streaks': _compute_streaks(earned_timestamps, user_tz),
        'time_patterns': _compute_time_patterns(earned_timestamps, user_tz),
        'platforms': _compute_platform_breakdown(profile_games),
        'genres': _compute_genre_breakdown(profile_games, dev_lookup, genre_lookup, theme_lookup, pub_lookup),
        'library': _compute_game_library(profile_games, igdb_lookup, engine_lookup),
        'badges': _compute_badge_stats(profile, gamification),
        'challenges': _compute_challenge_progress(profile),
        'community': _compute_community_stats(profile),
        'recaps': _compute_recap_stats(profile),
        'milestones': _compute_milestones(profile),
        'filters': {
            'exclude_shovelware': exclude_shovelware,
            'exclude_hidden': exclude_hidden,
        },
    }


# ---------------------------------------------------------------------------
# Section: Personal Records
# ---------------------------------------------------------------------------

def _compute_personal_records(profile, earned_timestamps, user_tz,
                              exclude_shovelware=False, exclude_hidden=False,
                              igdb_lookup=None):
    from trophies.models import EarnedTrophy, ProfileGame, MonthlyRecap, Game

    items = []

    # Base querysets with optional filters applied using correct FK paths
    et_base = EarnedTrophy.objects.filter(profile=profile, earned=True, earned_date_time__isnull=False)
    pg_base = ProfileGame.objects.filter(profile=profile)
    if exclude_shovelware:
        et_base = et_base.exclude(trophy__game__shovelware_status__in=['auto_flagged', 'manually_flagged'])
        pg_base = pg_base.exclude(game__shovelware_status__in=['auto_flagged', 'manually_flagged'])
    if exclude_hidden:
        pg_base = pg_base.filter(user_hidden=False)

    # --- Queries for record rows with game context ---

    # First trophy ever
    first = et_base.select_related('trophy__game__concept').order_by('earned_date_time').first()
    if first:
        name, icon = _game_context(first.trophy.game)
        items.append(_record('First Trophy Ever', first.trophy.trophy_name, name, icon, first.earned_date_time))

    # First platinum
    first_plat = (
        et_base.filter(trophy__trophy_type='platinum')
        .select_related('trophy__game__concept')
        .order_by('earned_date_time')
        .first()
    )
    if first_plat:
        name, icon = _game_context(first_plat.trophy.game)
        items.append(_record('First Platinum', name, None, icon, first_plat.earned_date_time))

    # Most recent platinum
    recent_plat = (
        et_base.filter(trophy__trophy_type='platinum')
        .select_related('trophy__game__concept')
        .order_by('-earned_date_time')
        .first()
    )
    if recent_plat:
        name, icon = _game_context(recent_plat.trophy.game)
        items.append(_record('Most Recent Platinum', name, None, icon, recent_plat.earned_date_time))

    # Quickest / longest platinum (first trophy to plat date per game)
    plat_game_ids = list(pg_base.filter(has_plat=True).values_list('game_id', flat=True))
    if plat_game_ids:
        first_per_game = dict(
            et_base.filter(trophy__game_id__in=plat_game_ids)
            .values('trophy__game_id')
            .annotate(first=Min('earned_date_time'))
            .values_list('trophy__game_id', 'first')
        )
        plat_per_game = dict(
            et_base.filter(trophy__trophy_type='platinum', trophy__game_id__in=plat_game_ids)
            .values('trophy__game_id')
            .annotate(plat=Min('earned_date_time'))
            .values_list('trophy__game_id', 'plat')
        )
        time_to_plat = {}
        for gid in plat_game_ids:
            f = first_per_game.get(gid)
            p = plat_per_game.get(gid)
            if f and p and p >= f:
                time_to_plat[gid] = p - f

        if time_to_plat:
            for label, selector in [('Quickest Platinum', min), ('Longest Platinum Journey', max)]:
                gid = selector(time_to_plat, key=time_to_plat.get)
                try:
                    game = Game.objects.select_related('concept').get(id=gid)
                    gname, gicon = _game_context(game)
                    items.append(_record(label, _format_duration(time_to_plat[gid]), gname, gicon))
                except Game.DoesNotExist:
                    pass

    # Playtime records (labeled clearly as total playtime, not time-to-plat)
    for label, ordering in [('Shortest Playtime (Platinum)', 'play_duration'), ('Longest Playtime (Platinum)', '-play_duration')]:
        pg = (
            pg_base.filter(has_plat=True, play_duration__isnull=False)
            .select_related('game__concept')
            .order_by(ordering)
            .first()
        )
        if pg and pg.play_duration:
            name, icon = _game_context(pg.game)
            items.append(_record(label, _format_duration(pg.play_duration), name, icon))

    # Most trophies in one game / fewest (where > 0)
    for label, ordering in [('Most Trophies in One Game', '-earned_trophies_count'), ('Fewest Trophies in a Game', 'earned_trophies_count')]:
        pg = (
            pg_base.filter(earned_trophies_count__gt=0)
            .select_related('game__concept')
            .order_by(ordering)
            .first()
        )
        if pg:
            name, icon = _game_context(pg.game)
            items.append(_record(label, str(pg.earned_trophies_count), name, icon))

    # Most played game (play_count)
    most_played = pg_base.filter(play_count__gt=0).select_related('game__concept').order_by('-play_count').first()
    if most_played:
        name, icon = _game_context(most_played.game)
        detail = f'{most_played.play_count} play session{"s" if most_played.play_count != 1 else ""}'
        items.append(_record('Most Played Game', detail, name, icon))

    # First game played
    first_game = (
        pg_base.filter(first_played_date_time__isnull=False)
        .select_related('game__concept')
        .order_by('first_played_date_time')
        .first()
    )
    if first_game:
        name, icon = _game_context(first_game.game)
        items.append(_record('First Game Played', name, None, icon, first_game.first_played_date_time))

    # First 100% completion
    first_complete = (
        pg_base.filter(progress=100, most_recent_trophy_date__isnull=False)
        .select_related('game__concept')
        .order_by('most_recent_trophy_date')
        .first()
    )
    if first_complete:
        name, icon = _game_context(first_complete.game)
        items.append(_record('First 100% Completion', name, None, icon, first_complete.most_recent_trophy_date))

    # Fastest 100% (first trophy to last trophy on a 100% game)
    complete_game_ids = list(pg_base.filter(progress=100).values_list('game_id', flat=True))
    if complete_game_ids:
        first_per_complete = dict(
            et_base.filter(trophy__game_id__in=complete_game_ids)
            .values('trophy__game_id')
            .annotate(first=Min('earned_date_time'))
            .values_list('trophy__game_id', 'first')
        )
        last_per_complete = dict(
            et_base.filter(trophy__game_id__in=complete_game_ids)
            .values('trophy__game_id')
            .annotate(last=Max('earned_date_time'))
            .values_list('trophy__game_id', 'last')
        )
        time_to_100 = {}
        for gid in complete_game_ids:
            f = first_per_complete.get(gid)
            l = last_per_complete.get(gid)
            if f and l and l >= f:
                time_to_100[gid] = l - f

        if time_to_100:
            fastest_gid = min(time_to_100, key=time_to_100.get)
            try:
                game = Game.objects.select_related('concept').get(id=fastest_gid)
                gname, gicon = _game_context(game)
                items.append(_record('Fastest 100%', _format_duration(time_to_100[fastest_gid]), gname, gicon))
            except Game.DoesNotExist:
                pass

    # Oldest game platted by IGDB release date (single query)
    if igdb_lookup and plat_game_ids:
        plat_pgs = list(
            pg_base.filter(game_id__in=plat_game_ids)
            .select_related('game__concept')
        )
        oldest_plat_release = None
        oldest_plat_date = None
        for pg in plat_pgs:
            cid = pg.game.concept_id if pg.game else None
            if not cid:
                continue
            igdb = igdb_lookup.get(cid)
            if igdb and igdb.igdb_first_release_date:
                if oldest_plat_date is None or igdb.igdb_first_release_date < oldest_plat_date:
                    oldest_plat_date = igdb.igdb_first_release_date
                    name, icon = _game_context(pg.game)
                    oldest_plat_release = _record(
                        'Oldest Game Platted',
                        name, f'Released {oldest_plat_date.strftime("%b %d, %Y")}',
                        icon,
                    )
        if oldest_plat_release:
            items.append(oldest_plat_release)

    # --- Stats derived from shared timestamps (no extra queries) ---

    daily_counts = Counter()
    plat_dates = []       # date objects for day-level stats
    plat_datetimes = []   # full datetimes for precise gap calculation
    for dt, trophy_type in earned_timestamps:
        local_dt = dt.astimezone(user_tz)
        daily_counts[local_dt.date()] += 1
        if trophy_type == 'platinum':
            plat_dates.append(local_dt.date())
            plat_datetimes.append(local_dt)
    plat_dates.sort()
    plat_datetimes.sort()

    # Best day
    if daily_counts:
        best_date, best_count = daily_counts.most_common(1)[0]
        items.append(_record('Most Trophies in a Day', str(best_count), None, None, best_date))

    # Most platinums in a day
    if plat_dates:
        plat_daily = Counter(plat_dates)
        best_plat_date, best_plat_count = plat_daily.most_common(1)[0]
        if best_plat_count > 1:
            items.append(_record('Most Platinums in a Day', str(best_plat_count), None, None, best_plat_date))

    # Best week (7-day sliding window)
    best_week_count, best_week_start = _best_week(daily_counts)
    if best_week_count > 0 and best_week_start:
        end = best_week_start + timedelta(days=6)
        week_range = f'{best_week_start.strftime("%b %d")} - {end.strftime("%b %d, %Y")}'
        items.append(_record('Best Week', str(best_week_count), week_range, None))

    # Plat gap stats (using full datetimes for precision)
    if len(plat_datetimes) >= 2:
        gaps = [(plat_datetimes[i + 1] - plat_datetimes[i]) for i in range(len(plat_datetimes) - 1)]
        shortest_idx = gaps.index(min(gaps))
        longest_idx = gaps.index(max(gaps))
        items.append(_record(
            'Shortest Gap Between Plats',
            _format_duration(gaps[shortest_idx]),
            None, None, plat_datetimes[shortest_idx + 1],
        ))
        items.append(_record(
            'Longest Platinum Drought',
            _format_duration(gaps[longest_idx]),
            None, None, plat_datetimes[longest_idx],
        ))

    # --- MonthlyRecap records ---
    best_recap_plats = (
        MonthlyRecap.objects.filter(profile=profile, is_finalized=True, platinums_earned__gt=0)
        .order_by('-platinums_earned')
        .values('year', 'month', 'platinums_earned')
        .first()
    )
    if best_recap_plats:
        month_name = calendar.month_name[best_recap_plats['month']]
        items.append(_record(
            'Most Platinums in a Month',
            str(best_recap_plats['platinums_earned']),
            f'{month_name} {best_recap_plats["year"]}', None,
        ))

    best_recap_trophies = (
        MonthlyRecap.objects.filter(profile=profile, is_finalized=True, total_trophies_earned__gt=0)
        .order_by('-total_trophies_earned')
        .values('year', 'month', 'total_trophies_earned')
        .first()
    )
    if best_recap_trophies:
        month_name = calendar.month_name[best_recap_trophies['month']]
        items.append(_record(
            'Most Prolific Month',
            str(best_recap_trophies['total_trophies_earned']),
            f'{month_name} {best_recap_trophies["year"]}', None,
        ))

    # --- Playtime aggregates ---
    playtime_agg = (
        pg_base.filter(play_duration__isnull=False)
        .aggregate(
            total=Sum('play_duration'),
            total_trophies=Sum('earned_trophies_count'),
            count=Count('id'),
        )
    )
    total_playtime = playtime_agg['total']
    playtime_count = playtime_agg['count'] or 0
    total_playtime_str = _format_duration(total_playtime) if total_playtime else None
    avg_playtime_str = None
    if total_playtime and playtime_count > 0:
        avg_playtime_str = _format_duration(total_playtime / playtime_count)

    # Trophies per playtime hour
    trophies_per_hour = None
    if total_playtime and playtime_agg['total_trophies']:
        total_hours = total_playtime.total_seconds() / 3600
        if total_hours > 0:
            trophies_per_hour = round(playtime_agg['total_trophies'] / total_hours, 2)

    # Longest playtime without earning platinum or 100%
    longest_no_plat = (
        pg_base.filter(has_plat=False, play_duration__isnull=False, progress__lt=100)
        .select_related('game__concept')
        .order_by('-play_duration')
        .first()
    )
    longest_no_plat_data = None
    if longest_no_plat and longest_no_plat.play_duration:
        name, icon = _game_context(longest_no_plat.game)
        longest_no_plat_data = {
            'name': name,
            'icon': icon,
            'playtime': _format_duration(longest_no_plat.play_duration),
            'progress': longest_no_plat.progress or 0,
        }

    # Most efficient game (highest trophies per hour, min 1 trophy)
    most_efficient = None
    biggest_sink = None
    games_with_playtime = list(
        pg_base.filter(play_duration__isnull=False, earned_trophies_count__gt=0)
        .select_related('game__concept')
    )
    if games_with_playtime:
        def _trophies_per_hr(pg):
            hours = pg.play_duration.total_seconds() / 3600
            return pg.earned_trophies_count / hours if hours > 0 else 0

        best = max(games_with_playtime, key=_trophies_per_hr)
        worst = min(games_with_playtime, key=_trophies_per_hr)
        best_rate = _trophies_per_hr(best)
        worst_rate = _trophies_per_hr(worst)

        if best_rate > 0:
            name, icon = _game_context(best.game)
            most_efficient = {
                'name': name, 'icon': icon,
                'rate': round(best_rate, 1),
                'trophies': best.earned_trophies_count,
                'playtime': _format_duration(best.play_duration),
            }
        if worst_rate < best_rate:
            name, icon = _game_context(worst.game)
            biggest_sink = {
                'name': name, 'icon': icon,
                'rate': round(worst_rate, 2),
                'trophies': worst.earned_trophies_count,
                'playtime': _format_duration(worst.play_duration),
            }

    # Longest commitment (biggest span between first and last played)
    longest_commitment = None
    commitment_candidates = (
        pg_base.filter(
            first_played_date_time__isnull=False,
            last_played_date_time__isnull=False,
        )
        .select_related('game__concept')
    )
    best_span = timedelta(0)
    for pg in commitment_candidates:
        span = pg.last_played_date_time - pg.first_played_date_time
        if span > best_span:
            best_span = span
            name, icon = _game_context(pg.game)
            longest_commitment = {
                'name': name, 'icon': icon,
                'span': _format_duration(span),
                'first': pg.first_played_date_time,
                'last': pg.last_played_date_time,
            }

    # Average playtime per platinum
    avg_playtime_per_plat = None
    plat_playtime = (
        pg_base.filter(has_plat=True, play_duration__isnull=False)
        .aggregate(total=Sum('play_duration'), count=Count('id'))
    )
    if plat_playtime['total'] and plat_playtime['count']:
        avg_playtime_per_plat = _format_duration(plat_playtime['total'] / plat_playtime['count'])

    # Average session length (total playtime / total play sessions)
    avg_session = None
    total_sessions = (
        pg_base.filter(play_count__gt=0)
        .aggregate(sessions=Sum('play_count'))
    )['sessions'] or 0
    if total_playtime and total_sessions > 0:
        avg_session = _format_duration(total_playtime / total_sessions)

    return {
        'items': items,
        'total_playtime': total_playtime_str,
        'avg_playtime': avg_playtime_str,
        'trophies_per_hour': trophies_per_hour,
        'longest_no_plat': longest_no_plat_data,
        'most_efficient': most_efficient,
        'biggest_sink': biggest_sink,
        'longest_commitment': longest_commitment,
        'avg_playtime_per_plat': avg_playtime_per_plat,
        'avg_session': avg_session,
    }


# ---------------------------------------------------------------------------
# Section: Rarity Profile
# ---------------------------------------------------------------------------

def _compute_rarity_profile(profile):
    from trophies.models import EarnedTrophy

    base_qs = EarnedTrophy.objects.filter(
        profile=profile, earned=True, trophy__trophy_earn_rate__isnull=False,
    )

    # Single aggregate for all PSN tier counts + averages
    agg = base_qs.aggregate(
        ultra_rare=Count('id', filter=Q(trophy__trophy_earn_rate__lt=5)),
        very_rare=Count('id', filter=Q(trophy__trophy_earn_rate__gte=5, trophy__trophy_earn_rate__lt=10)),
        rare=Count('id', filter=Q(trophy__trophy_earn_rate__gte=10, trophy__trophy_earn_rate__lt=20)),
        uncommon=Count('id', filter=Q(trophy__trophy_earn_rate__gte=20, trophy__trophy_earn_rate__lt=50)),
        common=Count('id', filter=Q(trophy__trophy_earn_rate__gte=50)),
        sub_1=Count('id', filter=Q(trophy__trophy_earn_rate__lt=1)),
        total=Count('id'),
        avg_rate=Avg('trophy__trophy_earn_rate'),
        avg_plat_rate=Avg('trophy__trophy_earn_rate', filter=Q(trophy__trophy_type='platinum')),
        avg_bronze_rate=Avg('trophy__trophy_earn_rate', filter=Q(trophy__trophy_type='bronze')),
        avg_silver_rate=Avg('trophy__trophy_earn_rate', filter=Q(trophy__trophy_type='silver')),
        avg_gold_rate=Avg('trophy__trophy_earn_rate', filter=Q(trophy__trophy_type='gold')),
    )

    total = agg['total'] or 1

    # PP rarity (earn_rate field is a 0.0-1.0 ratio, not a percentage)
    pp_qs = EarnedTrophy.objects.filter(
        profile=profile, earned=True, trophy__earn_rate__isnull=False, trophy__earn_rate__gt=0,
    )
    pp_agg = pp_qs.aggregate(
        pp_ultra_rare=Count('id', filter=Q(trophy__earn_rate__lt=0.05)),
        pp_very_rare=Count('id', filter=Q(trophy__earn_rate__gte=0.05, trophy__earn_rate__lt=0.10)),
        pp_rare=Count('id', filter=Q(trophy__earn_rate__gte=0.10, trophy__earn_rate__lt=0.20)),
        pp_uncommon=Count('id', filter=Q(trophy__earn_rate__gte=0.20, trophy__earn_rate__lt=0.50)),
        pp_common=Count('id', filter=Q(trophy__earn_rate__gte=0.50)),
        pp_total=Count('id'),
        pp_avg_rate=Avg('trophy__earn_rate'),
    )
    pp_total = pp_agg['pp_total'] or 1

    # Notable trophies (PSN rarity)
    rarest = base_qs.select_related('trophy__game__concept').order_by('trophy__trophy_earn_rate').first()
    rarest_non_plat = (
        base_qs.exclude(trophy__trophy_type='platinum')
        .select_related('trophy__game__concept')
        .order_by('trophy__trophy_earn_rate')
        .first()
    )
    rarest_plat = (
        base_qs.filter(trophy__trophy_type='platinum')
        .select_related('trophy__game__concept')
        .order_by('trophy__trophy_earn_rate')
        .first()
    )
    most_common = base_qs.select_related('trophy__game__concept').order_by('-trophy__trophy_earn_rate').first()
    most_common_plat = (
        base_qs.filter(trophy__trophy_type='platinum')
        .select_related('trophy__game__concept')
        .order_by('-trophy__trophy_earn_rate')
        .first()
    )

    # Rarest trophy by PP rate (may differ from PSN rarest)
    rarest_pp = (
        pp_qs.select_related('trophy__game__concept')
        .order_by('trophy__earn_rate')
        .first()
    )

    # Hardest / easiest game (min 5 earned trophies for meaningful avg)
    game_difficulty = list(
        base_qs.values('trophy__game_id')
        .annotate(avg_rate=Avg('trophy__trophy_earn_rate'), trophy_count=Count('id'))
        .filter(trophy_count__gte=5)
        .order_by('avg_rate')
    )
    hardest_game = _fetch_game_difficulty(game_difficulty[0] if game_difficulty else None)
    easiest_game = _fetch_game_difficulty(game_difficulty[-1] if game_difficulty else None)

    # PSN tier list
    psn_tiers = [
        {'name': 'Ultra Rare', 'threshold': '< 5%', 'count': agg['ultra_rare'], 'pct': round(agg['ultra_rare'] / total * 100, 1)},
        {'name': 'Very Rare', 'threshold': '5 - 10%', 'count': agg['very_rare'], 'pct': round(agg['very_rare'] / total * 100, 1)},
        {'name': 'Rare', 'threshold': '10 - 20%', 'count': agg['rare'], 'pct': round(agg['rare'] / total * 100, 1)},
        {'name': 'Uncommon', 'threshold': '20 - 50%', 'count': agg['uncommon'], 'pct': round(agg['uncommon'] / total * 100, 1)},
        {'name': 'Common', 'threshold': '> 50%', 'count': agg['common'], 'pct': round(agg['common'] / total * 100, 1)},
    ]

    # PP tier list
    pp_tiers = [
        {'name': 'Ultra Rare', 'threshold': '< 5%', 'count': pp_agg['pp_ultra_rare'], 'pct': round(pp_agg['pp_ultra_rare'] / pp_total * 100, 1)},
        {'name': 'Very Rare', 'threshold': '5 - 10%', 'count': pp_agg['pp_very_rare'], 'pct': round(pp_agg['pp_very_rare'] / pp_total * 100, 1)},
        {'name': 'Rare', 'threshold': '10 - 20%', 'count': pp_agg['pp_rare'], 'pct': round(pp_agg['pp_rare'] / pp_total * 100, 1)},
        {'name': 'Uncommon', 'threshold': '20 - 50%', 'count': pp_agg['pp_uncommon'], 'pct': round(pp_agg['pp_uncommon'] / pp_total * 100, 1)},
        {'name': 'Common', 'threshold': '> 50%', 'count': pp_agg['pp_common'], 'pct': round(pp_agg['pp_common'] / pp_total * 100, 1)},
    ]

    # Observation
    ultra_pct = agg['ultra_rare'] / total * 100
    avg_rate = agg['avg_rate'] or 0
    pp_avg_pct = (pp_agg['pp_avg_rate'] or 0) * 100  # convert decimal to percentage
    observation = None
    if ultra_pct > 15:
        observation = f'Rarity hunter: {round(ultra_pct)}% of your trophies are Ultra Rare on PSN'
    elif avg_rate and pp_avg_pct and abs(avg_rate - pp_avg_pct) > 10:
        observation = f'Your trophies average {round(avg_rate, 1)}% on PSN but {round(pp_avg_pct, 1)}% among PlatPursuit hunters'
    elif avg_rate and avg_rate < 20:
        observation = f'Your average trophy has a {round(avg_rate, 1)}% earn rate, rarer than most'

    # Build PP rarest trophy context using PP earn_rate field
    rarest_pp_ctx = None
    if rarest_pp:
        trophy = rarest_pp.trophy
        game = trophy.game
        game_name, game_icon = _game_context(game)
        rarest_pp_ctx = {
            'name': trophy.trophy_name,
            'icon': trophy.trophy_icon_url,
            'type': trophy.trophy_type,
            'earn_rate': round((trophy.earn_rate or 0) * 100, 2),
            'game_name': game_name,
            'game_icon': game_icon,
            'date': rarest_pp.earned_date_time,
        }

    return {
        'tiers': psn_tiers,
        'pp_tiers': pp_tiers,
        'sub_1_count': agg['sub_1'],
        'avg_earn_rate': round(agg['avg_rate'] or 0, 1),
        'avg_plat_rate': round(agg['avg_plat_rate'] or 0, 1),
        'pp_avg_earn_rate': round(pp_avg_pct, 1),
        'avg_by_type': {
            'bronze': round(agg['avg_bronze_rate'] or 0, 1),
            'silver': round(agg['avg_silver_rate'] or 0, 1),
            'gold': round(agg['avg_gold_rate'] or 0, 1),
            'platinum': round(agg['avg_plat_rate'] or 0, 1),
        },
        'rarest': _trophy_context(rarest),
        'rarest_plat': _trophy_context(rarest_plat),
        'rarest_non_plat': _trophy_context(rarest_non_plat),
        'rarest_pp': rarest_pp_ctx,
        'most_common': _trophy_context(most_common),
        'most_common_plat': _trophy_context(most_common_plat),
        'hardest_game': hardest_game,
        'easiest_game': easiest_game,
        'observation': observation,
    }


# ---------------------------------------------------------------------------
# Section: Streaks & Consistency
# ---------------------------------------------------------------------------

def _compute_streaks(earned_timestamps, user_tz):
    if not earned_timestamps:
        return _empty_section()

    # Build sorted unique date sets
    all_dates = set()
    plat_dates_set = set()
    yearly_counts = Counter()
    monthly_counts = Counter()  # (year, month) -> count

    for dt, trophy_type in earned_timestamps:
        local = dt.astimezone(user_tz)
        d = local.date()
        all_dates.add(d)
        yearly_counts[local.year] += 1
        monthly_counts[(local.year, local.month)] += 1
        if trophy_type == 'platinum':
            plat_dates_set.add(d)

    sorted_dates = sorted(all_dates)
    sorted_plat_dates = sorted(plat_dates_set)
    today = timezone.now().astimezone(user_tz).date()

    # Streaks
    longest_len, longest_start, longest_end = _longest_streak(sorted_dates)
    current = _current_streak(sorted_dates, today)
    plat_longest, plat_start, plat_end = _longest_streak(sorted_plat_dates)
    current_plat = _current_streak(sorted_plat_dates, today)
    drought_len, drought_after, drought_before = _longest_drought(sorted_dates)
    plat_drought_len, plat_drought_after, plat_drought_before = _longest_drought(sorted_plat_dates)

    # Activity stats
    total_active = len(all_dates)
    first_date = sorted_dates[0] if sorted_dates else today
    total_span = (today - first_date).days or 1
    active_ratio = round(total_active / total_span * 100, 1)
    days_since_last = (today - sorted_dates[-1]).days if sorted_dates else 0
    days_since_last_plat = (today - sorted_plat_dates[-1]).days if sorted_plat_dates else None
    avg_per_active = round(len(earned_timestamps) / total_active, 1) if total_active else 0

    # Monthly activity stats
    months_with_activity = len([v for v in monthly_counts.values() if v > 0])
    plat_months = {(d.year, d.month) for d in sorted_plat_dates}
    months_with_plat = len(plat_months)

    # Longest monthly streak (consecutive months with 1+ trophy)
    sorted_months = sorted(monthly_counts.keys())
    monthly_streak = _longest_monthly_streak(sorted_months)

    # Most active year / quietest year
    most_active_year = None
    quietest_year = None
    if yearly_counts:
        best_year = max(yearly_counts, key=yearly_counts.get)
        most_active_year = {'year': best_year, 'count': yearly_counts[best_year]}
        worst_year = min(yearly_counts, key=yearly_counts.get)
        quietest_year = {'year': worst_year, 'count': yearly_counts[worst_year]}

    # Most active calendar month historically
    historical_months = Counter()
    for (y, m), count in monthly_counts.items():
        historical_months[m] += count
    best_cal_month = None
    if historical_months:
        best_m = historical_months.most_common(1)[0][0]
        best_cal_month = {
            'month': calendar.month_name[best_m],
            'total': historical_months[best_m],
        }

    # Derived: inactive days, avg per month
    inactive_days = total_span - total_active
    career_months = total_span / 30.44
    avg_per_month = round(len(earned_timestamps) / career_months, 1) if career_months > 0 else 0

    # Best month-over-month improvement
    best_mom = None
    sorted_month_keys = sorted(monthly_counts.keys())
    if len(sorted_month_keys) >= 2:
        best_delta = 0
        best_mom_month = None
        for i in range(1, len(sorted_month_keys)):
            prev_key = sorted_month_keys[i - 1]
            curr_key = sorted_month_keys[i]
            delta = monthly_counts[curr_key] - monthly_counts[prev_key]
            if delta > best_delta:
                best_delta = delta
                best_mom_month = curr_key
        if best_delta > 0 and best_mom_month:
            best_mom = {
                'month': f'{calendar.month_name[best_mom_month[1]]} {best_mom_month[0]}',
                'delta': best_delta,
            }

    # Observation
    observation = None
    if current > 7:
        observation = f"You've been on a {current}-day trophy streak!"
    elif drought_len > 30 and days_since_last < drought_len:
        observation = f'Your longest drought was {drought_len} days, but you came back stronger'

    return {
        'longest_streak': longest_len,
        'longest_streak_start': longest_start,
        'longest_streak_end': longest_end,
        'current_streak': current,
        'plat_streak': plat_longest,
        'plat_streak_start': plat_start,
        'plat_streak_end': plat_end,
        'current_plat_streak': current_plat,
        'longest_drought': drought_len,
        'drought_after': drought_after,
        'drought_before': drought_before,
        'plat_drought': plat_drought_len,
        'plat_drought_after': plat_drought_after,
        'plat_drought_before': plat_drought_before,
        'total_active_days': total_active,
        'inactive_days': inactive_days,
        'active_ratio': active_ratio,
        'days_since_last': days_since_last,
        'days_since_last_plat': days_since_last_plat,
        'avg_per_active_day': avg_per_active,
        'avg_per_month': avg_per_month,
        'months_with_activity': months_with_activity,
        'months_with_plat': months_with_plat,
        'monthly_streak': monthly_streak,
        'most_active_year': most_active_year,
        'quietest_year': quietest_year,
        'best_calendar_month': best_cal_month,
        'best_mom_improvement': best_mom,
        'observation': observation,
    }


# ---------------------------------------------------------------------------
# Section: Time Patterns
# ---------------------------------------------------------------------------

def _compute_time_patterns(earned_timestamps, user_tz):
    if not earned_timestamps:
        return _empty_section()

    hours = []
    weekdays = []
    months = []
    clock_minutes = []

    for dt, _ in earned_timestamps:
        local = dt.astimezone(user_tz)
        hours.append(local.hour)
        weekdays.append(local.weekday())  # 0=Mon, 6=Sun
        months.append(local.month)
        clock_minutes.append(local.hour * 60 + local.minute)

    total = len(hours)

    # Time of day
    time_buckets = [
        ('Morning', 6, 12),
        ('Afternoon', 12, 18),
        ('Evening', 18, 24),
        ('Night', 0, 6),
    ]
    time_distribution = []
    for name, start, end in time_buckets:
        if start < end:
            count = sum(1 for h in hours if start <= h < end)
        else:
            count = sum(1 for h in hours if h >= start or h < end)
        time_distribution.append({
            'name': name, 'count': count, 'pct': round(count / total * 100, 1),
        })

    hour_counts = Counter(hours)
    peak_hour = hour_counts.most_common(1)[0][0]

    # Day of week
    day_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    day_counts = Counter(weekdays)
    day_distribution = [
        {'name': day_names[i], 'count': day_counts.get(i, 0), 'pct': round(day_counts.get(i, 0) / total * 100, 1)}
        for i in range(7)
    ]
    peak_day = day_names[day_counts.most_common(1)[0][0]]

    # Weekend vs weekday
    weekend = sum(1 for w in weekdays if w >= 5)
    weekend_pct = round(weekend / total * 100, 1)

    # Earliest / latest clock time
    earliest = min(clock_minutes)
    latest = max(clock_minutes)

    # Seasonal
    season_map = {12: 'Winter', 1: 'Winter', 2: 'Winter',
                  3: 'Spring', 4: 'Spring', 5: 'Spring',
                  6: 'Summer', 7: 'Summer', 8: 'Summer',
                  9: 'Fall', 10: 'Fall', 11: 'Fall'}
    season_counts = Counter(season_map[m] for m in months)
    season_distribution = [
        {'name': s, 'count': season_counts.get(s, 0), 'pct': round(season_counts.get(s, 0) / total * 100, 1)}
        for s in ['Spring', 'Summer', 'Fall', 'Winter']
    ]

    # Year over year
    yearly = Counter()
    for dt, _ in earned_timestamps:
        yearly[dt.astimezone(user_tz).year] += 1
    yoy = [{'year': y, 'count': yearly[y]} for y in sorted(yearly)]

    # Observation
    night_pct = time_distribution[3]['pct']  # Night is index 3
    morning_pct = time_distribution[0]['pct']
    observation = None
    if night_pct > 50:
        observation = f'Night owl: {round(night_pct)}% of your trophies are earned between midnight and 6 AM'
    elif morning_pct > 50:
        observation = f'Early bird: {round(morning_pct)}% of your trophies are earned before noon'
    elif weekend_pct > 60:
        observation = f'Weekend warrior: {round(weekend_pct)}% of your trophies are earned on weekends'
    elif weekend_pct < 20:
        observation = f'Weekday grinder: {round(100 - weekend_pct, 1)}% of your trophies are earned during the week'

    return {
        'time_distribution': time_distribution,
        'peak_hour': _format_hour(peak_hour),
        'day_distribution': day_distribution,
        'peak_day': peak_day,
        'weekend_pct': weekend_pct,
        'weekday_pct': round(100 - weekend_pct, 1),
        'earliest_time': _format_clock(earliest),
        'latest_time': _format_clock(latest),
        'season_distribution': season_distribution,
        'yearly_counts': yoy,
        'max_yearly': max(yearly.values()) if yearly else 1,
        'observation': observation,
    }


# ---------------------------------------------------------------------------
# Section: Platform Breakdown
# ---------------------------------------------------------------------------

def _compute_platform_breakdown(profile_games):
    platforms = defaultdict(lambda: {'trophies': 0, 'games': 0, 'plats': 0, 'total_progress': 0})

    for pg in profile_games:
        game = pg.game
        if not game or not game.title_platform:
            continue
        for plat in game.title_platform:
            platforms[plat]['games'] += 1
            platforms[plat]['trophies'] += pg.earned_trophies_count
            if pg.has_plat:
                platforms[plat]['plats'] += 1
            platforms[plat]['total_progress'] += pg.progress or 0

    # Sort by trophies desc
    sorted_platforms = sorted(platforms.items(), key=lambda x: x[1]['trophies'], reverse=True)

    items = []
    for name, data in sorted_platforms:
        game_count = data['games'] or 1
        trophies_per_game = round(data['trophies'] / game_count, 1)
        items.append({
            'name': name,
            'trophies': data['trophies'],
            'games': data['games'],
            'plats': data['plats'],
            'avg_progress': round(data['total_progress'] / game_count, 1),
            'trophies_per_game': trophies_per_game,
        })

    # Cross-gen count (games on multiple platforms)
    cross_gen = sum(
        1 for pg in profile_games
        if pg.game and pg.game.title_platform and len(pg.game.title_platform) > 1
    )
    single_platform = sum(
        1 for pg in profile_games
        if pg.game and pg.game.title_platform and len(pg.game.title_platform) == 1
    )

    # Most recent platform
    recent = None
    latest_date = None
    for pg in profile_games:
        if pg.game and pg.game.title_platform and pg.last_played_date_time:
            if latest_date is None or pg.last_played_date_time > latest_date:
                latest_date = pg.last_played_date_time
                recent = pg.game.title_platform[0]

    # First platform played
    first_platform = None
    earliest_date = None
    for pg in profile_games:
        if pg.game and pg.game.title_platform and pg.first_played_date_time:
            if earliest_date is None or pg.first_played_date_time < earliest_date:
                earliest_date = pg.first_played_date_time
                first_platform = pg.game.title_platform[0]

    # Highlight winners (from items already computed)
    most_plats_platform = None
    best_completion_platform = None
    best_tpg_platform = None
    if items:
        by_plats = max(items, key=lambda x: x['plats'])
        if by_plats['plats'] > 0:
            most_plats_platform = by_plats['name']
        by_completion = max(items, key=lambda x: x['avg_progress'])
        if by_completion['avg_progress'] > 0:
            best_completion_platform = {'name': by_completion['name'], 'avg': by_completion['avg_progress']}
        by_tpg = max(items, key=lambda x: x['trophies_per_game'])
        if by_tpg['trophies_per_game'] > 0:
            best_tpg_platform = {'name': by_tpg['name'], 'rate': by_tpg['trophies_per_game']}

    return {
        'items': items,
        'cross_gen': cross_gen,
        'single_platform': single_platform,
        'most_recent': recent,
        'first_platform': first_platform,
        'most_plats_platform': most_plats_platform,
        'best_completion_platform': best_completion_platform,
        'best_tpg_platform': best_tpg_platform,
    }


# ---------------------------------------------------------------------------
# Section: Genre Breakdown
# ---------------------------------------------------------------------------

def _compute_genre_breakdown(profile_games, dev_lookup=None, genre_lookup=None, theme_lookup=None, pub_lookup=None):
    from trophies.util_modules.constants import GENRE_DISPLAY_NAMES

    genre_lookup = genre_lookup or {}
    theme_lookup = theme_lookup or {}
    pub_lookup = pub_lookup or {}

    # Indie threshold: company_size 1-3 (up to 50 employees)
    INDIE_MAX_SIZE = 3

    genres = defaultdict(lambda: {'games': 0, 'plats': 0, 'total_progress': 0})
    themes = defaultdict(lambda: {'games': 0, 'plats': 0})
    publishers = defaultdict(lambda: {'games': 0, 'plats': 0})
    developers = defaultdict(lambda: {'games': 0, 'plats': 0})
    dev_countries = Counter()  # ISO numeric code -> count
    dev_founding_years = []  # (year, company_name) tuples
    seen_companies = set()  # avoid counting same company twice
    indie_count = 0
    aaa_count = 0
    indie_plats = 0
    aaa_plats = 0

    for pg in profile_games:
        concept = pg.game.concept if pg.game else None
        concept_id = concept.id if concept else None
        # Use IGDB genres exclusively (normalized M2M)
        genre_list = genre_lookup.get(concept_id, []) if concept_id else []
        theme_list = theme_lookup.get(concept_id, []) if concept_id else []
        # Use IGDB publisher data exclusively (normalized M2M)
        pub_names = pub_lookup.get(concept_id, []) if concept_id else []

        for genre in genre_list:
            genres[genre]['games'] += 1
            if pg.has_plat:
                genres[genre]['plats'] += 1
            genres[genre]['total_progress'] += pg.progress or 0

        for theme in theme_list:
            themes[theme]['games'] += 1
            if pg.has_plat:
                themes[theme]['plats'] += 1

        for pub_name in pub_names:
            publishers[pub_name]['games'] += 1
            if pg.has_plat:
                publishers[pub_name]['plats'] += 1

        # Developer stats from pre-fetched lookup + indie/AAA classification
        if concept and dev_lookup:
            devs = dev_lookup.get(concept.id, [])
            for company in devs:
                developers[company.name]['games'] += 1
                if pg.has_plat:
                    developers[company.name]['plats'] += 1
                # Track country and founding year (once per company)
                if company.id not in seen_companies:
                    seen_companies.add(company.id)
                    if company.country:
                        dev_countries[company.country] += 1
                    if company.start_date:
                        dev_founding_years.append((company.start_date.year, company.name))
            # Classify by largest developer's company_size
            if devs:
                max_size = max((c.company_size or 0) for c in devs)
                if 0 < max_size <= INDIE_MAX_SIZE:
                    indie_count += 1
                    if pg.has_plat:
                        indie_plats += 1
                elif max_size > INDIE_MAX_SIZE:
                    aaa_count += 1
                    if pg.has_plat:
                        aaa_plats += 1

    # Sort genres by plats desc
    sorted_genres = sorted(genres.items(), key=lambda x: x[1]['plats'], reverse=True)
    genre_items = []
    for name, data in sorted_genres:
        # IGDB genres are already human-readable; PSN genres are UPPER_CASE
        if name in GENRE_DISPLAY_NAMES:
            display = GENRE_DISPLAY_NAMES[name]
        elif name.isupper():
            display = name.replace('_', ' ').title()
        else:
            display = name  # IGDB genre, already formatted
        game_count = data['games'] or 1
        genre_items.append({
            'name': display,
            'raw': name,
            'games': data['games'],
            'plats': data['plats'],
            'avg_progress': round(data['total_progress'] / game_count, 1),
        })

    # Favorite genre
    favorite = genre_items[0]['name'] if genre_items else None
    unique_genres = len(genre_items)

    # Top publishers by plats
    sorted_pubs = sorted(publishers.items(), key=lambda x: x[1]['plats'], reverse=True)[:3]
    top_publishers = [{'name': name, 'plats': data['plats'], 'games': data['games']} for name, data in sorted_pubs]
    unique_publishers = len(publishers)

    # Top developers by plats (from IGDB data)
    sorted_devs = sorted(developers.items(), key=lambda x: x[1]['plats'], reverse=True)[:3]
    top_developers = [{'name': name, 'plats': data['plats'], 'games': data['games']} for name, data in sorted_devs]
    unique_developers = len(developers)

    # Genre diversity (unique genres with platinums)
    genres_with_plats = sum(1 for g in genre_items if g['plats'] > 0)

    # Observation
    observation = None
    total_plats = sum(g['plats'] for g in genre_items)
    if total_plats > 0 and genre_items:
        top_pct = genre_items[0]['plats'] / total_plats * 100
        if top_pct > 40:
            observation = f"Genre specialist: {round(top_pct)}% of your platinums are {genre_items[0]['name']} games"
        elif genres_with_plats >= 10:
            observation = f"Genre explorer: you've earned platinums across {genres_with_plats} different genres"

    # Theme items (sorted by games desc)
    sorted_themes = sorted(themes.items(), key=lambda x: x[1]['games'], reverse=True)
    theme_items = [
        {'name': name.replace('_', ' ').title(), 'games': data['games'], 'plats': data['plats']}
        for name, data in sorted_themes
    ]

    # Indie vs AAA stats
    total_classified = indie_count + aaa_count
    indie_data = None
    if total_classified > 0:
        indie_data = {
            'indie_games': indie_count,
            'aaa_games': aaa_count,
            'indie_plats': indie_plats,
            'aaa_plats': aaa_plats,
            'indie_pct': round(indie_count / total_classified * 100, 1),
            'threshold_label': '50 or fewer employees (IGDB data, may not be complete)',
        }

    return {
        'items': genre_items[:15],  # Top 15 genres
        'favorite': favorite,
        'unique_genres': unique_genres,
        'genres_with_plats': genres_with_plats,
        'theme_items': theme_items[:15],  # Top 15 themes
        'unique_themes': len(theme_items),
        'top_publishers': top_publishers,
        'unique_publishers': unique_publishers,
        'top_developers': top_developers,
        'unique_developers': unique_developers,
        'indie': indie_data,
        'dev_countries': _format_dev_countries(dev_countries),
        'unique_dev_countries': len(dev_countries),
        'dev_studios': _format_dev_founding(dev_founding_years),
        'observation': observation,
    }


# ---------------------------------------------------------------------------
# Section: Game Library Analysis
# ---------------------------------------------------------------------------

def _compute_game_library(profile_games, igdb_lookup=None, engine_lookup=None):
    from trophies.models import UserConceptRating

    igdb_lookup = igdb_lookup or {}
    engine_lookup = engine_lookup or {}

    almost_there = 0
    one_trophy = 0
    zero_progress = 0
    delisted = 0
    online_trophies = 0
    buggy_trophies = 0
    has_dlc = 0
    dlc_completed = 0
    family_ids = set()
    engine_counts = Counter()
    franchise_counts = Counter()
    franchise_plats = Counter()
    category_counts = Counter()
    ttb_values = []  # time_to_beat_completely in seconds
    playtime_vs_estimate = []  # (actual_seconds, estimate_seconds) for comparison
    release_to_play_gaps = []  # timedelta gaps between release and first play
    # Completion tiers
    tier_0_25 = 0
    tier_25_50 = 0
    tier_50_75 = 0
    tier_75_100 = 0
    tier_100 = 0
    # Abandoned game stats
    abandoned_trophy_counts = []  # earned counts for stale, unfinished games
    # Tier 2 (from raw_response)
    game_mode_counts = Counter()
    perspective_counts = Counter()
    keyword_counts = Counter()
    igdb_ratings = []  # (user_rating, critic_rating, concept_id) for scored games
    region_counts = Counter()
    concept_ids = set()

    oldest_unfinished = None
    newest_by_release = None
    oldest_by_release = None

    for pg in profile_games:
        game = pg.game
        concept = game.concept if game else None

        # Count stats
        if 90 <= (pg.progress or 0) < 100:
            almost_there += 1
        if pg.earned_trophies_count == 1:
            one_trophy += 1
        if pg.earned_trophies_count == 0:
            zero_progress += 1
        if game and game.is_delisted:
            delisted += 1
        if game and game.has_online_trophies:
            online_trophies += 1
        if game and game.has_buggy_trophies:
            buggy_trophies += 1
        if game and game.has_trophy_groups:
            has_dlc += 1
            if (pg.progress or 0) == 100:
                dlc_completed += 1
        if concept and concept.family_id:
            family_ids.add(concept.family_id)
        if concept:
            concept_ids.add(concept.id)

        # Completion tier distribution
        progress = pg.progress or 0
        if progress == 100:
            tier_100 += 1
        elif progress >= 75:
            tier_75_100 += 1
        elif progress >= 50:
            tier_50_75 += 1
        elif progress >= 25:
            tier_25_50 += 1
        else:
            tier_0_25 += 1

        # Abandoned games: unfinished + no activity in 6+ months
        if progress < 100 and pg.most_recent_trophy_date and pg.earned_trophies_count > 0:
            months_since = (timezone.now() - pg.most_recent_trophy_date).days / 30.44
            if months_since >= 6:
                abandoned_trophy_counts.append(pg.earned_trophies_count)

        # Engine data from normalized M2M
        if concept:
            for eng_name in engine_lookup.get(concept.id, []):
                engine_counts[eng_name] += 1

        # IGDB enrichment
        if concept:
            igdb = igdb_lookup.get(concept.id)
            if igdb:
                if igdb.franchise_names:
                    for fname in igdb.franchise_names:
                        franchise_counts[fname] += 1
                        if pg.has_plat:
                            franchise_plats[fname] += 1
                if igdb.game_category is not None:
                    cat_label = dict(igdb.GAME_CATEGORY_CHOICES).get(igdb.game_category, 'Other')
                    category_counts[cat_label] += 1
                if igdb.time_to_beat_completely:
                    ttb_values.append(igdb.time_to_beat_completely)
                    # Compare actual playtime vs IGDB estimate
                    if pg.play_duration:
                        actual_secs = pg.play_duration.total_seconds()
                        if actual_secs > 0:
                            playtime_vs_estimate.append((actual_secs, igdb.time_to_beat_completely))
                # Release-to-play gap
                if igdb.igdb_first_release_date and pg.first_played_date_time:
                    gap = pg.first_played_date_time - igdb.igdb_first_release_date
                    if gap.total_seconds() > 0:
                        release_to_play_gaps.append(gap)
                # Tier 2: parse from raw_response
                raw = igdb.raw_response or {}
                for gm in raw.get('game_modes', []):
                    if gm.get('name'):
                        game_mode_counts[gm['name']] += 1
                for pp in raw.get('player_perspectives', []):
                    if pp.get('name'):
                        perspective_counts[pp['name']] += 1
                for kw in raw.get('keywords', []):
                    if kw.get('name'):
                        keyword_counts[kw['name']] += 1
                user_rating = raw.get('rating')
                critic_rating = raw.get('aggregated_rating')
                if user_rating or critic_rating:
                    igdb_ratings.append((user_rating, critic_rating, concept.id))

        # Regions: only count by specific region if the game is regional
        if game:
            if game.is_regional and game.region:
                for r in game.region:
                    region_counts[r] += 1
            else:
                region_counts['Global'] += 1

        # Oldest unfinished (first_played, not 100%)
        if (pg.progress or 0) < 100 and pg.first_played_date_time:
            if oldest_unfinished is None or pg.first_played_date_time < oldest_unfinished['date']:
                name, icon = _game_context(game)
                oldest_unfinished = {
                    'name': name, 'icon': icon,
                    'date': pg.first_played_date_time,
                    'progress': pg.progress,
                }

        # Release date extremes
        release = concept.release_date if concept else None
        if release:
            name, icon = _game_context(game)
            entry = {'name': name, 'icon': icon, 'date': release}
            if newest_by_release is None or release > newest_by_release['date']:
                newest_by_release = entry
            if oldest_by_release is None or release < oldest_by_release['date']:
                oldest_by_release = entry

    # Region distribution
    region_items = [{'name': r, 'count': c} for r, c in region_counts.most_common()]

    # Community ratings for games in the user's library
    community = _compute_library_community_ratings(concept_ids)

    # IGDB aggregates
    top_engines = [{'name': e, 'count': c} for e, c in engine_counts.most_common(5)]
    top_franchises = [
        {'name': f, 'games': franchise_counts[f], 'plats': franchise_plats.get(f, 0)}
        for f, _ in franchise_counts.most_common(5)
    ]
    category_items = [{'name': c, 'count': n} for c, n in category_counts.most_common()]

    # Time-to-beat summary
    ttb_data = None
    if ttb_values:
        avg_ttb = sum(ttb_values) / len(ttb_values)
        total_ttb = sum(ttb_values)
        ttb_data = {
            'games_with_data': len(ttb_values),
            'avg_hours': round(avg_ttb / 3600, 1),
            'total_hours': round(total_ttb / 3600),
            'shortest_hours': round(min(ttb_values) / 3600, 1),
            'longest_hours': round(max(ttb_values) / 3600, 1),
        }

    return {
        'almost_there': almost_there,
        'one_trophy': one_trophy,
        'zero_progress': zero_progress,
        'delisted': delisted,
        'online_trophies': online_trophies,
        'buggy_trophies': buggy_trophies,
        'has_dlc': has_dlc,
        'game_families': len(family_ids),
        'region_items': region_items,
        'oldest_unfinished': oldest_unfinished,
        'newest_by_release': newest_by_release,
        'oldest_by_release': oldest_by_release,
        'community': community,
        'top_engines': top_engines,
        'unique_engines': len(engine_counts),
        'top_franchises': top_franchises,
        'unique_franchises': len(franchise_counts),
        'category_items': category_items,
        'ttb': ttb_data,
        'playtime_vs_estimate': _compute_playtime_comparison(playtime_vs_estimate),
        'release_to_play': _compute_release_gap(release_to_play_gaps),
        'game_modes': [{'name': n, 'count': c} for n, c in game_mode_counts.most_common()],
        'perspectives': [{'name': n, 'count': c} for n, c in perspective_counts.most_common()],
        'top_keywords': [{'name': n, 'count': c} for n, c in keyword_counts.most_common(20)],
        'unique_keywords': len(keyword_counts),
        'igdb_scores': _compute_igdb_scores(igdb_ratings),
        'completion_tiers': _build_completion_tiers(tier_0_25, tier_25_50, tier_50_75, tier_75_100, tier_100),
        'dlc_completed': dlc_completed,
        'abandoned': {
            'count': len(abandoned_trophy_counts),
            'avg_trophies': round(sum(abandoned_trophy_counts) / len(abandoned_trophy_counts), 1) if abandoned_trophy_counts else 0,
        } if abandoned_trophy_counts else None,
    }


def _compute_library_community_ratings(concept_ids):
    """Compute community rating stats for games in the user's library."""
    from trophies.models import UserConceptRating, Concept

    if not concept_ids:
        return None

    # Per-concept averages for games in the user's library
    per_concept = list(
        UserConceptRating.objects.filter(concept_id__in=concept_ids)
        .values('concept_id')
        .annotate(
            avg_difficulty=Avg('difficulty'),
            avg_grindiness=Avg('grindiness'),
            avg_fun=Avg('fun_ranking'),
            avg_overall=Avg('overall_rating'),
            avg_hours=Avg('hours_to_platinum'),
            rating_count=Count('id'),
        )
    )

    if not per_concept:
        return None

    games_rated = len(per_concept)
    games_in_library = len(concept_ids)

    # Library-wide averages (equal weight per game, not per rating)
    avg_difficulty = round(sum(c['avg_difficulty'] or 0 for c in per_concept) / games_rated, 1)
    avg_grindiness = round(sum(c['avg_grindiness'] or 0 for c in per_concept) / games_rated, 1)
    avg_fun = round(sum(c['avg_fun'] or 0 for c in per_concept) / games_rated, 1)
    avg_overall = round(sum(c['avg_overall'] or 0 for c in per_concept) / games_rated, 1)
    avg_hours = round(sum(c['avg_hours'] or 0 for c in per_concept) / games_rated, 1)

    # Find extremes
    concept_lookup = None  # lazy-loaded

    def _get_concept_display(concept_id):
        nonlocal concept_lookup
        if concept_lookup is None:
            extreme_ids = set()
            for key in ['difficulty', 'grindiness', 'fun']:
                if per_concept:
                    sorted_by = sorted(per_concept, key=lambda x: x[f'avg_{key}'] or 0)
                    extreme_ids.add(sorted_by[0]['concept_id'])
                    extreme_ids.add(sorted_by[-1]['concept_id'])
            concepts = Concept.objects.filter(id__in=extreme_ids).values('id', 'unified_title', 'concept_icon_url')
            concept_lookup = {c['id']: c for c in concepts}
        c = concept_lookup.get(concept_id, {})
        return c.get('unified_title', 'Unknown'), c.get('concept_icon_url')

    def _find_extreme(field, highest=True):
        valid = [c for c in per_concept if c[field] is not None]
        if not valid:
            return None
        entry = max(valid, key=lambda x: x[field]) if highest else min(valid, key=lambda x: x[field])
        name, icon = _get_concept_display(entry['concept_id'])
        return {'name': name, 'icon': icon, 'value': round(entry[field], 1)}

    return {
        'games_rated': games_rated,
        'games_in_library': games_in_library,
        'rated_pct': round(games_rated / games_in_library * 100, 1) if games_in_library else 0,
        'avg_difficulty': avg_difficulty,
        'avg_grindiness': avg_grindiness,
        'avg_fun': avg_fun,
        'avg_overall': avg_overall,
        'avg_hours': avg_hours,
        'hardest': _find_extreme('avg_difficulty', highest=True),
        'easiest': _find_extreme('avg_difficulty', highest=False),
        'most_fun': _find_extreme('avg_fun', highest=True),
        'most_grindy': _find_extreme('avg_grindiness', highest=True),
    }


# ---------------------------------------------------------------------------
# Section: Badge & XP Stats
# ---------------------------------------------------------------------------

def _compute_badge_stats(profile, gamification):
    from trophies.models import UserBadge, StageCompletionEvent, Badge

    gam = gamification  # may be None

    # Tier breakdown
    tier_names = {1: 'Bronze', 2: 'Silver', 3: 'Gold', 4: 'Platinum'}
    tier_agg = (
        UserBadge.objects.filter(profile=profile)
        .values('badge__tier')
        .annotate(count=Count('id'))
    )
    tier_breakdown = {tier_names.get(row['badge__tier'], '?'): row['count'] for row in tier_agg}

    # Highest tier
    highest_badge = (
        UserBadge.objects.filter(profile=profile)
        .select_related('badge')
        .order_by('-badge__tier', '-earned_at')
        .first()
    )

    # Most recent badge
    recent_badge = (
        UserBadge.objects.filter(profile=profile)
        .select_related('badge')
        .order_by('-earned_at')
        .first()
    )

    # Stages
    total_stages = StageCompletionEvent.objects.filter(profile=profile).count()
    recent_stage = (
        StageCompletionEvent.objects.filter(profile=profile)
        .select_related('badge', 'concept')
        .order_by('-completed_at')
        .first()
    )

    # Series fully completed (all 4 tiers earned for a series_slug)
    series_completion = (
        UserBadge.objects.filter(profile=profile)
        .values('badge__series_slug')
        .annotate(tier_count=Count('badge__tier', distinct=True))
        .filter(tier_count=4)
    )
    series_completed = series_completion.count()

    # Recent badge count (90 days)
    ninety_days_ago = timezone.now() - timedelta(days=90)
    recent_badge_count = UserBadge.objects.filter(
        profile=profile, earned_at__gte=ninety_days_ago
    ).count()

    # Badge velocity (per month)
    total_badges = gam.total_badges_earned if gam else 0
    first_badge = (
        UserBadge.objects.filter(profile=profile)
        .order_by('earned_at')
        .values_list('earned_at', flat=True)
        .first()
    )
    badge_velocity = 0
    if first_badge and total_badges > 0:
        months_active = max((timezone.now() - first_badge).days / 30.44, 1)
        badge_velocity = round(total_badges / months_active, 1)

    return {
        'total_xp': gam.total_badge_xp if gam else 0,
        'total_badges': total_badges,
        'unique_series': gam.unique_badges_earned if gam else 0,
        'tier_breakdown': tier_breakdown,
        'top_series': _top_series_by_xp(gam),
        'highest_badge': _badge_display(highest_badge),
        'recent_badge': _badge_display(recent_badge),
        'avg_xp_per_badge': round((gam.total_badge_xp if gam else 0) / max(total_badges, 1), 1),
        'total_stages': total_stages,
        'series_completed': series_completed,
        'badge_velocity': badge_velocity,
        'recent_stage': {
            'badge_name': recent_stage.badge.name if recent_stage else None,
            'concept_name': recent_stage.concept.unified_title if recent_stage and recent_stage.concept else None,
            'date': recent_stage.completed_at if recent_stage else None,
        } if recent_stage else None,
        'recent_badges_count_90d': recent_badge_count,
    }


# ---------------------------------------------------------------------------
# Section: Challenge Progress
# ---------------------------------------------------------------------------

def _compute_challenge_progress(profile):
    from trophies.models import (
        Challenge, AZChallengeSlot, CalendarChallengeDay,
        GenreChallengeSlot, GenreBonusSlot, UserMilestone, UserTitle,
    )

    challenges = Challenge.objects.filter(profile=profile, is_deleted=False)
    total_challenges = challenges.count()
    completed_challenges = challenges.filter(is_complete=True).count()

    # A-Z
    az_filled = AZChallengeSlot.objects.filter(
        challenge__profile=profile, challenge__is_deleted=False, is_completed=True,
    ).count()

    # Calendar
    cal_filled = CalendarChallengeDay.objects.filter(
        challenge__profile=profile, challenge__is_deleted=False, is_filled=True,
    ).count()
    cal_multi = CalendarChallengeDay.objects.filter(
        challenge__profile=profile, challenge__is_deleted=False, plat_count__gte=2,
    ).count()

    # Genre
    genre_filled = GenreChallengeSlot.objects.filter(
        challenge__profile=profile, challenge__is_deleted=False, is_completed=True,
    ).count()
    genre_total = GenreChallengeSlot.objects.filter(
        challenge__profile=profile, challenge__is_deleted=False,
    ).count()
    bonus_filled = GenreBonusSlot.objects.filter(
        challenge__profile=profile, challenge__is_deleted=False, is_completed=True,
    ).count()

    # Milestones & titles
    milestones_earned = UserMilestone.objects.filter(profile=profile).count()
    titles_earned = UserTitle.objects.filter(profile=profile).count()

    return {
        'total_challenges': total_challenges,
        'completed_challenges': completed_challenges,
        'az_filled': az_filled,
        'cal_filled': cal_filled,
        'cal_multi_plat': cal_multi,
        'genre_filled': genre_filled,
        'genre_total': genre_total,
        'bonus_filled': bonus_filled,
        'milestones_earned': milestones_earned,
        'titles_earned': titles_earned,
    }


# ---------------------------------------------------------------------------
# Section: Community Contributions
# ---------------------------------------------------------------------------

def _compute_community_stats(profile):
    from trophies.models import Review, UserConceptRating, GameFlag

    # Reviews
    review_agg = Review.objects.filter(profile=profile, is_deleted=False).aggregate(
        count=Count('id'),
        helpful=Coalesce(Sum('helpful_count'), 0),
        funny=Coalesce(Sum('funny_count'), 0),
        words=Coalesce(Sum('word_count'), 0),
        recommended=Count('id', filter=Q(recommended=True)),
    )

    # Most helpful / funniest review
    most_helpful = (
        Review.objects.filter(profile=profile, is_deleted=False, helpful_count__gt=0)
        .select_related('concept')
        .order_by('-helpful_count')
        .first()
    )
    funniest = (
        Review.objects.filter(profile=profile, is_deleted=False, funny_count__gt=0)
        .select_related('concept')
        .order_by('-funny_count')
        .first()
    )

    review_count = review_agg['count'] or 0
    recommended_pct = round(review_agg['recommended'] / review_count * 100, 1) if review_count else 0
    avg_words = round(review_agg['words'] / review_count) if review_count else 0

    # Ratings
    rating_agg = UserConceptRating.objects.filter(profile=profile).aggregate(
        count=Count('id'),
        avg_difficulty=Avg('difficulty'),
        avg_grindiness=Avg('grindiness'),
        avg_fun=Avg('fun_ranking'),
        avg_overall=Avg('overall_rating'),
    )

    return {
        'reviews_written': review_count,
        'helpful_votes': review_agg['helpful'],
        'funny_votes': review_agg['funny'],
        'total_words': review_agg['words'],
        'avg_word_count': avg_words,
        'recommended_pct': recommended_pct,
        'most_helpful': {
            'game': most_helpful.concept.unified_title if most_helpful and most_helpful.concept else None,
            'count': most_helpful.helpful_count if most_helpful else 0,
        } if most_helpful else None,
        'funniest': {
            'game': funniest.concept.unified_title if funniest and funniest.concept else None,
            'count': funniest.funny_count if funniest else 0,
        } if funniest else None,
        'ratings_given': rating_agg['count'] or 0,
        'avg_difficulty': round(rating_agg['avg_difficulty'] or 0, 1),
        'avg_grindiness': round(rating_agg['avg_grindiness'] or 0, 1),
        'avg_fun': round(rating_agg['avg_fun'] or 0, 1),
        'avg_overall': round(rating_agg['avg_overall'] or 0, 1),
        # Community flags
        'flags_submitted': GameFlag.objects.filter(reporter=profile).count(),
        'flags_approved': GameFlag.objects.filter(reporter=profile, status='approved').count(),
    }


# ---------------------------------------------------------------------------
# Section: Monthly Recap Mining
# ---------------------------------------------------------------------------

def _compute_recap_stats(profile):
    from trophies.models import MonthlyRecap

    recaps = MonthlyRecap.objects.filter(profile=profile, is_finalized=True)
    total_recaps = recaps.count()
    if total_recaps == 0:
        return _empty_section()

    agg = recaps.aggregate(
        total_badges=Coalesce(Sum('badges_earned_count'), 0),
        total_xp=Coalesce(Sum('badge_xp_earned'), 0),
        avg_trophies=Avg('total_trophies_earned'),
        avg_plats=Avg('platinums_earned'),
    )

    # Best / worst months
    best_month = recaps.order_by('-total_trophies_earned').values('year', 'month', 'total_trophies_earned').first()
    worst_active = (
        recaps.filter(total_trophies_earned__gt=0)
        .order_by('total_trophies_earned')
        .values('year', 'month', 'total_trophies_earned')
        .first()
    )
    best_plat_month = recaps.order_by('-platinums_earned').values('year', 'month', 'platinums_earned').first()
    best_completion = recaps.order_by('-games_completed').values('year', 'month', 'games_completed').first()

    def _fmt_recap(row, field):
        if not row:
            return None
        return {
            'label': f'{calendar.month_name[row["month"]]} {row["year"]}',
            'value': row[field],
        }

    return {
        'total_recaps': total_recaps,
        'avg_trophies_per_month': round(agg['avg_trophies'] or 0, 1),
        'avg_plats_per_month': round(agg['avg_plats'] or 0, 1),
        'total_recap_badges': agg['total_badges'],
        'total_recap_xp': agg['total_xp'],
        'best_month': _fmt_recap(best_month, 'total_trophies_earned'),
        'worst_active_month': _fmt_recap(worst_active, 'total_trophies_earned'),
        'best_plat_month': _fmt_recap(best_plat_month, 'platinums_earned'),
        'best_completion_month': _fmt_recap(best_completion, 'games_completed'),
    }


# ---------------------------------------------------------------------------
# Section: Milestone Stats
# ---------------------------------------------------------------------------

def _compute_milestones(profile):
    from trophies.models import UserMilestone, UserMilestoneProgress, Milestone, CALENDAR_DAYS_PER_MONTH
    from trophies.milestone_constants import MONTH_MAP, MILESTONE_CATEGORIES

    # All milestones and which ones the user has earned
    all_milestones = list(Milestone.objects.all().values_list('id', 'criteria_type'))
    earned_ids = set(
        UserMilestone.objects.filter(profile=profile)
        .values_list('milestone_id', flat=True)
    )

    total_available = len(all_milestones)
    total_earned = len(earned_ids)
    overall_pct = round(total_earned / total_available * 100, 1) if total_available else 0

    # Build a criteria_type -> milestone_id mapping
    type_to_ids = defaultdict(set)
    for mid, ctype in all_milestones:
        type_to_ids[ctype].add(mid)

    # Per-category breakdown (skip 'overview' which has no criteria_types)
    categories = []
    for slug, config in MILESTONE_CATEGORIES.items():
        ctypes = config.get('criteria_types', [])
        if not ctypes:
            continue
        cat_ids = set()
        for ct in ctypes:
            cat_ids |= type_to_ids.get(ct, set())
        cat_earned = len(cat_ids & earned_ids)
        cat_total = len(cat_ids)
        if cat_total == 0:
            continue
        categories.append({
            'name': config['name'],
            'earned': cat_earned,
            'total': cat_total,
            'pct': round(cat_earned / cat_total * 100, 1),
        })

    # Most recent milestone
    recent = (
        UserMilestone.objects.filter(profile=profile)
        .select_related('milestone')
        .order_by('-earned_at')
        .first()
    )
    most_recent = None
    if recent:
        most_recent = {
            'name': recent.milestone.name,
            'image': recent.milestone.image.url if recent.milestone.image else None,
            'earned_at': recent.earned_at,
        }

    # Next closest unearned milestone
    progress_qs = (
        UserMilestoneProgress.objects.filter(
            profile=profile, progress_value__gt=0,
        )
        .exclude(milestone__id__in=earned_ids)
        .select_related('milestone')
    )
    next_closest = None
    best_pct = -1
    for p in progress_qs:
        m = p.milestone
        required = _get_milestone_required(m, MONTH_MAP, CALENDAR_DAYS_PER_MONTH)
        if required > 0:
            pct = round(p.progress_value / required * 100, 1)
            if pct > best_pct:
                best_pct = pct
                next_closest = {
                    'name': m.name,
                    'image': m.image.url if m.image else None,
                    'progress': p.progress_value,
                    'required': required,
                    'pct': min(pct, 99.9),
                }

    return {
        'total_earned': total_earned,
        'total_available': total_available,
        'overall_pct': overall_pct,
        'categories': categories,
        'most_recent': most_recent,
        'next_closest': next_closest,
    }


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _get_milestone_required(milestone, month_map, days_per_month):
    """Get the effective required value for a milestone.

    Calendar month milestones use the number of days in the month as their
    target (progress tracks filled days), but the model's required_value
    stores 0. This resolves the correct target for display.
    """
    ct = milestone.criteria_type
    if ct in month_map:
        return days_per_month.get(month_map[ct], 0)
    if milestone.required_value and milestone.required_value > 0:
        return milestone.required_value
    # Fall back to criteria_details target for types like calendar_months_total
    target = (milestone.criteria_details or {}).get('target', 0)
    return target if target and target > 0 else 0


def _build_completion_tiers(*counts):
    """Build completion tier list with percentages."""
    names = ['0 - 24%', '25 - 49%', '50 - 74%', '75 - 99%', '100%']
    total = sum(counts) or 1
    return [
        {'name': names[i], 'count': counts[i], 'pct': round(counts[i] / total * 100, 1)}
        for i in range(len(names))
    ]


def _compute_igdb_scores(ratings):
    """Compute average IGDB user and critic scores across the library."""
    if not ratings:
        return None
    user_scores = [r[0] for r in ratings if r[0] is not None]
    critic_scores = [r[1] for r in ratings if r[1] is not None]
    return {
        'avg_user': round(sum(user_scores) / len(user_scores), 1) if user_scores else None,
        'avg_critic': round(sum(critic_scores) / len(critic_scores), 1) if critic_scores else None,
        'user_count': len(user_scores),
        'critic_count': len(critic_scores),
    }


def _compute_playtime_comparison(pairs):
    """Compare actual playtime vs IGDB estimates. Returns ratio and summary."""
    if not pairs:
        return None
    total_actual = sum(a for a, _ in pairs)
    total_estimate = sum(e for _, e in pairs)
    if total_estimate == 0:
        return None
    ratio = total_actual / total_estimate
    if ratio < 1:
        label = f'{round((1 - ratio) * 100)}% faster than IGDB estimates'
    elif ratio > 1:
        label = f'{round((ratio - 1) * 100)}% slower than IGDB estimates'
    else:
        label = 'Right on pace with IGDB estimates'
    return {
        'games_compared': len(pairs),
        'ratio': round(ratio, 2),
        'label': label,
        'actual_hours': round(total_actual / 3600),
        'estimate_hours': round(total_estimate / 3600),
    }


def _compute_release_gap(gaps):
    """Compute average time between game release and first play."""
    if not gaps:
        return None
    avg_days = sum(g.days for g in gaps) / len(gaps)
    if avg_days >= 365:
        avg_display = f'{round(avg_days / 365.25, 1)} years'
    elif avg_days >= 30:
        avg_display = f'{round(avg_days / 30.44, 1)} months'
    else:
        avg_display = f'{round(avg_days)} days'
    shortest = min(gaps)
    longest = max(gaps)
    return {
        'games_counted': len(gaps),
        'avg_display': avg_display,
        'avg_days': round(avg_days),
        'shortest_days': shortest.days,
        'longest_days': longest.days,
    }


def _format_dev_countries(country_counter):
    """Convert ISO 3166-1 numeric country codes to display names with counts."""
    # Common gaming industry countries (ISO 3166-1 numeric)
    COUNTRY_NAMES = {
        36: 'Australia', 40: 'Austria', 56: 'Belgium', 76: 'Brazil',
        124: 'Canada', 156: 'China', 203: 'Czechia', 208: 'Denmark',
        246: 'Finland', 250: 'France', 276: 'Germany', 300: 'Greece',
        344: 'Hong Kong', 348: 'Hungary', 356: 'India', 360: 'Indonesia',
        372: 'Ireland', 376: 'Israel', 380: 'Italy', 392: 'Japan',
        410: 'South Korea', 458: 'Malaysia', 484: 'Mexico', 528: 'Netherlands',
        554: 'New Zealand', 578: 'Norway', 616: 'Poland', 620: 'Portugal',
        642: 'Romania', 643: 'Russia', 702: 'Singapore', 710: 'South Africa',
        724: 'Spain', 752: 'Sweden', 756: 'Switzerland', 158: 'Taiwan',
        764: 'Thailand', 792: 'Turkey', 804: 'Ukraine',
        826: 'United Kingdom', 840: 'United States', 704: 'Vietnam',
    }
    if not country_counter:
        return []
    items = []
    for code, count in country_counter.most_common(10):
        name = COUNTRY_NAMES.get(code, f'Country {code}')
        items.append({'name': name, 'count': count})
    return items


def _format_dev_founding(founding_years):
    """Build oldest/newest studio stats from (year, name) tuples."""
    if not founding_years:
        return None
    sorted_years = sorted(founding_years, key=lambda x: x[0])
    oldest = sorted_years[0]
    newest = sorted_years[-1]
    return {
        'oldest_year': oldest[0],
        'oldest_name': oldest[1],
        'newest_year': newest[0],
        'newest_name': newest[1],
        'total_with_dates': len(founding_years),
    }


def _build_game_filter_q(exclude_shovelware, exclude_hidden):
    """Build a Q object for filtering ProfileGame querysets by toggle state."""
    filters = Q()
    if exclude_shovelware:
        filters &= ~Q(game__shovelware_status__in=['auto_flagged', 'manually_flagged'])
    if exclude_hidden:
        filters &= Q(user_hidden=False)
    return filters if filters != Q() else None



def _get_user_timezone(profile):
    """Get the user's configured timezone, defaulting to UTC."""
    try:
        tz_name = profile.user.user_timezone or 'UTC'
        return pytz.timezone(tz_name)
    except (pytz.exceptions.UnknownTimeZoneError, AttributeError):
        return pytz.UTC


def _game_context(game):
    """Return (display_name, icon_url) for a game, preferring Concept data."""
    if not game:
        return 'Unknown Game', None
    concept = getattr(game, 'concept', None)
    name = (concept.unified_title if concept and concept.unified_title else None) or game.title_name or 'Unknown Game'
    icon = (concept.concept_icon_url if concept and concept.concept_icon_url else None) or game.title_image
    return name, icon


def _trophy_context(earned_trophy):
    """Extract display info from an EarnedTrophy (with select_related loaded)."""
    if not earned_trophy:
        return None
    trophy = earned_trophy.trophy
    game = trophy.game
    game_name, game_icon = _game_context(game)
    return {
        'name': trophy.trophy_name,
        'icon': trophy.trophy_icon_url,
        'type': trophy.trophy_type,
        'earn_rate': round(trophy.trophy_earn_rate or 0, 2),
        'game_name': game_name,
        'game_icon': game_icon,
        'date': earned_trophy.earned_date_time,
    }


def _record(label, value, subtitle=None, icon=None, date=None):
    """Build a personal record dict."""
    return {
        'label': label,
        'value': value,
        'subtitle': subtitle,
        'icon': icon,
        'date': date,
    }


def _format_duration(td):
    """Format a timedelta for human display."""
    if td is None:
        return None
    total_seconds = int(td.total_seconds())
    if total_seconds < 0:
        return None
    days = total_seconds // 86400
    hours = (total_seconds % 86400) // 3600
    minutes = (total_seconds % 3600) // 60
    if days >= 365:
        years = days // 365
        remaining = days % 365
        return f'{years}y {remaining}d'
    if days > 0:
        return f'{days}d {hours}h'
    if hours > 0:
        return f'{hours}h {minutes}m'
    if minutes > 0:
        return f'{minutes}m'
    return '< 1m'


def _format_hour(hour):
    """Convert 0-23 hour to 12h display string."""
    if hour == 0:
        return '12 AM'
    if hour < 12:
        return f'{hour} AM'
    if hour == 12:
        return '12 PM'
    return f'{hour - 12} PM'


def _format_clock(minutes_since_midnight):
    """Format total minutes since midnight as 12h clock time."""
    hour = minutes_since_midnight // 60
    minute = minutes_since_midnight % 60
    suffix = 'AM' if hour < 12 else 'PM'
    display_hour = hour % 12 or 12
    return f'{display_hour}:{minute:02d} {suffix}'


def _longest_streak(sorted_dates):
    """Find the longest run of consecutive dates. Returns (length, start, end)."""
    if not sorted_dates:
        return 0, None, None
    best_len = 1
    best_start = sorted_dates[0]
    best_end = sorted_dates[0]
    curr_len = 1
    curr_start = sorted_dates[0]
    for i in range(1, len(sorted_dates)):
        if (sorted_dates[i] - sorted_dates[i - 1]).days == 1:
            curr_len += 1
        else:
            if curr_len > best_len:
                best_len = curr_len
                best_start = curr_start
                best_end = sorted_dates[i - 1]
            curr_len = 1
            curr_start = sorted_dates[i]
    if curr_len > best_len:
        best_len = curr_len
        best_start = curr_start
        best_end = sorted_dates[-1]
    return best_len, best_start, best_end


def _current_streak(sorted_dates, today):
    """Find current streak length (must include today or yesterday)."""
    if not sorted_dates:
        return 0
    last = sorted_dates[-1]
    if (today - last).days > 1:
        return 0
    streak = 1
    for i in range(len(sorted_dates) - 1, 0, -1):
        if (sorted_dates[i] - sorted_dates[i - 1]).days == 1:
            streak += 1
        else:
            break
    return streak


def _longest_drought(sorted_dates):
    """Find longest gap between consecutive trophy dates. Returns (days, gap_start, gap_end)."""
    if len(sorted_dates) < 2:
        return 0, None, None
    best_gap = 0
    best_start = None
    best_end = None
    for i in range(1, len(sorted_dates)):
        gap = (sorted_dates[i] - sorted_dates[i - 1]).days - 1
        if gap > best_gap:
            best_gap = gap
            best_start = sorted_dates[i - 1]
            best_end = sorted_dates[i]
    return best_gap, best_start, best_end


def _best_week(daily_counts):
    """Find the 7-day window with the most trophies (O(n) sliding window)."""
    if not daily_counts:
        return 0, None
    entries = sorted(daily_counts.items())
    best_count = 0
    best_start = None
    window_sum = 0
    left = 0
    for right in range(len(entries)):
        window_sum += entries[right][1]
        while (entries[right][0] - entries[left][0]).days >= 7:
            window_sum -= entries[left][1]
            left += 1
        if window_sum > best_count:
            best_count = window_sum
            best_start = entries[left][0]
    return best_count, best_start


def _longest_monthly_streak(sorted_year_month_tuples):
    """Find longest run of consecutive (year, month) pairs."""
    if not sorted_year_month_tuples:
        return 0
    best = 1
    curr = 1
    for i in range(1, len(sorted_year_month_tuples)):
        prev_y, prev_m = sorted_year_month_tuples[i - 1]
        curr_y, curr_m = sorted_year_month_tuples[i]
        # Check if consecutive month
        if (curr_y == prev_y and curr_m == prev_m + 1) or (curr_y == prev_y + 1 and prev_m == 12 and curr_m == 1):
            curr += 1
        else:
            best = max(best, curr)
            curr = 1
    return max(best, curr)


def _fetch_game_difficulty(row):
    """Look up game name/icon for a game difficulty aggregate row."""
    if not row:
        return None
    from trophies.models import Game
    try:
        game = Game.objects.select_related('concept').get(id=row['trophy__game_id'])
        name, icon = _game_context(game)
        return {'name': name, 'icon': icon, 'avg_rate': round(row['avg_rate'], 2)}
    except Game.DoesNotExist:
        return None


def _badge_display(user_badge):
    """Build display dict from a UserBadge with select_related('badge')."""
    if not user_badge:
        return None
    badge = user_badge.badge
    tier_names = {1: 'Bronze', 2: 'Silver', 3: 'Gold', 4: 'Platinum'}
    return {
        'name': badge.name,
        'series': badge.display_series or badge.series_slug,
        'tier': tier_names.get(badge.tier, '?'),
        'image': badge.badge_image.url if badge.badge_image else None,
        'earned_at': user_badge.earned_at,
    }


def _top_series_by_xp(gamification):
    """Extract top 5 badge series by XP from ProfileGamification."""
    if not gamification or not gamification.series_badge_xp:
        return []
    series_xp = gamification.series_badge_xp
    sorted_series = sorted(series_xp.items(), key=lambda x: x[1], reverse=True)[:5]
    return [{'series': slug, 'xp': xp} for slug, xp in sorted_series]


def _empty_section():
    """Placeholder for sections with no data."""
    return {'empty': True}
