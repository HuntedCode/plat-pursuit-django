"""
Dashboard module registry and data provider service.

The dashboard uses a Module Registry pattern where each module is a self-contained
triple: a Python data provider function, an HTML partial template, and optional JS.

Adding a new module:
    1. Write the provider function (in this file or import from a dedicated module)
    2. Add a descriptor dict to DASHBOARD_MODULES referencing the provider callable
    3. Create the partial template in templates/trophies/partials/dashboard/
"""
import inspect
import logging
from collections import defaultdict
from django.core.cache import cache
from django.db.models import F, FloatField, ExpressionWrapper

logger = logging.getLogger(__name__)

# Maximum number of modules free users can hide
MAX_FREE_HIDDEN = 3

# Showcase profile for premium previews (PlatPursuit team account)
SHOWCASE_PROFILE_ID = 3


def get_effective_premium(request):
    """
    Return effective premium status, accounting for staff preview override.

    Staff users can toggle a session variable to simulate premium/free views.
    Falls back to real profile.user_is_premium when no override is set.
    """
    preview = request.session.get('dashboard_preview_premium')
    if preview is not None:
        return preview
    return request.user.profile.user_is_premium

# Default cache TTL for lazy-loaded modules (seconds)
DEFAULT_CACHE_TTL = 600  # 10 minutes

# Valid module sizes and their CSS grid classes
# Grid: grid-cols-2 (tablet) / lg:grid-cols-4 (desktop) / 2xl:grid-cols-6 (1536px+)
VALID_SIZES = ('small', 'medium', 'large')
VALID_LOAD_STRATEGIES = ('server', 'lazy')

SIZE_GRID_CLASSES = {
    'small':  'col-span-2 lg:col-span-2 2xl:col-span-2',   # full tablet, 1/2 desktop, 1/3 wide
    'medium': 'col-span-2 lg:col-span-2 2xl:col-span-3',   # full tablet, 1/2 desktop, 1/2 wide
    'large':  'col-span-2 lg:col-span-4 2xl:col-span-6',   # full everywhere
}

# Item limits per module size
SIZE_LIMITS = {
    'small': 3,
    'medium': 6,
    'large': 10,
}


# ---------------------------------------------------------------------------
# Data Providers
# ---------------------------------------------------------------------------

def provide_trophy_snapshot(profile):
    """Trophy collection summary. Zero additional queries (all on Profile)."""
    total_earned = profile.total_trophies  # total_trophies is already the earned count
    total_all = total_earned + profile.total_unearned
    return {
        'total_plats': profile.total_plats,
        'total_golds': profile.total_golds,
        'total_silvers': profile.total_silvers,
        'total_bronzes': profile.total_bronzes,
        'total_trophies': total_all,
        'total_earned': total_earned,
        'total_unearned': profile.total_unearned,
        'total_games': profile.total_games,
        'total_completes': profile.total_completes,
        'total_hiddens': profile.total_hiddens,
        'avg_progress': profile.avg_progress,
        'trophy_level': profile.trophy_level,
        'tier': profile.tier,
        'is_plus': profile.is_plus,
        'earn_rate': round(total_earned / total_all * 100, 1) if total_all else 0,
    }


def provide_recent_platinums(profile, settings=None):
    """Last N platinum trophies earned with game info and rarity."""
    from trophies.models import EarnedTrophy

    settings = settings or {}
    limit = settings.get('limit', 6)
    plats = (
        EarnedTrophy.objects
        .filter(profile=profile, trophy__trophy_type='platinum', earned=True)
        .select_related('trophy__game__concept')
        .order_by('-earned_date_time')[:limit]
    )

    platinums = []
    for et in plats:
        game = et.trophy.game
        concept = getattr(game, 'concept', None) if game else None
        platinums.append({
            'game_name': concept.unified_title if concept else game.title_name if game else 'Unknown',
            'icon_url': concept.concept_icon_url if concept else (game.title_image if game else ''),
            'earned_date': et.earned_date_time,
            'earn_rate': et.trophy.trophy_earn_rate,
            'np_communication_id': game.np_communication_id if game else None,
        })

    return {'platinums': platinums}


def _find_challenge(profile, challenge_type):
    """Find a user's active challenge, falling back to most recently completed."""
    from trophies.models import Challenge
    qs = Challenge.objects.filter(
        profile=profile, challenge_type=challenge_type, is_deleted=False
    )
    return (
        qs.filter(is_complete=False).first()
        or qs.filter(is_complete=True).order_by('-completed_at').first()
    )


def provide_challenge_hub(profile, size='large'):
    """Overview of all 3 challenge types with mini visual previews."""
    from trophies.services.challenge_service import get_calendar_stats, get_calendar_month_data
    from django.utils import timezone
    import pytz

    # Get user's timezone for calendar today highlight
    tz_name = profile.user.user_timezone if profile.user else 'UTC'
    user_tz = pytz.timezone(tz_name or 'UTC')
    now_local = timezone.now().astimezone(user_tz)

    result = {}
    for ctype in ('az', 'calendar', 'genre'):
        challenge = _find_challenge(profile, ctype)

        if not challenge:
            result[ctype] = None
            continue

        data = {
            'challenge_id': challenge.id,
            'challenge_name': challenge.name,
            'is_complete': challenge.is_complete,
            'completed_at': challenge.completed_at,
        }

        if ctype == 'az':
            data['filled'] = challenge.filled_count
            data['completed'] = challenge.completed_count
            data['total'] = 26
            data['pct'] = round(challenge.completed_count / 26 * 100) if 26 else 0
            # Letter strip data
            slots = challenge.az_slots.all().order_by('letter')
            data['slots'] = [
                {'letter': s.letter, 'filled': s.game_id is not None, 'completed': s.is_completed}
                for s in slots
            ]
        elif ctype == 'calendar':
            month_data = get_calendar_month_data(challenge)
            stats = get_calendar_stats(challenge, month_data=month_data)
            filled = stats.get('total_filled', 0)
            data['filled'] = filled
            data['total'] = 365
            data['streak'] = stats.get('longest_streak', 0)
            data['pct'] = round(filled / 365 * 100)
            # Current month mini-calendar with today highlight
            current_month_num = now_local.month
            current_month = month_data[current_month_num - 1]
            data['current_month'] = {
                'name': current_month['month_name'],
                'weekday_offset': current_month['weekday_offset'],
                'today': now_local.day,
                'days': [{'day': d['day'], 'is_filled': d['is_filled']} for d in current_month['days']],
            }
        elif ctype == 'genre':
            from trophies.services.challenge_service import get_subgenre_status
            data['filled'] = challenge.filled_count
            data['completed'] = challenge.completed_count
            total = challenge.total_items or challenge.genre_slots.count()
            data['total'] = total
            data['bonus_count'] = challenge.bonus_count
            data['pct'] = round(challenge.completed_count / total * 100) if total else 0
            # Genre tag data
            slots = challenge.genre_slots.all().order_by('genre')
            data['slots'] = [
                {'genre': s.genre_display or s.genre, 'filled': s.concept_id is not None, 'completed': s.is_completed}
                for s in slots
            ]
            # Subgenre tags with status
            subgenre_status = get_subgenre_status(challenge)
            data['subgenres'] = sorted([
                {'name': key.replace('_', ' ').title(), 'status': status}
                for key, status in subgenre_status.items()
            ], key=lambda s: (s['status'] != 'platted', s['name']))
            data['subgenre_total'] = len(subgenre_status)
            data['platted_subgenre_count'] = sum(1 for v in subgenre_status.values() if v == 'platted')

        result[ctype] = data

    return result


def provide_badge_progress(profile, settings=None):
    """In-progress badges sorted by completion percentage.

    Only shows the next earnable tier per series: Bronze if unearned,
    Silver only if Bronze is earned, Gold only if Silver is earned, etc.
    """
    from trophies.models import UserBadgeProgress, UserBadge

    settings = settings or {}
    limit = settings.get('limit', 4)

    # Fetch more than needed so we can filter by prerequisite tier in Python
    fetch_limit = limit * 3
    progress_list = list(
        UserBadgeProgress.objects
        .filter(profile=profile, completed_concepts__gt=0)
        .select_related('badge', 'badge__base_badge')
        .filter(badge__required_stages__gt=0, badge__is_live=True)
        .annotate(
            pct=ExpressionWrapper(
                F('completed_concepts') * 100.0 / F('badge__required_stages'),
                output_field=FloatField()
            )
        )
        .exclude(pct__gte=100)
        .order_by('-pct')[:fetch_limit]
    )

    # Pre-fetch earned badge IDs for prerequisite checking (single query)
    earned_badge_ids = set(
        UserBadge.objects.filter(profile=profile).values_list('badge_id', flat=True)
    )

    # Build lookup of badges by (series_slug, tier) for prerequisite resolution
    from trophies.models import Badge
    series_slugs = {bp.badge.series_slug for bp in progress_list}
    badges_by_key = {}
    if series_slugs:
        for b in Badge.objects.filter(series_slug__in=series_slugs, is_live=True).only('id', 'series_slug', 'tier'):
            badges_by_key[(b.series_slug, b.tier)] = b.id

    badges_in_progress = []
    for bp in progress_list:
        badge = bp.badge

        # Only show if prerequisite tier is met:
        # Tier 1 (Bronze) has no prerequisite. Higher tiers require previous tier earned.
        if badge.tier > 1:
            prev_badge_id = badges_by_key.get((badge.series_slug, badge.tier - 1))
            if not prev_badge_id or prev_badge_id not in earned_badge_ids:
                continue

        badges_in_progress.append({
            'layers': badge.get_badge_layers(),
            'series_name': badge.effective_display_series or badge.name,
            'completed': bp.completed_concepts,
            'required': badge.required_stages,
            'pct': round(bp.pct, 1),
            'tier': badge.tier,
            'tier_name': badge.get_tier_display(),
            'series_slug': badge.series_slug,
        })

        if len(badges_in_progress) >= limit:
            break

    # Overall stats from ProfileGamification (reverse OneToOne, may not exist)
    from trophies.models import ProfileGamification
    try:
        gamification = profile.gamification
    except ProfileGamification.DoesNotExist:
        gamification = None

    return {
        'badges_in_progress': badges_in_progress,
        'total_earned': gamification.total_badges_earned if gamification else 0,
        'unique_earned': gamification.unique_badges_earned if gamification else 0,
    }


def provide_recent_badges(profile, settings=None):
    """Recently earned badges, newest first, with community earn count."""
    from trophies.models import UserBadge

    settings = settings or {}
    limit = settings.get('limit', 6)

    recent = (
        UserBadge.objects
        .filter(profile=profile)
        .select_related('badge', 'badge__base_badge')
        .order_by('-earned_at')[:limit]
    )

    badges = []
    for ub in recent:
        badge = ub.badge
        badges.append({
            'layers': badge.get_badge_layers(),
            'series_name': badge.effective_display_series or badge.name,
            'tier': badge.tier,
            'tier_name': badge.get_tier_display(),
            'series_slug': badge.series_slug,
            'earned_at': ub.earned_at,
            'is_displayed': ub.is_displayed,
            'earned_count': badge.earned_count,
        })

    return {'badges': badges}


def provide_recent_activity(profile, settings=None):
    """Trophy-focused activity feed with grouping by game + day.

    Trophies are grouped by game + local date (timezone-aware). Platinums
    are always shown as standalone events. Badges are never grouped.
    """
    from trophies.models import EarnedTrophy, UserBadge
    import pytz

    settings = settings or {}
    limit = settings.get('limit', 8)

    # Get user timezone for date grouping
    tz_name = profile.user.user_timezone if profile.user else 'UTC'
    user_tz = pytz.timezone(tz_name or 'UTC')

    # Fetch more than needed since grouping reduces count
    fetch_limit = limit * 4
    trophy_qs = (
        EarnedTrophy.objects
        .filter(profile=profile, earned=True, earned_date_time__isnull=False)
        .select_related('trophy__game__concept')
        .order_by('-earned_date_time')[:fetch_limit]
    )

    badge_qs = (
        UserBadge.objects
        .filter(profile=profile)
        .select_related('badge', 'badge__base_badge')
        .order_by('-earned_at')[:limit]
    )

    events = []

    # Group trophies by (game, local_date), but platinums are always standalone
    groups = defaultdict(list)
    for et in trophy_qs:
        game = et.trophy.game
        concept = getattr(game, 'concept', None) if game else None
        local_dt = et.earned_date_time.astimezone(user_tz)
        local_date = local_dt.date()
        np_id = game.np_communication_id if game else None

        if et.trophy.trophy_type == 'platinum':
            # Platinums are always standalone
            events.append({
                'type': 'platinum',
                'name': concept.unified_title if concept else game.title_name if game else 'Unknown',
                'icon_url': concept.concept_icon_url if concept else (game.title_image if game else ''),
                'np_communication_id': np_id,
                'date': et.earned_date_time,
                'earn_rate': et.trophy.trophy_earn_rate,
            })
        else:
            groups[(np_id, local_date)].append(et)

    # Build grouped trophy events
    for (np_id, local_date), trophy_list in groups.items():
        first = trophy_list[0]
        game = first.trophy.game
        concept = getattr(game, 'concept', None) if game else None
        # Count by type for the badge display
        type_counts = defaultdict(int)
        for et in trophy_list:
            type_counts[et.trophy.trophy_type] += 1

        events.append({
            'type': 'trophy_group',
            'count': len(trophy_list),
            'game_name': concept.unified_title if concept else game.title_name if game else 'Unknown',
            'icon_url': concept.concept_icon_url if concept else (game.title_image if game else ''),
            'np_communication_id': np_id,
            'type_counts': dict(type_counts),
            'date': max(et.earned_date_time for et in trophy_list),
        })

    # Badge events (never grouped)
    for ub in badge_qs:
        badge = ub.badge
        events.append({
            'type': 'badge',
            'name': badge.effective_display_series or badge.name,
            'tier_name': badge.get_tier_display(),
            'tier': badge.tier,
            'series_slug': badge.series_slug,
            'layers': badge.get_badge_layers(),
            'date': ub.earned_at,
        })

    events.sort(key=lambda e: e['date'], reverse=True)
    return {'events': events[:limit]}


def provide_monthly_recap_preview(profile):
    """Teaser stats for the most recent finalized monthly recap.

    Always pulls from the last completed month, not the current one.
    Handles the gap where the previous month's recap hasn't been generated yet.
    """
    from trophies.models import MonthlyRecap

    recap = (
        MonthlyRecap.objects
        .filter(profile=profile, is_finalized=True)
        .order_by('-year', '-month')
        .first()
    )

    if not recap:
        return {'has_recap': False}

    import calendar
    month_name = f'{calendar.month_name[recap.month]} {recap.year}'

    return {
        'has_recap': True,
        'month_name': month_name,
        'year': recap.year,
        'month': recap.month,
        'platinums': recap.platinums_earned,
        'total_trophies': recap.total_trophies_earned,
        'games_completed': recap.games_completed,
        'games_started': recap.games_started or 0,
    }


def provide_quick_settings(profile):
    """User settings for inline dashboard controls. Zero extra queries."""
    user = profile.user
    return {
        'hide_hiddens': profile.hide_hiddens,
        'hide_zeros': profile.hide_zeros,
        'user_timezone': user.user_timezone if user else 'UTC',
        'default_region': user.default_region if user else '',
        'use_24hr_clock': user.use_24hr_clock if user else False,
    }


def provide_badge_stats(profile, settings=None):
    """Badge collection analytics: tier breakdown, rarest badge, completion rate."""
    from trophies.models import UserBadge, Badge

    user_badges = list(
        UserBadge.objects
        .filter(profile=profile)
        .select_related('badge', 'badge__base_badge')
        .order_by('-earned_at')
    )

    if not user_badges:
        return {'has_stats': False}

    # Tier breakdown
    tier_counts = {1: 0, 2: 0, 3: 0, 4: 0}
    series_slugs = set()
    rarest_badge = None
    rarest_earned_count = float('inf')
    oldest_badge = None

    for ub in user_badges:
        badge = ub.badge
        tier_counts[badge.tier] = tier_counts.get(badge.tier, 0) + 1
        series_slugs.add(badge.series_slug)

        # Rarest = lowest earned_count
        if badge.earned_count and badge.earned_count < rarest_earned_count:
            rarest_earned_count = badge.earned_count
            rarest_badge = {
                'name': badge.effective_display_series or badge.name,
                'tier_name': badge.get_tier_display(),
                'tier': badge.tier,
                'series_slug': badge.series_slug,
                'layers': badge.get_badge_layers(),
                'earned_count': badge.earned_count,
            }

    # Oldest badge is last in the list (ordered -earned_at, so last = oldest)
    oldest_ub = user_badges[-1]
    oldest_badge = {
        'name': oldest_ub.badge.effective_display_series or oldest_ub.badge.name,
        'series_slug': oldest_ub.badge.series_slug,
        'layers': oldest_ub.badge.get_badge_layers(),
        'earned_at': oldest_ub.earned_at,
    }

    # Collection rate: unique series earned vs total live series
    total_live_series = Badge.objects.filter(
        is_live=True, tier=1
    ).values('series_slug').distinct().count()
    collection_pct = round(len(series_slugs) / total_live_series * 100) if total_live_series else 0

    return {
        'has_stats': True,
        'total_badges': len(user_badges),
        'unique_series': len(series_slugs),
        'tier_counts': {
            'bronze': tier_counts.get(1, 0),
            'silver': tier_counts.get(2, 0),
            'gold': tier_counts.get(3, 0),
            'platinum': tier_counts.get(4, 0),
        },
        'rarest_badge': rarest_badge,
        'oldest_badge': oldest_badge,
        'collection_pct': collection_pct,
        'total_live_series': total_live_series,
    }


def provide_badge_xp_leaderboard(profile, settings=None):
    """Badge XP stats and leaderboard position from Redis sorted sets."""
    from trophies.models import ProfileGamification
    from trophies.services.redis_leaderboard_service import (
        get_xp_rank, get_xp_count, get_xp_top, get_xp_neighborhood,
    )

    try:
        gamification = profile.gamification
    except ProfileGamification.DoesNotExist:
        return {'has_stats': False}

    total_xp = gamification.total_badge_xp
    if total_xp == 0:
        return {'has_stats': False}

    display_username = profile.display_psn_username
    user_rank = get_xp_rank(profile.id)
    total_participants = get_xp_count()

    TOP_N = 5
    NEIGHBORHOOD = 2

    def _format_entry(e):
        return {
            'rank': e['rank'],
            'psn_username': e['psn_username'],
            'avatar_url': e.get('avatar_url', ''),
            'flag': e.get('flag', ''),
            'is_premium': e.get('is_premium', False),
            'total_xp': e['total_xp'],
            'total_xp_formatted': f"{e['total_xp']:,}",
            'total_badges': e.get('total_badges', 0),
            'is_self': e['psn_username'] == display_username,
        }

    user_in_top = user_rank is not None and user_rank <= TOP_N
    show_gap = False
    entries = []

    if user_in_top:
        for e in get_xp_top(TOP_N):
            entries.append(_format_entry(e))
    elif user_rank is not None:
        for e in get_xp_top(3):
            entries.append(_format_entry(e))
        neighborhood_entries = get_xp_neighborhood(profile.id, above=NEIGHBORHOOD, below=NEIGHBORHOOD)
        show_gap = neighborhood_entries and neighborhood_entries[0]['rank'] > 4
        for e in neighborhood_entries:
            if e['rank'] > 3:
                entries.append(_format_entry(e))
    else:
        for e in get_xp_top(TOP_N):
            entries.append(_format_entry(e))

    return {
        'has_stats': True,
        'total_xp': total_xp,
        'total_xp_formatted': f"{total_xp:,}",
        'total_badges': gamification.total_badges_earned,
        'unique_series': gamification.unique_badges_earned,
        'user_rank': user_rank,
        'total_participants': total_participants,
        'leaderboard_entries': entries,
        'user_in_top': user_in_top,
        'show_gap': show_gap,
    }


def provide_country_xp_leaderboard(profile, settings=None):
    """Country-specific XP leaderboard: rank, stats, and mini leaderboard within the user's country."""
    from trophies.models import ProfileGamification
    from trophies.services.redis_leaderboard_service import (
        get_country_xp_rank, get_country_xp_count,
        get_country_xp_top, get_country_xp_neighborhood,
    )

    if not profile.country_code:
        return {'has_stats': False, 'no_country': True}

    try:
        gamification = profile.gamification
    except ProfileGamification.DoesNotExist:
        return {'has_stats': False}

    total_xp = gamification.total_badge_xp
    if total_xp == 0:
        return {'has_stats': False}

    country_code = profile.country_code
    display_username = profile.display_psn_username
    user_rank = get_country_xp_rank(country_code, profile.id)
    total_participants = get_country_xp_count(country_code)

    TOP_N = 5
    NEIGHBORHOOD = 2

    def _format_entry(e):
        return {
            'rank': e['rank'],
            'psn_username': e['psn_username'],
            'avatar_url': e.get('avatar_url', ''),
            'flag': e.get('flag', ''),
            'is_premium': e.get('is_premium', False),
            'total_xp': e['total_xp'],
            'total_xp_formatted': f"{e['total_xp']:,}",
            'total_badges': e.get('total_badges', 0),
            'is_self': e['psn_username'] == display_username,
        }

    user_in_top = user_rank is not None and user_rank <= TOP_N
    show_gap = False
    entries = []

    if user_in_top:
        for e in get_country_xp_top(country_code, TOP_N):
            entries.append(_format_entry(e))
    elif user_rank is not None:
        for e in get_country_xp_top(country_code, 3):
            entries.append(_format_entry(e))
        neighborhood_entries = get_country_xp_neighborhood(
            country_code, profile.id, above=NEIGHBORHOOD, below=NEIGHBORHOOD
        )
        show_gap = neighborhood_entries and neighborhood_entries[0]['rank'] > 4
        for e in neighborhood_entries:
            if e['rank'] > 3:
                entries.append(_format_entry(e))
    else:
        for e in get_country_xp_top(country_code, TOP_N):
            entries.append(_format_entry(e))

    return {
        'has_stats': True,
        'country_code': country_code,
        'country_name': profile.country or country_code,
        'flag': profile.flag or '',
        'total_xp': total_xp,
        'total_xp_formatted': f"{total_xp:,}",
        'user_rank': user_rank,
        'total_participants': total_participants,
        'leaderboard_entries': entries,
        'user_in_top': user_in_top,
        'show_gap': show_gap,
    }


def provide_az_challenge(profile):
    """Full 26-letter A-Z challenge grid with game icons and completion status."""
    challenge = _find_challenge(profile, 'az')
    if not challenge:
        return {'has_challenge': False}

    slots_qs = challenge.az_slots.all().select_related(
        'game', 'game__concept'
    ).order_by('letter')

    slots = []
    most_recent_plat = None
    most_recent_plat_at = None
    next_target = None

    for s in slots_qs:
        game = s.game
        concept = getattr(game, 'concept', None) if game else None
        if s.is_completed:
            state = 'completed'
        elif game:
            state = 'assigned'
        else:
            state = 'empty'

        game_name = concept.unified_title if concept else (game.title_name if game else None)
        icon_url = concept.concept_icon_url if concept else (game.title_image if game else None)

        slots.append({
            'letter': s.letter,
            'state': state,
            'game_name': game_name,
            'icon_url': icon_url,
        })

        # Track most recently completed slot (by completed_at timestamp)
        if state == 'completed' and s.completed_at:
            if most_recent_plat_at is None or s.completed_at > most_recent_plat_at:
                most_recent_plat_at = s.completed_at
                most_recent_plat = {
                    'letter': s.letter,
                    'game_name': game_name,
                    'icon_url': icon_url,
                    'completed_at': s.completed_at,
                }

        # First incomplete slot is the next target
        if next_target is None and state in ('assigned', 'empty'):
            next_target = {'letter': s.letter, 'game_name': game_name}

    return {
        'has_challenge': True,
        'challenge_id': challenge.id,
        'challenge_name': challenge.name,
        'is_complete': challenge.is_complete,
        'completed_at': challenge.completed_at,
        'completed_count': challenge.completed_count,
        'filled_count': challenge.filled_count,
        'total': 26,
        'pct': round(challenge.completed_count / 26 * 100),
        'slots': slots,
        'most_recent_plat': most_recent_plat,
        'next_target': next_target,
    }


def provide_genre_challenge(profile):
    """Genre slots with completion status and subgenre tag cloud."""
    from trophies.services.challenge_service import get_subgenre_status

    challenge = _find_challenge(profile, 'genre')
    if not challenge:
        return {'has_challenge': False}

    # Genre slots
    slots_qs = challenge.genre_slots.all().select_related('concept').order_by('genre')
    slots = []
    most_recent_plat = None
    most_recent_plat_at = None
    next_target = None

    for s in slots_qs:
        concept = s.concept
        if s.is_completed:
            state = 'completed'
        elif concept:
            state = 'assigned'
        else:
            state = 'empty'

        genre_name = s.genre_display or s.genre
        concept_title = concept.unified_title if concept else None
        icon_url = concept.concept_icon_url if concept else None

        slots.append({
            'genre': genre_name,
            'state': state,
            'concept_title': concept_title,
            'icon_url': icon_url,
        })

        # Track most recently completed slot
        if state == 'completed' and s.completed_at:
            if most_recent_plat_at is None or s.completed_at > most_recent_plat_at:
                most_recent_plat_at = s.completed_at
                most_recent_plat = {
                    'genre': genre_name,
                    'concept_title': concept_title,
                    'icon_url': icon_url,
                    'completed_at': s.completed_at,
                }

        # First incomplete slot is the next target
        if next_target is None and state in ('assigned', 'empty'):
            next_target = {'genre': genre_name, 'concept_title': concept_title}

    # Bonus slots
    bonus_qs = challenge.bonus_slots.all().select_related('concept')
    bonus_slots = []
    for b in bonus_qs:
        concept = b.concept
        bonus_slots.append({
            'concept_title': concept.unified_title if concept else None,
            'icon_url': concept.concept_icon_url if concept else None,
            'is_completed': b.is_completed,
        })

    # Subgenres
    subgenre_status = get_subgenre_status(challenge)
    subgenres = sorted([
        {'name': key.replace('_', ' ').title(), 'status': status}
        for key, status in subgenre_status.items()
    ], key=lambda sg: (sg['status'] != 'platted', sg['name']))

    total = challenge.total_items or challenge.genre_slots.count()

    return {
        'has_challenge': True,
        'challenge_id': challenge.id,
        'challenge_name': challenge.name,
        'is_complete': challenge.is_complete,
        'completed_at': challenge.completed_at,
        'completed_count': challenge.completed_count,
        'filled_count': challenge.filled_count,
        'total': total,
        'pct': round(challenge.completed_count / total * 100) if total else 0,
        'slots': slots,
        'bonus_slots': bonus_slots,
        'bonus_count': challenge.bonus_count,
        'subgenres': subgenres,
        'subgenre_total': len(subgenre_status),
        'platted_subgenre_count': sum(1 for v in subgenre_status.values() if v == 'platted'),
        'most_recent_plat': most_recent_plat,
        'next_target': next_target,
    }


def provide_calendar_challenge(profile):
    """Full 12-month perpetual calendar showing platinum earns per day."""
    from trophies.services.challenge_service import get_calendar_month_data, get_calendar_stats
    from django.utils import timezone
    import pytz

    challenge = _find_challenge(profile, 'calendar')
    if not challenge:
        return {'has_challenge': False}

    month_data = get_calendar_month_data(challenge)
    stats = get_calendar_stats(challenge, month_data=month_data)

    # User timezone for today highlight
    tz_name = profile.user.user_timezone if profile.user else 'UTC'
    user_tz = pytz.timezone(tz_name or 'UTC')
    now_local = timezone.now().astimezone(user_tz)

    # Serialize months (strip game info, use integer weekday_offset)
    months = []
    for m in month_data:
        months.append({
            'month_name': m['month_name'],
            'month_abbr': m['month_abbr'],
            'weekday_offset': m['weekday_offset'],
            'filled_count': m['filled_count'],
            'num_days': m['num_days'],
            'days': [{'day': d['day'], 'is_filled': d['is_filled']} for d in m['days']],
        })

    total_filled = stats.get('total_filled', 0)
    return {
        'has_challenge': True,
        'challenge_id': challenge.id,
        'is_complete': challenge.is_complete,
        'completed_at': challenge.completed_at,
        'total_filled': total_filled,
        'total_days': 365,
        'pct': round(total_filled / 365 * 100),
        'longest_streak': stats.get('longest_streak', 0),
        'best_month_name': stats.get('best_month_name', ''),
        'best_month_filled': stats.get('best_month_filled', 0),
        'today_month': now_local.month,
        'today_day': now_local.day,
        'months': months,
    }


def provide_completion_milestones(profile, settings=None):
    """Games at 90%+ completion that aren't 100% yet. The finish line is in sight."""
    from trophies.models import ProfileGame

    settings = settings or {}
    limit = settings.get('limit', 6)
    threshold = settings.get('threshold', 90)

    qs = (
        ProfileGame.objects.filter(
            profile=profile,
            progress__gte=threshold,
            progress__lt=100,
            user_hidden=False,
        )
        .select_related('game', 'game__concept')
        .order_by('-progress')
    )

    if profile.hide_hiddens:
        qs = qs.exclude(hidden_flag=True)

    games = []
    for pg in qs[:limit]:
        game = pg.game
        concept = getattr(game, 'concept', None)
        remaining = pg.unearned_trophies_count
        games.append({
            'game_name': concept.unified_title if concept else game.title_name,
            'icon_url': concept.concept_icon_url if concept else game.title_image,
            'np_communication_id': game.np_communication_id,
            'progress': pg.progress,
            'remaining_trophies': remaining,
            'earned_count': pg.earned_trophies_count,
            'total_count': pg.earned_trophies_count + remaining,
        })

    return {'games': games, 'threshold': threshold}


def provide_milestone_tracker(profile, settings=None):
    """In-progress milestones sorted by completion %, plus recently earned."""
    from trophies.models import UserMilestoneProgress, UserMilestone

    settings = settings or {}
    limit = settings.get('limit', 6)

    # Earned milestone IDs
    earned_ids = set(
        UserMilestone.objects.filter(profile=profile)
        .values_list('milestone_id', flat=True)
    )

    # Recently earned (last 3)
    recent_earned_qs = (
        UserMilestone.objects.filter(profile=profile)
        .select_related('milestone')
        .order_by('-earned_at')[:3]
    )
    earned_list = []
    for um in recent_earned_qs:
        m = um.milestone
        earned_list.append({
            'name': m.name,
            'description': m.description,
            'image_url': m.image.url if m.image else '',
            'earned_at': um.earned_at,
        })

    # In-progress milestones: fetch all with progress, sort by pct in Python
    progress_qs = (
        UserMilestoneProgress.objects.filter(
            profile=profile, progress_value__gt=0
        )
        .select_related('milestone')
        .exclude(milestone__id__in=earned_ids)
    )

    in_progress = []
    for p in progress_qs:
        m = p.milestone
        if m.required_value <= 0:
            continue
        pct = round(p.progress_value / m.required_value * 100, 1)
        if pct >= 100:
            continue
        in_progress.append({
            'name': m.name,
            'description': m.description,
            'image_url': m.image.url if m.image else '',
            'progress_value': p.progress_value,
            'required_value': m.required_value,
            'pct': min(pct, 99.9),
        })

    in_progress.sort(key=lambda x: x['pct'], reverse=True)
    in_progress = in_progress[:limit]

    return {
        'in_progress': in_progress,
        'recently_earned': earned_list,
        'total_earned': len(earned_ids),
    }


def provide_my_reviews(profile, settings=None):
    """Review engagement: recent votes on your reviews and overall stats."""
    from trophies.models import Review, ReviewVote
    from django.db.models import Count, Sum
    from django.utils import timezone
    import datetime

    settings = settings or {}
    limit = settings.get('limit', 6)

    # Summary stats
    stats = Review.objects.filter(
        profile=profile, is_deleted=False
    ).aggregate(
        total=Count('id'),
        total_helpful=Sum('helpful_count'),
        total_funny=Sum('funny_count'),
        total_replies=Sum('reply_count'),
    )

    total = stats['total'] or 0
    if total == 0:
        return {
            'has_reviews': False,
            'total_reviews': 0,
            'total_helpful': 0,
            'total_funny': 0,
            'total_replies': 0,
            'recent_engagement': [],
            'new_votes_count': 0,
        }

    # Recent engagement: votes on the user's reviews in the last 7 days
    week_ago = timezone.now() - datetime.timedelta(days=7)
    recent_votes = (
        ReviewVote.objects.filter(
            review__profile=profile,
            review__is_deleted=False,
            created_at__gte=week_ago,
        )
        .exclude(profile=profile)  # exclude self-votes
        .select_related('review__concept_trophy_group__concept')
        .order_by('-created_at')[:limit]
    )

    engagement = []
    for vote in recent_votes:
        review = vote.review
        ctg = review.concept_trophy_group
        concept = ctg.concept if ctg else None
        engagement.append({
            'game_name': concept.unified_title if concept else 'Unknown',
            'icon_url': concept.concept_icon_url if concept else '',
            'vote_type': vote.vote_type,
            'voted_at': vote.created_at,
            'concept_slug': concept.slug if concept else '',
        })

    # Total new votes this week
    new_votes_count = (
        ReviewVote.objects.filter(
            review__profile=profile,
            review__is_deleted=False,
            created_at__gte=week_ago,
        )
        .exclude(profile=profile)
        .count()
    )

    return {
        'has_reviews': True,
        'total_reviews': total,
        'total_helpful': stats['total_helpful'] or 0,
        'total_funny': stats['total_funny'] or 0,
        'total_replies': stats['total_replies'] or 0,
        'recent_engagement': engagement,
        'new_votes_count': new_votes_count,
    }


def provide_rarity_showcase(profile, settings=None):
    """User's rarest earned trophies, ordered by earn rate ascending."""
    from trophies.models import EarnedTrophy

    settings = settings or {}
    limit = settings.get('limit', 6)

    rarest_qs = (
        EarnedTrophy.objects
        .filter(profile=profile, earned=True, trophy__trophy_earn_rate__gt=0)
        .select_related('trophy__game__concept')
        .order_by('trophy__trophy_earn_rate')[:limit]
    )

    trophies = []
    for et in rarest_qs:
        trophy = et.trophy
        game = trophy.game
        concept = getattr(game, 'concept', None) if game else None
        trophies.append({
            'trophy_name': trophy.trophy_name,
            'trophy_type': trophy.trophy_type,
            'trophy_detail': trophy.trophy_detail or '',
            'trophy_icon_url': trophy.trophy_icon_url or '',
            'earn_rate': trophy.trophy_earn_rate,
            'game_name': concept.unified_title if concept else (game.title_name if game else 'Unknown'),
            'icon_url': concept.concept_icon_url if concept else (game.title_image if game else ''),
            'np_communication_id': game.np_communication_id if game else None,
            'earned_date': et.earned_date_time,
        })

    return {'trophies': trophies, 'has_trophies': bool(trophies)}


def provide_rate_my_games(profile):
    """CTA card showing unrated platinums with preview strip."""
    from trophies.services.review_hub_service import ReviewHubService
    from trophies.models import EarnedTrophy, UserConceptRating, Concept

    unrated_count = ReviewHubService.get_unrated_platinum_count(profile)
    unreviewed_count = ReviewHubService.get_unreviewed_platinum_count(profile)

    # Get platted concept IDs (non-shovelware)
    plat_concept_ids = list(
        EarnedTrophy.objects
        .filter(profile=profile, earned=True, trophy__trophy_type='platinum')
        .exclude(trophy__game__shovelware_status__in=['auto_flagged', 'manually_flagged'])
        .values_list('trophy__game__concept_id', flat=True)
        .distinct()
    )
    total_plats = len(plat_concept_ids)

    # Preview: up to 12 unrated game icons for the scrollable strip
    preview_games = []
    if unrated_count > 0 and plat_concept_ids:
        rated_ids = set(
            UserConceptRating.objects
            .filter(profile=profile, concept_trophy_group__isnull=True)
            .values_list('concept_id', flat=True)
        )
        unrated_ids = [cid for cid in plat_concept_ids if cid and cid not in rated_ids][:12]
        if unrated_ids:
            for c in Concept.objects.filter(id__in=unrated_ids).exclude(slug='').exclude(slug__isnull=True).values('unified_title', 'concept_icon_url', 'slug'):
                preview_games.append({
                    'name': c['unified_title'],
                    'icon_url': c['concept_icon_url'] or '',
                    'slug': c['slug'],
                })

    rated_count = max(0, total_plats - unrated_count)
    return {
        'unrated_count': unrated_count,
        'unreviewed_count': unreviewed_count,
        'rated_count': rated_count,
        'total_plats': total_plats,
        'preview_games': preview_games,
        'has_unrated': unrated_count > 0,
        'rate_pct': round(rated_count / total_plats * 100) if total_plats else 100,
    }


def provide_badge_showcase(profile, settings=None):
    """
    Badge showcase: shows the user's displayed badge and all earned badges
    for selection. Allows users to pick which badge to feature on their profile.
    """
    from trophies.models import UserBadge

    earned_badges = list(
        UserBadge.objects
        .filter(profile=profile)
        .select_related('badge', 'badge__base_badge')
        .order_by('-badge__tier', '-earned_at')
    )

    displayed_badge = None
    badge_list = []
    seen_series = set()

    for ub in earned_badges:
        badge = ub.badge

        # Deduplicate by series: only show the highest tier per series
        # (query is ordered by -badge__tier so first per series is highest)
        if badge.series_slug in seen_series:
            # Still check if this tier is the displayed one
            if ub.is_displayed and not displayed_badge:
                pass  # Fall through to build entry for displayed badge
            else:
                continue
        seen_series.add(badge.series_slug)

        try:
            layers = badge.get_badge_layers()
            image_url = layers.get('main', '')
            has_custom = layers.get('has_custom_image', False)
        except Exception:
            image_url = ''
            has_custom = False

        # Only show badges with custom artwork
        if not has_custom:
            continue

        entry = {
            'id': badge.id,
            'name': badge.effective_display_series or badge.series_slug,
            'series': badge.effective_display_series or badge.series_slug,
            'tier': badge.tier,
            'tier_name': {1: 'Bronze', 2: 'Silver', 3: 'Gold', 4: 'Platinum'}.get(badge.tier, ''),
            'image_url': image_url,
            'is_displayed': ub.is_displayed,
        }
        badge_list.append(entry)

        if ub.is_displayed:
            displayed_badge = entry

    # Fallback: auto-select highest tier badge if none is displayed
    if not displayed_badge and badge_list:
        displayed_badge = badge_list[0]  # Already sorted by -tier, -earned_at

    # Fetch profile showcase selections (premium feature)
    from trophies.models import ProfileBadgeShowcase
    showcase_ids = list(
        ProfileBadgeShowcase.objects.filter(profile=profile)
        .order_by('display_order')
        .values_list('badge_id', flat=True)
    )

    return {
        'displayed_badge': displayed_badge,
        'badges': badge_list,
        'total_badges': len(badge_list),
        'showcase_badge_ids': showcase_ids,
        'showcase_count': len(showcase_ids),
        'is_premium': profile.user_is_premium,
    }


def provide_profile_card_preview(profile, settings=None):
    """
    Profile card preview: provides theme, premium status, and available themes
    for the client-side preview module with inline theme picker.
    The actual card HTML is fetched via /api/v1/profile-card/html/ on the client.
    """
    from trophies.models import ProfileCardSettings
    from trophies.themes import get_available_themes_for_grid
    from django.urls import reverse

    card_settings, _ = ProfileCardSettings.objects.get_or_create(profile=profile)
    is_premium = profile.user_is_premium

    # Build compact theme list: key, name, accent_color
    themes = []
    for key, data in get_available_themes_for_grid(include_game_art=False):
        themes.append({
            'key': key,
            'name': data['name'],
            'css': data['background_css'],
        })

    return {
        'theme': card_settings.card_theme or 'default',
        'is_premium': is_premium,
        'themes': themes,
        'shareables_url': reverse('my_shareables') + '?tab=profile_card',
    }


def provide_recent_platinum_card(profile):
    """Most recent platinum share card data for client-side HTML preview."""
    from trophies.models import EarnedTrophy

    et = (
        EarnedTrophy.objects
        .filter(profile=profile, earned=True, trophy__trophy_type='platinum')
        .select_related('trophy__game__concept')
        .order_by('-earned_date_time')
        .first()
    )

    if not et:
        return {'has_platinum': False}

    trophy = et.trophy
    game = trophy.game
    concept = getattr(game, 'concept', None) if game else None

    return {
        'has_platinum': True,
        'earned_trophy_id': et.id,
        'game_name': concept.unified_title if concept else (game.title_name if game else 'Unknown'),
        'icon_url': concept.concept_icon_url if concept else (game.title_image if game else ''),
        'trophy_name': trophy.trophy_name,
        'earn_rate': trophy.trophy_earn_rate,
        'earned_date': et.earned_date_time,
    }


def provide_challenge_share_cards(profile):
    """Challenge IDs for client-side share card HTML previews."""
    challenges = {}
    for ctype in ('az', 'calendar', 'genre'):
        challenge = _find_challenge(profile, ctype)
        if challenge:
            challenges[ctype] = {
                'challenge_id': challenge.id,
                'challenge_name': challenge.name,
                'is_complete': challenge.is_complete,
            }

    return {
        'challenges': challenges,
        'has_challenges': bool(challenges),
    }


def provide_recap_share_card(profile):
    """Most recent finalized recap data for client-side share card preview."""
    from trophies.models import MonthlyRecap
    import calendar as cal_module

    recap = (
        MonthlyRecap.objects
        .filter(profile=profile, is_finalized=True)
        .order_by('-year', '-month')
        .first()
    )

    if not recap:
        return {'has_recap': False}

    return {
        'has_recap': True,
        'has_been_viewed': recap.has_been_viewed,
        'year': recap.year,
        'month': recap.month,
        'month_name': f"{cal_module.month_name[recap.month]} {recap.year}",
    }


def provide_platinum_grid_cta(profile):
    """CTA for the Platinum Grid share image builder."""
    from django.urls import reverse
    from trophies.models import EarnedTrophy

    plat_count = EarnedTrophy.objects.filter(
        profile=profile, earned=True, trophy__trophy_type='platinum'
    ).count()

    # Grab up to 12 recent plat icons for a mini preview grid
    preview_ets = (
        EarnedTrophy.objects
        .filter(profile=profile, earned=True, trophy__trophy_type='platinum')
        .select_related('trophy__game')
        .order_by('-earned_date_time')[:12]
    )
    preview_icons = [
        et.trophy.game.title_image or et.trophy.game.title_icon_url or ''
        for et in preview_ets
    ]
    # Pad to 12 for consistent 4x3 grid
    while len(preview_icons) < 12:
        preview_icons.append('')

    return {
        'plat_count': plat_count,
        'grid_url': reverse('platinum_grid'),
        'preview_icons': preview_icons,
    }


# ---------------------------------------------------------------------------
# Premium module providers
# ---------------------------------------------------------------------------


def provide_advanced_stats(profile, settings=None):
    """
    Deep analytics: earning velocity, rarity profile, platform breakdown,
    and completion tier distribution. Supports date range filtering.
    """
    from trophies.models import EarnedTrophy, ProfileGame
    from django.db.models import Min, Max, Count, Avg, Q
    from django.db.models.functions import TruncDate
    from django.utils import timezone
    from datetime import timedelta

    settings = settings or {}
    date_range_key = settings.get('range', 'all')

    RANGE_DAYS = {'7d': 7, '30d': 30, '90d': 90, '1y': 365}
    RANGE_LABELS = {'7d': 'Last 7 Days', '30d': 'Last 30 Days', '90d': 'Last 90 Days', '1y': 'Last Year', 'all': 'All Time'}
    range_label = RANGE_LABELS.get(date_range_key, 'All Time')

    base_qs = EarnedTrophy.objects.filter(
        profile=profile, earned=True, earned_date_time__isnull=False,
    )

    # Apply date range filter
    now = timezone.now()
    if date_range_key in RANGE_DAYS:
        cutoff = now - timedelta(days=RANGE_DAYS[date_range_key])
        earned_qs = base_qs.filter(earned_date_time__gte=cutoff)
    else:
        earned_qs = base_qs

    total_earned = earned_qs.count()
    if total_earned == 0:
        return {'has_data': False, 'range': date_range_key, 'range_label': range_label}

    # --- Earning Velocity ---
    date_agg = earned_qs.aggregate(
        first=Min('earned_date_time'), last=Max('earned_date_time'),
    )
    first_date = date_agg['first']
    last_date = date_agg['last']

    # Avg per month over the selected range
    if first_date and last_date:
        months_span = max(
            ((last_date.year - first_date.year) * 12
             + last_date.month - first_date.month),
            1,
        )
        avg_per_month = round(total_earned / months_span, 1)
    else:
        avg_per_month = 0

    # Avg per week over the selected range
    if first_date and last_date:
        days_span = max((last_date - first_date).days, 1)
        avg_per_week = round(total_earned / (days_span / 7), 1)
    else:
        avg_per_week = 0

    days_since_last = (now - last_date).days if last_date else None

    # Active days (distinct days with at least one trophy)
    active_days = (
        earned_qs
        .annotate(day=TruncDate('earned_date_time'))
        .values('day')
        .distinct()
        .count()
    )

    # Best week (most trophies in any 7-day window)
    # Use a sliding window over daily counts for efficiency
    daily_counts = list(
        earned_qs
        .annotate(day=TruncDate('earned_date_time'))
        .values('day')
        .annotate(n=Count('id'))
        .order_by('day')
        .values_list('day', 'n')
    )
    best_week = 0
    if daily_counts:
        from collections import deque
        window = deque()
        window_sum = 0
        for day, n in daily_counts:
            window.append((day, n))
            window_sum += n
            while (day - window[0][0]).days >= 7:
                _, old_n = window.popleft()
                window_sum -= old_n
            best_week = max(best_week, window_sum)

    # --- Rarity Profile ---
    rarity_qs = earned_qs.filter(trophy__trophy_earn_rate__gt=0)
    rarity = rarity_qs.aggregate(
        avg_earn_rate=Avg('trophy__trophy_earn_rate'),
        ultra_rare=Count('id', filter=Q(trophy__trophy_earn_rate__lt=5)),
        very_rare=Count('id', filter=Q(
            trophy__trophy_earn_rate__gte=5, trophy__trophy_earn_rate__lt=10,
        )),
        rare=Count('id', filter=Q(
            trophy__trophy_earn_rate__gte=10, trophy__trophy_earn_rate__lt=20,
        )),
        uncommon=Count('id', filter=Q(
            trophy__trophy_earn_rate__gte=20, trophy__trophy_earn_rate__lt=50,
        )),
        common=Count('id', filter=Q(trophy__trophy_earn_rate__gte=50)),
    )
    rarity_total = (
        rarity['ultra_rare'] + rarity['very_rare'] + rarity['rare']
        + rarity['uncommon'] + rarity['common']
    )

    # --- Platform Breakdown ---
    platform_lists = (
        earned_qs
        .values_list('trophy__game__title_platform', flat=True)
    )
    platform_counts = defaultdict(int)
    for plist in platform_lists:
        if plist:
            for p in plist:
                platform_counts[p] += 1

    # Sort by count descending, cap at top 6 platforms
    sorted_platforms = sorted(platform_counts.items(), key=lambda x: -x[1])[:6]
    platform_max = sorted_platforms[0][1] if sorted_platforms else 1

    # --- Completion Tiers (filtered by activity in date range) ---
    pg_qs = ProfileGame.objects.filter(profile=profile)
    if date_range_key in RANGE_DAYS:
        # Only include games with trophy activity in the selected window
        pg_qs = pg_qs.filter(most_recent_trophy_date__gte=cutoff)
    completion = pg_qs.aggregate(
        tier_0_25=Count('id', filter=Q(progress__lt=25)),
        tier_25_50=Count('id', filter=Q(progress__gte=25, progress__lt=50)),
        tier_50_75=Count('id', filter=Q(progress__gte=50, progress__lt=75)),
        tier_75_99=Count('id', filter=Q(progress__gte=75, progress__lt=100)),
        tier_100=Count('id', filter=Q(progress=100)),
    )
    completion_total = sum(v for v in completion.values() if v)

    # --- Time of Day & Day of Week (timezone-adjusted) ---
    import pytz

    tz_name = getattr(profile.user, 'user_timezone', None) or 'UTC'
    try:
        user_tz = pytz.timezone(tz_name)
    except pytz.UnknownTimeZoneError:
        user_tz = pytz.UTC

    timestamps = list(
        earned_qs.values_list('earned_date_time', flat=True)
    )

    # Time of day buckets
    time_buckets = [
        {'label': 'Morning', 'range': '6am-12pm', 'count': 0, 'hex': '#f59e0b'},
        {'label': 'Afternoon', 'range': '12pm-6pm', 'count': 0, 'hex': '#f97316'},
        {'label': 'Evening', 'range': '6pm-12am', 'count': 0, 'hex': '#8b5cf6'},
        {'label': 'Night', 'range': '12am-6am', 'count': 0, 'hex': '#1e40af'},
    ]
    # Day of week counts (Mon=0 through Sun=6)
    day_names = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    day_counts = [0] * 7

    for ts in timestamps:
        local_dt = ts.astimezone(user_tz)
        hour = local_dt.hour
        if 6 <= hour < 12:
            time_buckets[0]['count'] += 1
        elif 12 <= hour < 18:
            time_buckets[1]['count'] += 1
        elif 18 <= hour < 24:
            time_buckets[2]['count'] += 1
        else:
            time_buckets[3]['count'] += 1
        day_counts[local_dt.weekday()] += 1

    time_max = max((b['count'] for b in time_buckets), default=1) or 1
    for b in time_buckets:
        b['pct'] = round(b['count'] / time_max * 100)

    day_max = max(day_counts) or 1
    # Rank days by count (1=highest) for color grading
    sorted_indices = sorted(range(7), key=lambda i: -day_counts[i])
    day_ranks = [0] * 7
    for rank, idx in enumerate(sorted_indices):
        day_ranks[idx] = rank

    # Colors: #1 gold, #2 silver, #3 bronze, rest primary
    rank_hex = ['#f59e0b', '#9ca3af', '#b45309', '#6366f1', '#6366f1', '#6366f1', '#6366f1']
    weekdays = [
        {
            'label': day_names[i],
            'count': day_counts[i],
            'pct': round(day_counts[i] / day_max * 100),
            'hex': rank_hex[day_ranks[i]] if day_counts[i] > 0 else '#6366f1',
        }
        for i in range(7)
    ]

    def _fmt(n):
        return f"{n:,}"

    return {
        'has_data': True,
        'range': date_range_key,
        'range_label': range_label,
        'total_earned': _fmt(total_earned),
        'velocity': {
            'avg_per_week': avg_per_week,
            'avg_per_month': avg_per_month,
            'days_since_last': days_since_last,
            'active_days': _fmt(active_days),
            'best_week': _fmt(best_week),
        },
        'rarity': {
            'avg_earn_rate': round(rarity['avg_earn_rate'] or 0, 1),
            'tiers': [
                {'label': 'Ultra Rare', 'desc': '<5% earn rate', 'count': _fmt(rarity['ultra_rare']), 'pct': round(rarity['ultra_rare'] / rarity_total * 100) if rarity_total else 0, 'hex': '#a855f7'},
                {'label': 'Very Rare', 'desc': '5-10% earn rate', 'count': _fmt(rarity['very_rare']), 'pct': round(rarity['very_rare'] / rarity_total * 100) if rarity_total else 0, 'hex': '#3b82f6'},
                {'label': 'Rare', 'desc': '10-20% earn rate', 'count': _fmt(rarity['rare']), 'pct': round(rarity['rare'] / rarity_total * 100) if rarity_total else 0, 'hex': '#06b6d4'},
                {'label': 'Uncommon', 'desc': '20-50% earn rate', 'count': _fmt(rarity['uncommon']), 'pct': round(rarity['uncommon'] / rarity_total * 100) if rarity_total else 0, 'hex': '#22c55e'},
                {'label': 'Common', 'desc': '50%+ earn rate', 'count': _fmt(rarity['common']), 'pct': round(rarity['common'] / rarity_total * 100) if rarity_total else 0, 'hex': '#6b7280'},
            ],
            'total': _fmt(rarity_total),
        },
        'platforms': [
            {'name': name, 'count': _fmt(count), 'pct': round(count / platform_max * 100)}
            for name, count in sorted_platforms
        ],
        'completion': {
            'tiers': [
                {'label': '0-24%', 'count': _fmt(completion['tier_0_25'] or 0), 'raw': completion['tier_0_25'] or 0, 'color': 'error'},
                {'label': '25-49%', 'count': _fmt(completion['tier_25_50'] or 0), 'raw': completion['tier_25_50'] or 0, 'color': 'warning'},
                {'label': '50-74%', 'count': _fmt(completion['tier_50_75'] or 0), 'raw': completion['tier_50_75'] or 0, 'color': 'info'},
                {'label': '75-99%', 'count': _fmt(completion['tier_75_99'] or 0), 'raw': completion['tier_75_99'] or 0, 'color': 'accent'},
                {'label': '100%', 'count': _fmt(completion['tier_100'] or 0), 'raw': completion['tier_100'] or 0, 'color': 'success'},
            ],
            'total': completion_total,
        },
        'time_of_day': [
            {**b, 'count_fmt': _fmt(b['count'])} for b in time_buckets
        ],
        'weekdays': [
            {**d, 'count_fmt': _fmt(d['count'])} for d in weekdays
        ],
    }


def provide_premium_settings(profile):
    """Current theme and background for inline management."""
    from trophies.themes import GRADIENT_THEMES, get_theme_style

    # Theme
    theme_key = profile.selected_theme or ''
    theme_data = GRADIENT_THEMES.get(theme_key) if theme_key else None
    theme_name = theme_data['name'] if theme_data else 'Default'
    theme_style = get_theme_style(theme_key) if theme_key else ''

    # Background
    bg_concept = profile.selected_background
    bg_info = None
    if bg_concept:
        bg_info = {
            'title': bg_concept.unified_title,
            'icon_url': bg_concept.concept_icon_url or '',
            'bg_url': bg_concept.bg_url or '',
        }

    return {
        'theme_key': theme_key,
        'theme_name': theme_name,
        'theme_style': theme_style,
        'background': bg_info,
    }


def _build_heatmap_data(profile, year):
    """GitHub-style contribution grid for all trophy earns over a year."""
    from trophies.models import EarnedTrophy
    from django.db.models import Count
    from django.db.models.functions import TruncDate
    from django.utils import timezone
    from datetime import date, timedelta
    import calendar as cal_module

    now = timezone.now()

    earned_qs = EarnedTrophy.objects.filter(
        profile=profile, earned=True, earned_date_time__isnull=False,
        earned_date_time__year=year,
    )

    # Daily trophy counts
    daily = dict(
        earned_qs
        .annotate(day=TruncDate('earned_date_time'))
        .values('day')
        .annotate(count=Count('id'))
        .values_list('day', 'count')
    )

    total_trophies = sum(daily.values())
    if not daily and year == now.year:
        # No data for current year, still show empty grid
        pass

    # Build grid as 7 rows (Sun-Sat), each row has one cell per week column
    jan1 = date(year, 1, 1)
    dec31 = date(year, 12, 31)
    # Pad to start on Sunday
    start = jan1 - timedelta(days=(jan1.weekday() + 1) % 7)

    # Compute dynamic intensity thresholds based on actual data
    nonzero_counts = sorted([c for c in daily.values() if c > 0])
    if nonzero_counts:
        q1 = nonzero_counts[len(nonzero_counts) // 4] if len(nonzero_counts) > 3 else 1
        q2 = nonzero_counts[len(nonzero_counts) // 2] if len(nonzero_counts) > 1 else q1
        q3 = nonzero_counts[3 * len(nonzero_counts) // 4] if len(nonzero_counts) > 3 else q2
    else:
        q1, q2, q3 = 1, 2, 4

    def _level(count):
        if count == 0:
            return 0
        if count <= q1:
            return 1
        if count <= q2:
            return 2
        if count <= q3:
            return 3
        return 4

    # Build flat list of all days, grouped into weeks
    all_cells = []
    current = start
    while current <= dec31 or current.weekday() != 6:
        if current.year == year:
            count = daily.get(current, 0)
            all_cells.append({
                'date': current.isoformat(),
                'count': count,
                'level': _level(count),
                'in_year': True,
            })
        else:
            all_cells.append({'in_year': False})
        current += timedelta(days=1)

    # Compute number of week columns
    num_weeks = len(all_cells) // 7

    # Transpose into 7 rows (day 0=Sun through day 6=Sat)
    rows = []
    day_labels = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
    for day_idx in range(7):
        row_cells = []
        for week_idx in range(num_weeks):
            cell_idx = week_idx * 7 + day_idx
            if cell_idx < len(all_cells):
                row_cells.append(all_cells[cell_idx])
            else:
                row_cells.append({'in_year': False})
        rows.append({
            'label': day_labels[day_idx],
            'show_label': day_idx in (1, 3, 5),  # Mon, Wed, Fri
            'cells': row_cells,
        })

    # Month label positions with span widths for simple template iteration
    month_positions = []
    for m in range(1, 13):
        first_of_month = date(year, m, 1)
        week_start = (first_of_month - start).days // 7
        if m < 12:
            next_month = date(year, m + 1, 1)
            week_end = (next_month - start).days // 7
        else:
            week_end = num_weeks
        month_positions.append({
            'name': cal_module.month_abbr[m],
            'span': max(week_end - week_start, 1),
        })

    return {
        'has_data': True,
        'rows': rows,
        'num_weeks': num_weeks,
        'months': month_positions,
        'year': year,
        'current_year': now.year,
        'total_trophies': total_trophies,
    }


def _build_genre_radar_data(profile, year):
    """Genre distribution across all earned trophies for Chart.js radar chart."""
    from trophies.models import EarnedTrophy

    # Count trophies per genre via game concept genres
    earned_qs = EarnedTrophy.objects.filter(
        profile=profile, earned=True, earned_date_time__isnull=False,
    ).select_related('trophy__game__concept')
    if year is not None:
        earned_qs = earned_qs.filter(earned_date_time__year=year)

    genre_counts = defaultdict(int)
    for et in earned_qs:
        game = et.trophy.game if et.trophy else None
        concept = getattr(game, 'concept', None) if game else None
        if concept and concept.genres:
            for genre in concept.genres:
                genre_counts[genre] += 1

    if not genre_counts:
        return {'has_data': False}

    # Sort by count descending, cap at top 8 for readability
    sorted_genres = sorted(genre_counts.items(), key=lambda x: -x[1])[:8]

    GENRE_ALIASES = {
        'role_playing_games': 'RPG',
        'role playing games': 'RPG',
    }

    def _format_genre(name):
        key = name.lower().strip()
        if key in GENRE_ALIASES:
            return GENRE_ALIASES[key]
        return name.replace('_', ' ').title()

    labels = [_format_genre(g[0]) for g in sorted_genres]
    counts = [g[1] for g in sorted_genres]
    total = sum(counts)

    return {
        'has_data': True,
        'labels': labels,
        'counts': counts,
        'total': total,
    }


def _build_rarity_radar_data(profile, year):
    """Rarity distribution across earned trophies for Chart.js radar chart."""
    from trophies.models import EarnedTrophy
    from django.db.models import Count, Q

    earned_qs = EarnedTrophy.objects.filter(
        profile=profile, earned=True, earned_date_time__isnull=False,
        trophy__trophy_earn_rate__gt=0,
    )
    if year is not None:
        earned_qs = earned_qs.filter(earned_date_time__year=year)

    rarity = earned_qs.aggregate(
        ultra_rare=Count('id', filter=Q(trophy__trophy_earn_rate__lt=5)),
        very_rare=Count('id', filter=Q(
            trophy__trophy_earn_rate__gte=5, trophy__trophy_earn_rate__lt=10,
        )),
        rare=Count('id', filter=Q(
            trophy__trophy_earn_rate__gte=10, trophy__trophy_earn_rate__lt=20,
        )),
        uncommon=Count('id', filter=Q(
            trophy__trophy_earn_rate__gte=20, trophy__trophy_earn_rate__lt=50,
        )),
        common=Count('id', filter=Q(trophy__trophy_earn_rate__gte=50)),
    )

    counts = [
        rarity['ultra_rare'], rarity['very_rare'], rarity['rare'],
        rarity['uncommon'], rarity['common'],
    ]
    total = sum(counts)
    if total == 0:
        return {'has_data': False}

    return {
        'has_data': True,
        'labels': ['Ultra Rare', 'Very Rare', 'Rare', 'Uncommon', 'Common'],
        'counts': counts,
        'total': total,
    }


def _build_platform_radar_data(profile, year):
    """Platform distribution across earned trophies for Chart.js radar chart."""
    from trophies.models import EarnedTrophy
    from trophies.util_modules.constants import ALL_PLATFORMS

    PLATFORM_LABELS = {
        'PS5': 'PS5', 'PS4': 'PS4', 'PS3': 'PS3',
        'PSVITA': 'PS Vita', 'PSVR': 'PS VR', 'PSVR2': 'PS VR2',
    }

    earned_qs = EarnedTrophy.objects.filter(
        profile=profile, earned=True, earned_date_time__isnull=False,
    )
    if year is not None:
        earned_qs = earned_qs.filter(earned_date_time__year=year)

    platform_lists = earned_qs.values_list('trophy__game__title_platform', flat=True)
    platform_counts = defaultdict(int)
    for plist in platform_lists:
        if plist:
            for p in plist:
                platform_counts[p] += 1

    # Always show all platforms, even with 0 count
    labels = [PLATFORM_LABELS.get(p, p) for p in ALL_PLATFORMS]
    counts = [platform_counts.get(p, 0) for p in ALL_PLATFORMS]
    total = sum(counts)

    if total == 0:
        return {'has_data': False}

    return {
        'has_data': True,
        'labels': labels,
        'counts': counts,
        'total': total,
    }


def _build_trophy_type_breakdown_data(profile, year):
    """Trophy type (bronze/silver/gold/plat) breakdown by month or year for stacked bar."""
    from trophies.models import EarnedTrophy
    from django.db.models import Count
    from django.utils import timezone
    import calendar as cal_module

    earned_qs = EarnedTrophy.objects.filter(
        profile=profile, earned=True, earned_date_time__isnull=False,
    )

    types = ['bronze', 'silver', 'gold', 'platinum']

    if year is not None:
        # Year mode: monthly breakdown
        from django.db.models.functions import ExtractMonth
        earned_qs = earned_qs.filter(earned_date_time__year=year)

        raw = list(
            earned_qs
            .values('trophy__trophy_type', month=ExtractMonth('earned_date_time'))
            .annotate(count=Count('id'))
            .order_by('month')
        )
        lookup = {(e['trophy__trophy_type'], e['month']): e['count'] for e in raw}

        n = _months_to_show(year)
        labels = [cal_module.month_abbr[m] for m in range(1, n + 1)]
        data = {t: [lookup.get((t, m), 0) for m in range(1, n + 1)] for t in types}
    else:
        # All mode: yearly breakdown
        from django.db.models.functions import ExtractYear
        from django.db.models import Min

        first = earned_qs.aggregate(first=Min('earned_date_time'))['first']
        if not first:
            return {'has_data': False}

        now = timezone.now()
        years = list(range(first.year, now.year + 1))
        labels = [str(y) for y in years]

        raw = list(
            earned_qs
            .values('trophy__trophy_type', yr=ExtractYear('earned_date_time'))
            .annotate(count=Count('id'))
            .order_by('yr')
        )
        lookup = {(e['trophy__trophy_type'], e['yr']): e['count'] for e in raw}
        data = {t: [lookup.get((t, y), 0) for y in years] for t in types}

    total = sum(sum(v) for v in data.values())
    if total == 0:
        return {'has_data': False}

    totals = {t: sum(data[t]) for t in types}

    return {
        'has_data': True,
        'labels': labels,
        'bronze': data['bronze'],
        'silver': data['silver'],
        'gold': data['gold'],
        'platinum': data['platinum'],
        'totals': totals,
    }


def _year_start_dt(year):
    """Timezone-aware datetime for midnight Jan 1 of the given year."""
    import datetime as dt
    return dt.datetime(year, 1, 1, tzinfo=dt.timezone.utc)


def _months_to_show(year):
    """How many months of data to include: current month for current year, 12 for past years."""
    from django.utils import timezone
    now = timezone.now()
    if year >= now.year:
        return now.month
    return 12


def _build_year_review_data(profile, current_year):
    """Year-over-year trophy earning comparison for Chart.js line chart."""
    from trophies.models import EarnedTrophy
    from django.db.models import Count
    from django.db.models.functions import TruncMonth
    import calendar as cal_module

    prev_year = current_year - 1

    earned_qs = EarnedTrophy.objects.filter(
        profile=profile, earned=True, earned_date_time__isnull=False,
    )

    labels = [cal_module.month_abbr[m] for m in range(1, 13)]

    def _monthly_cumulative(yr):
        monthly_raw = list(
            earned_qs.filter(earned_date_time__year=yr)
            .annotate(month=TruncMonth('earned_date_time'))
            .values('month')
            .annotate(count=Count('id'))
            .order_by('month')
        )
        month_counts = [0] * 12
        for entry in monthly_raw:
            m = entry['month'].month
            month_counts[m - 1] = entry['count']

        # Cumulative
        cumulative = []
        running = 0
        for c in month_counts:
            running += c
            cumulative.append(running)
        return cumulative, running

    current_data, current_total = _monthly_cumulative(current_year)
    prev_data, prev_total = _monthly_cumulative(prev_year)

    if current_total == 0 and prev_total == 0:
        return {'has_data': False}

    pct_change = None
    if prev_total > 0:
        pct_change = round((current_total - prev_total) / prev_total * 100, 1)

    n = _months_to_show(current_year)

    return {
        'has_data': True,
        'labels': labels[:n],
        'current_year': current_year,
        'prev_year': prev_year,
        'current_data': current_data[:n],
        'prev_data': prev_data[:n],
        'current_total': f"{current_total:,}",
        'prev_total': f"{prev_total:,}",
        'pct_change': pct_change,
    }


def _build_games_started_completed_data(profile, year):
    """Monthly games started vs completed for Chart.js line chart."""
    from trophies.models import ProfileGame
    from django.db.models import Count
    from django.db.models.functions import TruncMonth
    import calendar as cal_module

    labels = [cal_module.month_abbr[m] for m in range(1, 13)]

    # Games started (first trophy in that game during this year)
    started_raw = list(
        ProfileGame.objects
        .filter(profile=profile, first_played_date_time__isnull=False,
                first_played_date_time__year=year)
        .annotate(month=TruncMonth('first_played_date_time'))
        .values('month')
        .annotate(count=Count('id'))
        .order_by('month')
    )
    started = [0] * 12
    for entry in started_raw:
        started[entry['month'].month - 1] = entry['count']

    # Games completed (reached 100% during this year)
    completed_raw = list(
        ProfileGame.objects
        .filter(profile=profile, progress=100,
                most_recent_trophy_date__isnull=False,
                most_recent_trophy_date__year=year)
        .annotate(month=TruncMonth('most_recent_trophy_date'))
        .values('month')
        .annotate(count=Count('id'))
        .order_by('month')
    )
    completed = [0] * 12
    for entry in completed_raw:
        completed[entry['month'].month - 1] = entry['count']

    if not any(started) and not any(completed):
        return {'has_data': False}

    # Convert to cumulative
    cum_started = []
    cum_completed = []
    running_s = 0
    running_c = 0
    for i in range(12):
        running_s += started[i]
        running_c += completed[i]
        cum_started.append(running_s)
        cum_completed.append(running_c)

    n = _months_to_show(year)

    return {
        'has_data': True,
        'labels': labels[:n],
        'started': cum_started[:n],
        'completed': cum_completed[:n],
        'total_started': running_s,
        'total_completed': running_c,
    }


def _build_trophy_pool_history(profile):
    """Reconstruct when each game's trophies entered the user's pool.

    Uses the earliest earned_date_time per game (via EarnedTrophy) as the
    "game start" date. At that point, the game's total trophies enter the pool.

    Returns dict: {date: total_trophies_entering_pool_that_day}
    """
    from trophies.models import EarnedTrophy, ProfileGame
    from django.db.models import Min

    # Get first earn date per game from EarnedTrophy side
    first_earns = dict(
        EarnedTrophy.objects
        .filter(profile=profile, earned=True, earned_date_time__isnull=False)
        .values('trophy__game_id')
        .annotate(first_earn=Min('earned_date_time'))
        .values_list('trophy__game_id', 'first_earn')
    )

    # Get total trophy counts per game from ProfileGame
    game_totals = dict(
        ProfileGame.objects
        .filter(profile=profile, game_id__in=first_earns.keys())
        .values_list('game_id', 'earned_trophies_count')
    )
    game_unearned = dict(
        ProfileGame.objects
        .filter(profile=profile, game_id__in=first_earns.keys())
        .values_list('game_id', 'unearned_trophies_count')
    )

    # Build: {date: total_trophies_entering_pool}
    game_starts = defaultdict(int)
    for game_id, first_dt in first_earns.items():
        total = (game_totals.get(game_id, 0) or 0) + (game_unearned.get(game_id, 0) or 0)
        game_starts[first_dt.date()] += total

    return game_starts


def _build_earned_unearned_data(profile, year):
    """Cumulative earned vs unearned over the year using first-earn reconstruction."""
    from trophies.models import EarnedTrophy, ProfileGame
    from django.db.models import Count, Avg
    from django.db.models.functions import TruncMonth
    from datetime import date
    import calendar as cal_module

    labels = [cal_module.month_abbr[m] for m in range(1, 13)]
    game_starts = _build_trophy_pool_history(profile)

    # Pool entering before this year
    pool_baseline = sum(v for d, v in game_starts.items() if d < date(year, 1, 1))

    # Earned baseline
    earned_baseline = EarnedTrophy.objects.filter(
        profile=profile, earned=True, earned_date_time__isnull=False,
        earned_date_time__lt=_year_start_dt(year),
    ).count()

    unearned_baseline = max(pool_baseline - earned_baseline, 0)

    # Pool entering per month this year
    monthly_pool = [0] * 12
    for d, total in game_starts.items():
        if d.year == year:
            monthly_pool[d.month - 1] += total

    # Earned per month this year
    earned_raw = list(
        EarnedTrophy.objects
        .filter(profile=profile, earned=True, earned_date_time__isnull=False,
                earned_date_time__year=year)
        .annotate(month=TruncMonth('earned_date_time'))
        .values('month')
        .annotate(count=Count('id'))
        .order_by('month')
    )
    monthly_earned = [0] * 12
    for entry in earned_raw:
        monthly_earned[entry['month'].month - 1] = entry['count']

    year_earned = sum(monthly_earned)

    if earned_baseline == 0 and year_earned == 0:
        return {'has_data': False}

    # Build cumulative lines
    cumulative_earned = []
    cumulative_unearned = []
    running_earned = earned_baseline
    running_unearned = unearned_baseline
    for i in range(12):
        running_unearned += monthly_pool[i]  # New games add to unearned
        running_earned += monthly_earned[i]   # Earning moves from unearned
        running_unearned -= monthly_earned[i]
        cumulative_earned.append(running_earned)
        cumulative_unearned.append(max(running_unearned, 0))

    avg_completion = (
        ProfileGame.objects.filter(profile=profile).aggregate(avg=Avg('progress'))
    )['avg'] or 0

    n = _months_to_show(year)

    return {
        'has_data': True,
        'labels': labels[:n],
        'cumulative_earned': cumulative_earned[:n],
        'cumulative_unearned': cumulative_unearned[:n],
        'year_earned': f"{year_earned:,}",
        'current_total': f"{cumulative_earned[n - 1]:,}" if cumulative_earned else '0',
        'current_unearned': f"{cumulative_unearned[n - 1]:,}" if cumulative_unearned else '0',
        'avg_completion': round(avg_completion, 1),
    }


def _build_quarterly_labels(start_year, end_year):
    """Build quarterly labels like ['Q1 2020', 'Q2 2020', ...]."""
    labels = []
    for y in range(start_year, end_year + 1):
        for q in range(1, 5):
            labels.append(f"Q{q} {y}")
    return labels


def _build_all_time_games_flow(profile):
    """All-time games started vs completed by quarter."""
    from trophies.models import ProfileGame, EarnedTrophy
    from django.db.models import Count, Min
    from django.db.models.functions import TruncQuarter
    from django.utils import timezone

    now = timezone.now()

    # Find earliest year
    first = EarnedTrophy.objects.filter(
        profile=profile, earned=True, earned_date_time__isnull=False
    ).aggregate(first=Min('earned_date_time'))['first']
    if not first:
        return {'has_data': False}
    start_year = first.year

    labels = _build_quarterly_labels(start_year, now.year)
    num_q = len(labels)

    def _quarter_idx(dt):
        return (dt.year - start_year) * 4 + (dt.month - 1) // 3

    # Games started by quarter
    started_raw = list(
        ProfileGame.objects
        .filter(profile=profile, first_played_date_time__isnull=False)
        .annotate(quarter=TruncQuarter('first_played_date_time'))
        .values('quarter')
        .annotate(count=Count('id'))
        .order_by('quarter')
    )
    started = [0] * num_q
    for entry in started_raw:
        idx = _quarter_idx(entry['quarter'])
        if 0 <= idx < num_q:
            started[idx] = entry['count']

    # Games completed by quarter
    completed_raw = list(
        ProfileGame.objects
        .filter(profile=profile, progress=100,
                most_recent_trophy_date__isnull=False)
        .annotate(quarter=TruncQuarter('most_recent_trophy_date'))
        .values('quarter')
        .annotate(count=Count('id'))
        .order_by('quarter')
    )
    completed = [0] * num_q
    for entry in completed_raw:
        idx = _quarter_idx(entry['quarter'])
        if 0 <= idx < num_q:
            completed[idx] = entry['count']

    # Cumulative
    cum_s, cum_c = [], []
    rs, rc = 0, 0
    for i in range(num_q):
        rs += started[i]
        rc += completed[i]
        cum_s.append(rs)
        cum_c.append(rc)

    # Truncate to current quarter
    current_q_idx = _quarter_idx(now)
    n = min(current_q_idx + 1, num_q)

    return {
        'has_data': True,
        'labels': labels[:n],
        'started': cum_s[:n],
        'completed': cum_c[:n],
        'total_started': rs,
        'total_completed': rc,
    }


def _build_all_time_earned_unearned(profile):
    """All-time earned vs unearned trophies by quarter using first-earn reconstruction."""
    from trophies.models import EarnedTrophy, ProfileGame
    from django.db.models import Count, Min, Avg
    from django.db.models.functions import TruncQuarter
    from django.utils import timezone

    now = timezone.now()
    game_starts = _build_trophy_pool_history(profile)

    first = EarnedTrophy.objects.filter(
        profile=profile, earned=True, earned_date_time__isnull=False
    ).aggregate(first=Min('earned_date_time'))['first']
    if not first:
        return {'has_data': False}
    start_year = first.year

    labels = _build_quarterly_labels(start_year, now.year)
    num_q = len(labels)

    def _quarter_idx(dt):
        return (dt.year - start_year) * 4 + (dt.month - 1) // 3

    # Pool entering per quarter (from game starts)
    quarterly_pool = [0] * num_q
    for d, total in game_starts.items():
        idx = _quarter_idx(d)
        if 0 <= idx < num_q:
            quarterly_pool[idx] += total

    # Earned per quarter
    earned_raw = list(
        EarnedTrophy.objects
        .filter(profile=profile, earned=True, earned_date_time__isnull=False)
        .annotate(quarter=TruncQuarter('earned_date_time'))
        .values('quarter')
        .annotate(count=Count('id'))
        .order_by('quarter')
    )
    quarterly_earned = [0] * num_q
    for entry in earned_raw:
        idx = _quarter_idx(entry['quarter'])
        if 0 <= idx < num_q:
            quarterly_earned[idx] = entry['count']

    # Build cumulative lines
    cum_earned, cum_unearned = [], []
    re, ru = 0, 0
    for i in range(num_q):
        ru += quarterly_pool[i]    # New games add to unearned
        re += quarterly_earned[i]  # Earning moves from unearned
        ru -= quarterly_earned[i]
        cum_earned.append(re)
        cum_unearned.append(max(ru, 0))

    # Truncate to current quarter
    current_q_idx = _quarter_idx(now)
    n = min(current_q_idx + 1, num_q)

    avg_completion = (
        ProfileGame.objects.filter(profile=profile).aggregate(avg=Avg('progress'))
    )['avg'] or 0

    total_earned_now = profile.total_trophies

    return {
        'has_data': True,
        'labels': labels[:n],
        'cumulative_earned': cum_earned[:n],
        'cumulative_unearned': cum_unearned[:n],
        'year_earned': f"{total_earned_now:,}",
        'current_total': f"{cum_earned[n - 1]:,}" if cum_earned else '0',
        'current_unearned': f"{cum_unearned[n - 1]:,}" if cum_unearned else '0',
        'avg_completion': round(avg_completion, 1),
    }


def _build_all_time_yearly_totals(profile):
    """Trophies earned per year with quarterly breakdown for stacked bar chart."""
    from trophies.models import EarnedTrophy
    from django.db.models import Count, Min
    from django.db.models.functions import ExtractYear, ExtractQuarter
    from django.utils import timezone

    now = timezone.now()

    first = EarnedTrophy.objects.filter(
        profile=profile, earned=True, earned_date_time__isnull=False
    ).aggregate(first=Min('earned_date_time'))['first']
    if not first:
        return {'has_data': False}

    start_year = first.year
    years = list(range(start_year, now.year + 1))
    labels = [str(y) for y in years]

    # Get counts by year + quarter in one query
    raw = list(
        EarnedTrophy.objects
        .filter(profile=profile, earned=True, earned_date_time__isnull=False)
        .annotate(yr=ExtractYear('earned_date_time'), qtr=ExtractQuarter('earned_date_time'))
        .values('yr', 'qtr')
        .annotate(count=Count('id'))
        .order_by('yr', 'qtr')
    )

    # Build lookup: {(year, quarter): count}
    lookup = {(e['yr'], e['qtr']): e['count'] for e in raw}

    q1 = [lookup.get((y, 1), 0) for y in years]
    q2 = [lookup.get((y, 2), 0) for y in years]
    q3 = [lookup.get((y, 3), 0) for y in years]
    q4 = [lookup.get((y, 4), 0) for y in years]

    totals = [q1[i] + q2[i] + q3[i] + q4[i] for i in range(len(years))]
    best_idx = max(range(len(totals)), key=lambda i: totals[i]) if totals else 0
    total = sum(totals)

    return {
        'has_data': True,
        'labels': labels,
        'q1': q1,
        'q2': q2,
        'q3': q3,
        'q4': q4,
        'best_year': labels[best_idx] if labels else '',
        'best_count': f"{totals[best_idx]:,}" if totals else '0',
        'total': f"{total:,}",
    }


def provide_trophy_visualizations(profile, settings=None):
    """Combined premium visualization module: heatmap, genre radar, year review, games flow, earned/unearned."""
    from django.utils import timezone

    settings = settings or {}
    now = timezone.now()
    year_val = settings.get('year', '')
    is_all = year_val == 'all'

    try:
        year = int(year_val) if year_val and not is_all else now.year
    except (ValueError, TypeError):
        year = now.year

    if is_all:
        return {
            'year': 'all',
            'year_label': 'All Time',
            'current_year': now.year,
            'heatmap': _build_heatmap_data(profile, now.year),
            'genre_radar': _build_genre_radar_data(profile, None),
            'rarity_radar': _build_rarity_radar_data(profile, None),
            'platform_radar': _build_platform_radar_data(profile, None),
            'year_review': _build_all_time_yearly_totals(profile),
            'type_breakdown': _build_trophy_type_breakdown_data(profile, None),
            'games_flow': _build_all_time_games_flow(profile),
            'earned_unearned': _build_all_time_earned_unearned(profile),
        }

    return {
        'year': year,
        'year_label': str(year),
        'current_year': now.year,
        'heatmap': _build_heatmap_data(profile, year),
        'genre_radar': _build_genre_radar_data(profile, year),
        'rarity_radar': _build_rarity_radar_data(profile, year),
        'platform_radar': _build_platform_radar_data(profile, year),
        'year_review': _build_year_review_data(profile, year),
        'type_breakdown': _build_trophy_type_breakdown_data(profile, year),
        'games_flow': _build_games_started_completed_data(profile, year),
        'earned_unearned': _build_earned_unearned_data(profile, year),
    }


# ---------------------------------------------------------------------------
# Premium badge analytics providers
# ---------------------------------------------------------------------------


def provide_advanced_badge_stats(profile, settings=None):
    """Deep badge analytics: velocity, series completion, XP breakdown, types, tiers, rarest."""
    from trophies.models import UserBadge, ProfileGamification, StageCompletionEvent
    from django.db.models import Min, Max, Count, Q
    from django.utils import timezone
    from datetime import timedelta

    settings = settings or {}
    date_range_key = settings.get('range', 'all')

    RANGE_DAYS = {'7d': 7, '30d': 30, '90d': 90, '1y': 365}
    RANGE_LABELS = {'7d': 'Last 7 Days', '30d': 'Last 30 Days', '90d': 'Last 90 Days', '1y': 'Last Year', 'all': 'All Time'}
    range_label = RANGE_LABELS.get(date_range_key, 'All Time')

    now = timezone.now()
    base_qs = UserBadge.objects.filter(profile=profile)

    if date_range_key in RANGE_DAYS:
        cutoff = now - timedelta(days=RANGE_DAYS[date_range_key])
        badge_qs = base_qs.filter(earned_at__gte=cutoff)
    else:
        badge_qs = base_qs

    total_badges = badge_qs.count()
    if total_badges == 0:
        return {'has_data': False, 'range': date_range_key, 'range_label': range_label}

    def _fmt(n):
        return f"{n:,}"

    # --- Badge Velocity ---
    date_agg = badge_qs.aggregate(first=Min('earned_at'), last=Max('earned_at'))
    first_date = date_agg['first']
    last_date = date_agg['last']

    if first_date and last_date:
        months_span = max(
            ((last_date.year - first_date.year) * 12 + last_date.month - first_date.month),
            1,
        )
        avg_per_month = round(total_badges / months_span, 1)
    else:
        avg_per_month = 0

    days_since_last = (now - last_date).days if last_date else None

    # XP earned in range: completion bonuses + stage XP
    from trophies.util_modules.constants import BADGE_TIER_XP
    from trophies.services.xp_service import TIER_XP_MAP
    completion_xp_in_range = total_badges * BADGE_TIER_XP

    # Stage XP in range (tier-specific XP per stage completed)
    stage_event_qs = StageCompletionEvent.objects.filter(profile=profile)
    stage_xp_qs = stage_event_qs
    if date_range_key in RANGE_DAYS:
        stage_xp_qs = stage_xp_qs.filter(completed_at__gte=cutoff)
    stage_xp_in_range = 0
    for tier, count in stage_xp_qs.values('badge__tier').annotate(count=Count('id')).values_list('badge__tier', 'count'):
        stage_xp_in_range += count * TIER_XP_MAP.get(tier, 0)

    xp_in_range = completion_xp_in_range + stage_xp_in_range
    xp_per_month = round(xp_in_range / months_span) if first_date and last_date else 0

    # --- Series Completion Depth ---
    series_tiers = (
        badge_qs
        .filter(badge__series_slug__isnull=False)
        .values('badge__series_slug')
        .annotate(tier_count=Count('badge__tier', distinct=True))
    )
    depth_counts = {1: 0, 2: 0, 3: 0, 4: 0}
    for entry in series_tiers:
        tc = min(entry['tier_count'], 4)
        depth_counts[tc] += 1

    # --- XP Breakdown (always all-time, from pre-computed JSON) ---
    try:
        gamification = profile.gamification
        series_xp = gamification.series_badge_xp or {}
    except ProfileGamification.DoesNotExist:
        series_xp = {}

    sorted_series = sorted(series_xp.items(), key=lambda x: -x[1])[:8]
    xp_max = sorted_series[0][1] if sorted_series else 1

    def _format_series(slug):
        return slug.replace('-', ' ').title() if slug else 'Unknown'

    xp_breakdown = [
        {'name': _format_series(name), 'xp': _fmt(xp), 'pct': round(xp / xp_max * 100)}
        for name, xp in sorted_series
    ]

    # --- Badge Type Distribution (badges + stages per type) ---
    type_counts = dict(
        badge_qs
        .values('badge__badge_type')
        .annotate(count=Count('id'))
        .values_list('badge__badge_type', 'count')
    )
    type_stage_counts = dict(
        StageCompletionEvent.objects
        .filter(profile=profile)
        .values('badge__badge_type')
        .annotate(count=Count('id'))
        .values_list('badge__badge_type', 'count')
    )
    TYPE_LABELS = {
        'series': 'Series', 'collection': 'Collection', 'megamix': 'Megamix',
        'developer': 'Developer', 'user': 'User', 'genre': 'Genre', 'misc': 'Misc',
    }
    type_max = max(type_counts.values()) if type_counts else 1
    stage_max = max(type_stage_counts.values()) if type_stage_counts else 1
    badge_types = [
        {'label': TYPE_LABELS.get(t, t.title()), 'count': _fmt(type_counts.get(t, 0)),
         'stages': _fmt(type_stage_counts.get(t, 0)),
         'pct': round(type_counts.get(t, 0) / type_max * 100) if type_max else 0,
         'stage_pct': round(type_stage_counts.get(t, 0) / stage_max * 100) if stage_max else 0}
        for t in ['series', 'collection', 'megamix', 'developer', 'user', 'genre', 'misc']
        if type_counts.get(t, 0) > 0 or type_stage_counts.get(t, 0) > 0
    ]

    # --- Tier Distribution ---
    tier_counts = dict(
        badge_qs
        .values('badge__tier')
        .annotate(count=Count('id'))
        .values_list('badge__tier', 'count')
    )
    TIER_NAMES = {1: 'Bronze', 2: 'Silver', 3: 'Gold', 4: 'Platinum'}
    TIER_HEXES = {1: '#b45309', 2: '#9ca3af', 3: '#f59e0b', 4: None}
    tier_total = sum(tier_counts.values()) or 1
    tiers = [
        {
            'label': TIER_NAMES[t], 'count': _fmt(tier_counts.get(t, 0)),
            'pct': round(tier_counts.get(t, 0) / tier_total * 100),
            'hex': TIER_HEXES[t],
        }
        for t in [1, 2, 3, 4]
    ]

    # --- Rarest Badges ---
    rarest_badges = list(
        badge_qs
        .select_related('badge')
        .order_by('badge__earned_count')[:3]
    )
    rarest = []
    for ub in rarest_badges:
        b = ub.badge
        rarest.append({
            'name': b.effective_display_series or b.name,
            'tier': b.tier,
            'tier_name': TIER_NAMES.get(b.tier, ''),
            'earned_count': _fmt(b.earned_count),
            'series_slug': b.series_slug or '',
        })

    # --- Stage Progress (uses StageCompletionEvent, respects date range) ---
    from trophies.models import UserBadgeProgress, Badge, StageCompletionEvent

    # All-time stage totals (for overall progress stats)
    all_progress = (
        UserBadgeProgress.objects
        .filter(profile=profile, badge__is_live=True, badge__required_stages__gt=0)
        .select_related('badge')
    )
    total_stages_completed_all = 0
    total_stages_required = 0
    for ubp in all_progress:
        total_stages_completed_all += ubp.completed_concepts
        total_stages_required += ubp.badge.required_stages

    total_live_series = Badge.objects.filter(is_live=True, tier=1).values('series_slug').distinct().count()
    avg_stage_pct = round(total_stages_completed_all / total_stages_required * 100, 1) if total_stages_required else 0

    # Stage completions in selected date range (from StageCompletionEvent)
    stage_event_qs = StageCompletionEvent.objects.filter(profile=profile)
    if date_range_key in RANGE_DAYS:
        stages_in_range = stage_event_qs.filter(completed_at__gte=cutoff).count()
    else:
        stages_in_range = stage_event_qs.count()

    stages_per_month = round(stages_in_range / months_span, 1) if first_date and last_date else 0

    # Most active series in range
    most_active_series = (
        stage_event_qs
        .filter(completed_at__gte=cutoff) if date_range_key in RANGE_DAYS else stage_event_qs
    ).values('badge__series_slug', 'badge__name').annotate(
        count=Count('id')
    ).order_by('-count').first()

    most_progressed = None
    max_completed = 0
    if most_active_series:
        most_progressed = most_active_series['badge__name']
        max_completed = most_active_series['count']

    # Days since last stage completion
    last_stage = stage_event_qs.order_by('-completed_at').first()
    days_since_last_stage = (now - last_stage.completed_at).days if last_stage else None

    # --- XP Efficiency (always all-time) ---
    total_all_badges = base_qs.count()
    total_xp = gamification.total_badge_xp if series_xp else 0
    completion_xp_total = total_all_badges * BADGE_TIER_XP
    stage_xp_total = max(total_xp - completion_xp_total, 0)
    xp_per_badge = round(total_xp / total_all_badges) if total_all_badges else 0
    xp_per_stage = round(stage_xp_total / total_stages_completed_all) if total_stages_completed_all else 0
    completion_pct = round(completion_xp_total / total_xp * 100) if total_xp else 0
    stage_pct = 100 - completion_pct

    # Stages per badge (all-time)
    stages_per_badge = round(total_stages_completed_all / total_all_badges, 1) if total_all_badges else 0

    return {
        'has_data': True,
        'range': date_range_key,
        'range_label': range_label,
        'total_badges': _fmt(total_badges),
        'velocity': {
            'avg_per_month': avg_per_month,
            'days_since_last': days_since_last,
            'xp_in_range': _fmt(xp_in_range),
            'xp_per_month': _fmt(xp_per_month),
        },
        'series_depth': depth_counts,
        'xp_breakdown': xp_breakdown,
        'badge_types': badge_types,
        'tiers': tiers,
        'rarest': rarest,
        'stage_progress': {
            'completed': _fmt(total_stages_completed_all),
            'required': _fmt(total_stages_required),
            'avg_pct': avg_stage_pct,
            'total_series': total_live_series,
        },
        'xp_efficiency': {
            'total_xp': _fmt(total_xp),
            'stage_xp': _fmt(stage_xp_total),
            'completion_xp': _fmt(completion_xp_total),
            'stage_pct': stage_pct,
            'completion_pct': completion_pct,
            'xp_per_badge': _fmt(xp_per_badge),
            'xp_per_stage': _fmt(xp_per_stage),
        },
        'stage_velocity': {
            'stages_in_range': _fmt(stages_in_range),
            'stages_per_month': stages_per_month,
            'stages_per_badge': stages_per_badge,
            'days_since_last_stage': days_since_last_stage,
            'most_progressed': most_progressed or 'N/A',
            'most_progressed_count': _fmt(max_completed),
        },
    }


def _build_stage_progress_data(profile):
    """Series stage completion: bar chart (current grind) + tier grid (big picture)."""
    from trophies.models import UserBadgeProgress, Badge, UserBadge, StageCompletionEvent

    TIER_NAMES = {1: 'Bronze', 2: 'Silver', 3: 'Gold', 4: 'Platinum'}
    TIER_COLORS = {1: '#92400e', 2: '#9ca3af', 3: '#facc15', 4: None}

    # Get all badge progress
    progress_qs = (
        UserBadgeProgress.objects
        .filter(profile=profile, badge__is_live=True, badge__required_stages__gt=0)
        .select_related('badge')
        .order_by('-completed_concepts')
    )

    # Get earned badges
    earned_badges = list(
        UserBadge.objects
        .filter(profile=profile, badge__series_slug__isnull=False)
        .select_related('badge')
    )
    earned_ids = {ub.badge_id for ub in earned_badges}

    # Build per-series earned tiers
    series_tiers = defaultdict(set)
    series_names = {}
    for ub in earned_badges:
        slug = ub.badge.series_slug
        series_tiers[slug].add(ub.badge.tier)
        if slug not in series_names:
            series_names[slug] = ub.badge.effective_display_series or ub.badge.name

    # --- Bar chart: current grind (next unearned tier per series) ---
    # Collect all unearned badge progress, then pick the lowest tier per series
    all_unearned = []
    for ubp in progress_qs:
        badge = ubp.badge
        slug = badge.series_slug
        if not slug or badge.id in earned_ids:
            continue
        if slug not in series_names:
            series_names[slug] = badge.effective_display_series or badge.name
        all_unearned.append((slug, badge, ubp))

    # Group by series, pick the lowest tier (the next one to earn)
    bar_series = {}
    for slug, badge, ubp in sorted(all_unearned, key=lambda x: x[1].tier):
        if slug in bar_series:
            continue
        pct = round(ubp.completed_concepts / badge.required_stages * 100, 1) if badge.required_stages else 0
        bar_series[slug] = {
            'name': series_names[slug],
            'tier': badge.tier,
            'tier_name': TIER_NAMES.get(badge.tier, ''),
            'tier_color': TIER_COLORS.get(badge.tier),
            'completed': ubp.completed_concepts,
            'required': badge.required_stages,
            'pct': min(pct, 100),
        }

    sorted_bars = sorted(bar_series.values(), key=lambda x: -x['pct'])[:8]

    # Build bar chart datasets: one "completed" colored by tier + one "remaining"
    bar_labels = [s['name'] for s in sorted_bars]
    bar_completed = [s['completed'] for s in sorted_bars]
    bar_remaining = [s['required'] - s['completed'] for s in sorted_bars]
    bar_colors = [s['tier_color'] or '#6366f1' for s in sorted_bars]
    bar_tier_labels = [s['tier_name'] for s in sorted_bars]

    # --- Tier grid: full picture for top series ---
    # Combine earned + in-progress series, sort by most tiers earned
    all_slugs = set(series_tiers.keys()) | set(bar_series.keys())
    grid_rows = []
    for slug in all_slugs:
        earned = series_tiers.get(slug, set())
        in_prog = bar_series.get(slug)
        current_pct = in_prog['pct'] if in_prog else (100.0 if len(earned) == 4 else 0)
        current_tier = in_prog['tier'] if in_prog else (max(earned) + 1 if earned else 1)
        grid_rows.append({
            'name': series_names.get(slug, slug),
            'series_slug': slug,
            'tiers': [t in earned for t in [1, 2, 3, 4]],
            'earned_count': len(earned),
            'current_tier': min(current_tier, 4),
            'current_pct': current_pct,
        })

    grid_rows.sort(key=lambda x: (-x['earned_count'], -x['current_pct']))
    grid_rows = grid_rows[:10]

    # Summary stats
    tiers_earned = len(earned_ids)
    series_completed = sum(1 for tiers in series_tiers.values() if 1 in tiers)
    series_platinumed = sum(1 for tiers in series_tiers.values() if len(tiers) == 4)
    total_stages = StageCompletionEvent.objects.filter(profile=profile).count()

    has_data = bool(sorted_bars) or bool(grid_rows)

    return {
        'has_data': has_data,
        # Bar chart data
        'bar_labels': bar_labels,
        'bar_completed': bar_completed,
        'bar_remaining': bar_remaining,
        'bar_colors': bar_colors,
        'bar_tier_labels': bar_tier_labels,
        'has_bars': bool(sorted_bars),
        'bar_height': max(len(sorted_bars) * 35, 120),
        # Tier grid data
        'grid_rows': grid_rows,
        'has_grid': bool(grid_rows),
        # Summary
        'series_completed': series_completed,
        'series_platinumed': series_platinumed,
        'tiers_earned': tiers_earned,
        'total_stages': f"{total_stages:,}",
    }


def _build_series_xp_radar_data(profile):
    """Top series by XP for radar chart (always all-time)."""
    from trophies.models import ProfileGamification

    try:
        gamification = profile.gamification
        series_xp = gamification.series_badge_xp or {}
    except ProfileGamification.DoesNotExist:
        return {'has_data': False}

    if not series_xp:
        return {'has_data': False}

    sorted_series = sorted(series_xp.items(), key=lambda x: -x[1])[:8]

    def _format_slug(slug):
        return slug.replace('-', ' ').title() if slug else 'Unknown'

    labels = [_format_slug(s[0]) for s in sorted_series]
    counts = [s[1] for s in sorted_series]
    total = sum(counts)
    max_xp = counts[0] if counts else 1

    ranked_list = [
        {'name': labels[i], 'xp': f"{counts[i]:,}", 'pct': round(counts[i] / max_xp * 100)}
        for i in range(len(labels))
    ]

    return {
        'has_data': True,
        'labels': labels,
        'counts': counts,
        'total': total,
        'total_formatted': f"{total:,}",
        'ranked_list': ranked_list,
    }


def _build_badge_type_radar_data(profile):
    """Stage completion count per badge type for Chart.js radar chart. Excludes types with 0 stages."""
    from trophies.models import StageCompletionEvent
    from trophies.constants import BADGE_TYPES
    from django.db.models import Count

    TYPE_LABELS = {
        'series': 'Series', 'collection': 'Collection', 'megamix': 'Megamix',
        'developer': 'Developer', 'misc': 'Misc', 'user': 'User', 'genre': 'Genre',
    }

    type_counts = dict(
        StageCompletionEvent.objects.filter(profile=profile)
        .values_list('badge__badge_type')
        .annotate(count=Count('id'))
        .values_list('badge__badge_type', 'count')
    )

    # Only include types with stages > 0
    active_types = [t for t in BADGE_TYPES if type_counts.get(t, 0) > 0]
    if not active_types:
        return {'has_data': False}

    labels = [TYPE_LABELS.get(t, t.title()) for t in active_types]
    counts = [type_counts[t] for t in active_types]
    total = sum(counts)

    return {
        'has_data': True,
        'labels': labels,
        'counts': counts,
        'total': total,
    }


def _build_series_stages_radar_data(profile):
    """Top series by stage completion count for Chart.js radar chart."""
    from trophies.models import StageCompletionEvent
    from django.db.models import Count

    raw = list(
        StageCompletionEvent.objects.filter(profile=profile)
        .values('badge__series_slug')
        .annotate(count=Count('id'))
        .order_by('-count')[:8]
    )

    if not raw:
        return {'has_data': False}

    def _format_slug(slug):
        return slug.replace('-', ' ').title() if slug else 'Unknown'

    labels = [_format_slug(r['badge__series_slug']) for r in raw]
    counts = [r['count'] for r in raw]
    total = sum(counts)

    return {
        'has_data': True,
        'labels': labels,
        'counts': counts,
        'total': total,
    }


def _build_stage_type_breakdown_data(profile):
    """Stages completed per badge type for Chart.js horizontal bar chart."""
    from trophies.models import StageCompletionEvent
    from trophies.constants import BADGE_TYPES
    from django.db.models import Count

    TYPE_LABELS = {
        'series': 'Series', 'collection': 'Collection', 'megamix': 'Megamix',
        'developer': 'Developer', 'misc': 'Misc', 'user': 'User', 'genre': 'Genre',
    }

    type_counts = dict(
        StageCompletionEvent.objects.filter(profile=profile)
        .values_list('badge__badge_type')
        .annotate(count=Count('id'))
        .values_list('badge__badge_type', 'count')
    )

    labels = [TYPE_LABELS.get(t, t.title()) for t in BADGE_TYPES]
    counts = [type_counts.get(t, 0) for t in BADGE_TYPES]
    total = sum(counts)

    if total == 0:
        return {'has_data': False}

    return {
        'has_data': True,
        'labels': labels,
        'counts': counts,
        'total': total,
    }


def _build_badge_xp_growth_data(profile, year):
    """Cumulative badge XP over the year (monthly). Includes stage XP + completion bonuses."""
    from trophies.models import UserBadge, StageCompletionEvent
    from django.db.models import Count
    from django.db.models.functions import TruncMonth
    from datetime import date
    import calendar as cal_module
    from trophies.util_modules.constants import BADGE_TIER_XP
    from trophies.services.xp_service import TIER_XP_MAP

    labels = [cal_module.month_abbr[m] for m in range(1, 13)]

    # Badge completion XP: baseline + monthly
    badge_baseline = UserBadge.objects.filter(
        profile=profile, earned_at__lt=_year_start_dt(year),
    ).count()

    badge_monthly_raw = list(
        UserBadge.objects
        .filter(profile=profile, earned_at__year=year)
        .annotate(month=TruncMonth('earned_at'))
        .values('month')
        .annotate(count=Count('id'))
        .order_by('month')
    )
    monthly_badges = [0] * 12
    for entry in badge_monthly_raw:
        monthly_badges[entry['month'].month - 1] = entry['count']

    # Stage XP: baseline + monthly (each stage earns tier-specific XP)
    stage_baseline_xp = 0
    pre_year_stages = list(
        StageCompletionEvent.objects
        .filter(profile=profile, completed_at__lt=_year_start_dt(year))
        .values_list('badge__tier', flat=True)
    )
    for tier in pre_year_stages:
        stage_baseline_xp += TIER_XP_MAP.get(tier, 0)

    stage_monthly_raw = list(
        StageCompletionEvent.objects
        .filter(profile=profile, completed_at__year=year)
        .annotate(month=TruncMonth('completed_at'))
        .values('month', 'badge__tier')
        .annotate(count=Count('id'))
        .order_by('month')
    )
    monthly_stage_xp = [0] * 12
    for entry in stage_monthly_raw:
        m = entry['month'].month
        tier_xp = TIER_XP_MAP.get(entry['badge__tier'], 0)
        monthly_stage_xp[m - 1] += entry['count'] * tier_xp

    baseline_xp = (badge_baseline * BADGE_TIER_XP) + stage_baseline_xp
    year_badges = sum(monthly_badges)

    if baseline_xp == 0 and year_badges == 0 and sum(monthly_stage_xp) == 0:
        return {'has_data': False}

    # Cumulative XP line (completion bonuses + stage XP)
    cumulative = []
    running = baseline_xp
    for i in range(12):
        running += (monthly_badges[i] * BADGE_TIER_XP) + monthly_stage_xp[i]
        cumulative.append(running)

    n = _months_to_show(year)

    return {
        'has_data': True,
        'labels': labels[:n],
        'cumulative': cumulative[:n],
        'year_xp': f"{year_badges * BADGE_TIER_XP:,}",
        'current_xp': f"{cumulative[n - 1]:,}" if cumulative else '0',
    }


def _build_all_time_badge_xp_growth(profile):
    """All-time badge XP by year with quarterly breakdown. Includes stage XP + completion bonuses."""
    from trophies.models import UserBadge, StageCompletionEvent
    from django.db.models import Count, Min
    from django.db.models.functions import ExtractYear, ExtractQuarter
    from django.utils import timezone
    from trophies.util_modules.constants import BADGE_TIER_XP
    from trophies.services.xp_service import TIER_XP_MAP

    now = timezone.now()

    first_badge = UserBadge.objects.filter(profile=profile).aggregate(first=Min('earned_at'))['first']
    first_stage = StageCompletionEvent.objects.filter(profile=profile).aggregate(first=Min('completed_at'))['first']
    first = min(filter(None, [first_badge, first_stage]), default=None)
    if not first:
        return {'has_data': False}

    start_year = first.year
    years = list(range(start_year, now.year + 1))
    labels = [str(y) for y in years]

    # Badge completion XP by year+quarter
    badge_raw = list(
        UserBadge.objects
        .filter(profile=profile)
        .annotate(yr=ExtractYear('earned_at'), qtr=ExtractQuarter('earned_at'))
        .values('yr', 'qtr')
        .annotate(count=Count('id'))
        .order_by('yr', 'qtr')
    )
    badge_lookup = {(e['yr'], e['qtr']): e['count'] * BADGE_TIER_XP for e in badge_raw}

    # Stage XP by year+quarter (tier-specific XP per stage)
    stage_raw = list(
        StageCompletionEvent.objects
        .filter(profile=profile)
        .annotate(yr=ExtractYear('completed_at'), qtr=ExtractQuarter('completed_at'))
        .values('yr', 'qtr', 'badge__tier')
        .annotate(count=Count('id'))
        .order_by('yr', 'qtr')
    )
    stage_lookup = defaultdict(int)
    for e in stage_raw:
        key = (e['yr'], e['qtr'])
        stage_lookup[key] += e['count'] * TIER_XP_MAP.get(e['badge__tier'], 0)

    # Combine into quarterly totals
    q1 = [badge_lookup.get((y, 1), 0) + stage_lookup.get((y, 1), 0) for y in years]
    q2 = [badge_lookup.get((y, 2), 0) + stage_lookup.get((y, 2), 0) for y in years]
    q3 = [badge_lookup.get((y, 3), 0) + stage_lookup.get((y, 3), 0) for y in years]
    q4 = [badge_lookup.get((y, 4), 0) + stage_lookup.get((y, 4), 0) for y in years]

    totals = [q1[i] + q2[i] + q3[i] + q4[i] for i in range(len(years))]
    best_idx = max(range(len(totals)), key=lambda i: totals[i]) if totals else 0
    total = sum(totals)

    return {
        'has_data': True,
        'labels': labels,
        'q1': q1, 'q2': q2, 'q3': q3, 'q4': q4,
        'best_year': labels[best_idx] if labels else '',
        'best_xp': f"{totals[best_idx]:,}" if totals else '0',
        'total': f"{total:,}",
    }


def _build_badge_tier_trend_data(profile, year):
    """Badge tier distribution by month."""
    from trophies.models import UserBadge
    from django.db.models import Count
    from django.db.models.functions import TruncMonth
    import calendar as cal_module

    labels = [cal_module.month_abbr[m] for m in range(1, 13)]

    monthly_tiers = list(
        UserBadge.objects
        .filter(profile=profile, earned_at__year=year)
        .annotate(month=TruncMonth('earned_at'))
        .values('month', 'badge__tier')
        .annotate(count=Count('id'))
        .order_by('month')
    )

    if not monthly_tiers:
        return {'has_data': False}

    series = {1: [0] * 12, 2: [0] * 12, 3: [0] * 12, 4: [0] * 12}
    for entry in monthly_tiers:
        m = entry['month'].month
        tier = entry['badge__tier']
        if tier in series:
            series[tier][m - 1] = entry['count']

    n = _months_to_show(year)

    return {
        'has_data': True,
        'labels': labels[:n],
        'bronze': series[1][:n],
        'silver': series[2][:n],
        'gold': series[3][:n],
        'platinum': series[4][:n],
    }


def _build_all_time_badge_tier_trend(profile):
    """All-time badge tier distribution by quarter."""
    from trophies.models import UserBadge
    from django.db.models import Count, Min
    from django.db.models.functions import TruncQuarter
    from django.utils import timezone

    now = timezone.now()

    first = UserBadge.objects.filter(
        profile=profile,
    ).aggregate(first=Min('earned_at'))['first']
    if not first:
        return {'has_data': False}

    start_year = first.year
    labels = _build_quarterly_labels(start_year, now.year)
    num_q = len(labels)

    def _quarter_idx(dt):
        return (dt.year - start_year) * 4 + (dt.month - 1) // 3

    raw = list(
        UserBadge.objects
        .filter(profile=profile)
        .annotate(quarter=TruncQuarter('earned_at'))
        .values('quarter', 'badge__tier')
        .annotate(count=Count('id'))
        .order_by('quarter')
    )

    series = {1: [0] * num_q, 2: [0] * num_q, 3: [0] * num_q, 4: [0] * num_q}
    for entry in raw:
        idx = _quarter_idx(entry['quarter'])
        tier = entry['badge__tier']
        if 0 <= idx < num_q and tier in series:
            series[tier][idx] = entry['count']

    current_q_idx = _quarter_idx(now)
    n = min(current_q_idx + 1, num_q)

    return {
        'has_data': True,
        'labels': labels[:n],
        'bronze': series[1][:n],
        'silver': series[2][:n],
        'gold': series[3][:n],
        'platinum': series[4][:n],
    }


def _build_badge_growth_data(profile, year):
    """Cumulative badges earned over the year (line chart)."""
    from trophies.models import UserBadge
    from django.db.models import Count
    from django.db.models.functions import TruncMonth
    from datetime import date
    import calendar as cal_module

    labels = [cal_module.month_abbr[m] for m in range(1, 13)]

    baseline = UserBadge.objects.filter(
        profile=profile, earned_at__lt=_year_start_dt(year),
    ).count()

    monthly_raw = list(
        UserBadge.objects
        .filter(profile=profile, earned_at__year=year)
        .annotate(month=TruncMonth('earned_at'))
        .values('month')
        .annotate(count=Count('id'))
        .order_by('month')
    )
    monthly = [0] * 12
    for entry in monthly_raw:
        monthly[entry['month'].month - 1] = entry['count']

    year_total = sum(monthly)
    if baseline == 0 and year_total == 0:
        return {'has_data': False}

    cumulative = []
    running = baseline
    for m in monthly:
        running += m
        cumulative.append(running)

    n = _months_to_show(year)

    return {
        'has_data': True,
        'labels': labels[:n],
        'cumulative': cumulative[:n],
    }


def _build_badge_earning_rate_data(profile, year):
    """Badges earned per month (bar chart)."""
    from trophies.models import UserBadge
    from django.db.models import Count
    from django.db.models.functions import TruncMonth
    import calendar as cal_module

    labels = [cal_module.month_abbr[m] for m in range(1, 13)]

    raw = list(
        UserBadge.objects
        .filter(profile=profile, earned_at__year=year)
        .annotate(month=TruncMonth('earned_at'))
        .values('month')
        .annotate(count=Count('id'))
        .order_by('month')
    )

    if not raw:
        return {'has_data': False}

    monthly = [0] * 12
    for entry in raw:
        monthly[entry['month'].month - 1] = entry['count']

    n = _months_to_show(year)
    total = sum(monthly[:n])
    avg = round(total / n, 1) if n else 0
    peak = max(monthly[:n]) if monthly[:n] else 0

    return {
        'has_data': True,
        'labels': labels[:n],
        'monthly': monthly[:n],
        'total': f"{total:,}",
        'avg': avg,
        'peak': peak,
    }


def _build_stage_growth_data(profile, year):
    """Cumulative stages completed over the year (line chart)."""
    from trophies.models import StageCompletionEvent
    from django.db.models import Count
    from django.db.models.functions import TruncMonth
    from datetime import date
    import calendar as cal_module

    labels = [cal_module.month_abbr[m] for m in range(1, 13)]

    baseline = StageCompletionEvent.objects.filter(
        profile=profile, completed_at__lt=_year_start_dt(year),
    ).count()

    monthly_raw = list(
        StageCompletionEvent.objects
        .filter(profile=profile, completed_at__year=year)
        .annotate(month=TruncMonth('completed_at'))
        .values('month')
        .annotate(count=Count('id'))
        .order_by('month')
    )
    monthly = [0] * 12
    for entry in monthly_raw:
        monthly[entry['month'].month - 1] = entry['count']

    year_total = sum(monthly)
    if baseline == 0 and year_total == 0:
        return {'has_data': False}

    cumulative = []
    running = baseline
    for m in monthly:
        running += m
        cumulative.append(running)

    n = _months_to_show(year)

    return {
        'has_data': True,
        'labels': labels[:n],
        'cumulative': cumulative[:n],
    }


def _build_all_time_badge_growth(profile):
    """All-time cumulative badges earned by quarter."""
    from trophies.models import UserBadge
    from django.db.models import Count, Min
    from django.db.models.functions import TruncQuarter
    from django.utils import timezone

    now = timezone.now()
    first = UserBadge.objects.filter(profile=profile).aggregate(first=Min('earned_at'))['first']
    if not first:
        return {'has_data': False}

    start_year = first.year
    labels = _build_quarterly_labels(start_year, now.year)
    num_q = len(labels)

    def _quarter_idx(dt):
        return (dt.year - start_year) * 4 + (dt.month - 1) // 3

    raw = list(
        UserBadge.objects
        .filter(profile=profile)
        .annotate(quarter=TruncQuarter('earned_at'))
        .values('quarter')
        .annotate(count=Count('id'))
        .order_by('quarter')
    )
    quarterly = [0] * num_q
    for entry in raw:
        idx = _quarter_idx(entry['quarter'])
        if 0 <= idx < num_q:
            quarterly[idx] = entry['count']

    cumulative = []
    running = 0
    for q in quarterly:
        running += q
        cumulative.append(running)

    current_q_idx = _quarter_idx(now)
    n = min(current_q_idx + 1, num_q)

    return {
        'has_data': True,
        'labels': labels[:n],
        'cumulative': cumulative[:n],
    }


def _build_all_time_badge_earning_rate(profile):
    """All-time badges earned per quarter."""
    from trophies.models import UserBadge
    from django.db.models import Count, Min
    from django.db.models.functions import TruncQuarter
    from django.utils import timezone

    now = timezone.now()
    first = UserBadge.objects.filter(profile=profile).aggregate(first=Min('earned_at'))['first']
    if not first:
        return {'has_data': False}

    start_year = first.year
    labels = _build_quarterly_labels(start_year, now.year)
    num_q = len(labels)

    def _quarter_idx(dt):
        return (dt.year - start_year) * 4 + (dt.month - 1) // 3

    raw = list(
        UserBadge.objects
        .filter(profile=profile)
        .annotate(quarter=TruncQuarter('earned_at'))
        .values('quarter')
        .annotate(count=Count('id'))
        .order_by('quarter')
    )
    quarterly = [0] * num_q
    for entry in raw:
        idx = _quarter_idx(entry['quarter'])
        if 0 <= idx < num_q:
            quarterly[idx] = entry['count']

    current_q_idx = _quarter_idx(now)
    n = min(current_q_idx + 1, num_q)
    total = sum(quarterly[:n])
    avg = round(total / n, 1) if n else 0
    peak = max(quarterly[:n]) if quarterly[:n] else 0

    return {
        'has_data': True,
        'labels': labels[:n],
        'monthly': quarterly[:n],
        'total': f"{total:,}",
        'avg': avg,
        'peak': peak,
    }


def _build_all_time_stage_growth(profile):
    """All-time cumulative stages completed by quarter."""
    from trophies.models import StageCompletionEvent
    from django.db.models import Count, Min
    from django.db.models.functions import TruncQuarter
    from django.utils import timezone

    now = timezone.now()
    first = StageCompletionEvent.objects.filter(profile=profile).aggregate(first=Min('completed_at'))['first']
    if not first:
        return {'has_data': False}

    start_year = first.year
    labels = _build_quarterly_labels(start_year, now.year)
    num_q = len(labels)

    def _quarter_idx(dt):
        return (dt.year - start_year) * 4 + (dt.month - 1) // 3

    raw = list(
        StageCompletionEvent.objects
        .filter(profile=profile)
        .annotate(quarter=TruncQuarter('completed_at'))
        .values('quarter')
        .annotate(count=Count('id'))
        .order_by('quarter')
    )
    quarterly = [0] * num_q
    for entry in raw:
        idx = _quarter_idx(entry['quarter'])
        if 0 <= idx < num_q:
            quarterly[idx] = entry['count']

    cumulative = []
    running = 0
    for q in quarterly:
        running += q
        cumulative.append(running)

    current_q_idx = _quarter_idx(now)
    n = min(current_q_idx + 1, num_q)

    return {
        'has_data': True,
        'labels': labels[:n],
        'cumulative': cumulative[:n],
    }


def _build_xp_sources_data(profile, year):
    """Monthly XP split: completion bonuses vs progress XP."""
    from trophies.models import UserBadge
    from django.db.models import Count
    from django.db.models.functions import TruncMonth
    from datetime import date
    import calendar as cal_module
    from trophies.util_modules.constants import BADGE_TIER_XP

    labels = [cal_module.month_abbr[m] for m in range(1, 13)]

    # Badges earned per month = completion bonus events
    badge_monthly = list(
        UserBadge.objects
        .filter(profile=profile, earned_at__year=year)
        .annotate(month=TruncMonth('earned_at'))
        .values('month')
        .annotate(count=Count('id'))
        .order_by('month')
    )
    monthly_badges = [0] * 12
    for entry in badge_monthly:
        monthly_badges[entry['month'].month - 1] = entry['count']

    # Completion XP per month = badges * BADGE_TIER_XP
    completion_xp = [b * BADGE_TIER_XP for b in monthly_badges]

    # Total XP we can compute per month: we know completion XP precisely.
    # Progress XP is harder to attribute monthly (no timestamp on stage completion).
    # Approximate: total progress XP / total months active, distributed evenly.
    # Better for visualization: just show the completion XP line and note that
    # progress XP is earned continuously between badge completions.

    if not any(monthly_badges):
        return {'has_data': False}

    # Cumulative completion XP
    cum_completion = []
    baseline = UserBadge.objects.filter(
        profile=profile, earned_at__lt=_year_start_dt(year),
    ).count() * BADGE_TIER_XP
    running = baseline
    for c in completion_xp:
        running += c
        cum_completion.append(running)

    n = _months_to_show(year)

    return {
        'has_data': True,
        'labels': labels[:n],
        'completion_xp': cum_completion[:n],
        'year_completion_xp': f"{sum(completion_xp):,}",
    }


def _build_all_time_xp_sources(profile):
    """All-time XP sources by quarter: completion bonus XP."""
    from trophies.models import UserBadge
    from django.db.models import Count, Min
    from django.db.models.functions import TruncQuarter
    from django.utils import timezone
    from trophies.util_modules.constants import BADGE_TIER_XP

    now = timezone.now()

    first = UserBadge.objects.filter(
        profile=profile,
    ).aggregate(first=Min('earned_at'))['first']
    if not first:
        return {'has_data': False}

    start_year = first.year
    labels = _build_quarterly_labels(start_year, now.year)
    num_q = len(labels)

    def _quarter_idx(dt):
        return (dt.year - start_year) * 4 + (dt.month - 1) // 3

    raw = list(
        UserBadge.objects
        .filter(profile=profile)
        .annotate(quarter=TruncQuarter('earned_at'))
        .values('quarter')
        .annotate(count=Count('id'))
        .order_by('quarter')
    )
    quarterly = [0] * num_q
    for entry in raw:
        idx = _quarter_idx(entry['quarter'])
        if 0 <= idx < num_q:
            quarterly[idx] = entry['count'] * BADGE_TIER_XP

    cum_xp = []
    running = 0
    for q in quarterly:
        running += q
        cum_xp.append(running)

    current_q_idx = _quarter_idx(now)
    n = min(current_q_idx + 1, num_q)

    return {
        'has_data': True,
        'labels': labels[:n],
        'completion_xp': cum_xp[:n],
    }


def _build_stages_by_series_data(profile, year):
    """Stage completions by series per month for stacked bar chart."""
    from trophies.models import StageCompletionEvent
    from django.db.models import Count
    from django.db.models.functions import TruncMonth
    import calendar as cal_module

    labels = [cal_module.month_abbr[m] for m in range(1, 13)]

    raw = list(
        StageCompletionEvent.objects
        .filter(profile=profile, completed_at__year=year)
        .annotate(month=TruncMonth('completed_at'))
        .values('month', 'badge__series_slug')
        .annotate(count=Count('id'))
        .order_by('month')
    )

    if not raw:
        return {'has_data': False}

    # Find top series by total count across all months
    series_totals = defaultdict(int)
    for entry in raw:
        slug = entry['badge__series_slug'] or 'other'
        series_totals[slug] += entry['count']

    top_series = sorted(series_totals.items(), key=lambda x: -x[1])[:6]
    top_slugs = [s[0] for s in top_series]

    def _format_slug(slug):
        return slug.replace('-', ' ').title() if slug else 'Other'

    # Build monthly data per series
    series_data = {}
    for slug in top_slugs:
        series_data[slug] = [0] * 12
    for entry in raw:
        slug = entry['badge__series_slug'] or 'other'
        if slug in series_data:
            m = entry['month'].month
            series_data[slug][m - 1] = entry['count']

    n = _months_to_show(year)

    # Color palette for series
    SERIES_COLORS = [
        'rgba(59, 130, 246, 0.7)',   # blue
        'rgba(34, 197, 94, 0.7)',    # green
        'rgba(245, 158, 11, 0.7)',   # amber
        'rgba(239, 68, 68, 0.7)',    # red
        'rgba(168, 85, 247, 0.7)',   # purple
        'rgba(6, 182, 212, 0.7)',    # cyan
    ]

    datasets = []
    for i, slug in enumerate(top_slugs):
        datasets.append({
            'label': _format_slug(slug),
            'data': series_data[slug][:n],
            'color': SERIES_COLORS[i % len(SERIES_COLORS)],
        })

    return {
        'has_data': True,
        'labels': labels[:n],
        'datasets': datasets,
    }


def _build_all_time_stages_by_series(profile):
    """All-time stage completions by series per quarter."""
    from trophies.models import StageCompletionEvent
    from django.db.models import Count, Min
    from django.db.models.functions import TruncQuarter
    from django.utils import timezone

    now = timezone.now()

    first = StageCompletionEvent.objects.filter(
        profile=profile,
    ).aggregate(first=Min('completed_at'))['first']
    if not first:
        return {'has_data': False}

    start_year = first.year
    labels = _build_quarterly_labels(start_year, now.year)
    num_q = len(labels)

    def _quarter_idx(dt):
        return (dt.year - start_year) * 4 + (dt.month - 1) // 3

    raw = list(
        StageCompletionEvent.objects
        .filter(profile=profile)
        .annotate(quarter=TruncQuarter('completed_at'))
        .values('quarter', 'badge__series_slug')
        .annotate(count=Count('id'))
        .order_by('quarter')
    )

    if not raw:
        return {'has_data': False}

    # Find top series
    series_totals = defaultdict(int)
    for entry in raw:
        slug = entry['badge__series_slug'] or 'other'
        series_totals[slug] += entry['count']

    top_series = sorted(series_totals.items(), key=lambda x: -x[1])[:6]
    top_slugs = [s[0] for s in top_series]

    def _format_slug(slug):
        return slug.replace('-', ' ').title() if slug else 'Other'

    series_data = {}
    for slug in top_slugs:
        series_data[slug] = [0] * num_q
    for entry in raw:
        slug = entry['badge__series_slug'] or 'other'
        if slug in series_data:
            idx = _quarter_idx(entry['quarter'])
            if 0 <= idx < num_q:
                series_data[slug][idx] = entry['count']

    current_q_idx = _quarter_idx(now)
    n = min(current_q_idx + 1, num_q)

    SERIES_COLORS = [
        'rgba(59, 130, 246, 0.7)',
        'rgba(34, 197, 94, 0.7)',
        'rgba(245, 158, 11, 0.7)',
        'rgba(239, 68, 68, 0.7)',
        'rgba(168, 85, 247, 0.7)',
        'rgba(6, 182, 212, 0.7)',
    ]

    datasets = []
    for i, slug in enumerate(top_slugs):
        datasets.append({
            'label': _format_slug(slug),
            'data': series_data[slug][:n],
            'color': SERIES_COLORS[i % len(SERIES_COLORS)],
        })

    return {
        'has_data': True,
        'labels': labels[:n],
        'datasets': datasets,
    }


def _build_stage_completion_rate_data(profile, year):
    """Stages completed per month (rate line chart)."""
    from trophies.models import StageCompletionEvent
    from django.db.models import Count
    from django.db.models.functions import TruncMonth
    import calendar as cal_module

    labels = [cal_module.month_abbr[m] for m in range(1, 13)]

    raw = list(
        StageCompletionEvent.objects
        .filter(profile=profile, completed_at__year=year)
        .annotate(month=TruncMonth('completed_at'))
        .values('month')
        .annotate(count=Count('id'))
        .order_by('month')
    )

    if not raw:
        return {'has_data': False}

    monthly = [0] * 12
    for entry in raw:
        monthly[entry['month'].month - 1] = entry['count']

    n = _months_to_show(year)
    total = sum(monthly[:n])
    avg = round(total / n, 1) if n else 0
    peak = max(monthly[:n]) if monthly[:n] else 0

    return {
        'has_data': True,
        'labels': labels[:n],
        'monthly': monthly[:n],
        'total': f"{total:,}",
        'avg': avg,
        'peak': peak,
    }


def _build_all_time_stage_completion_rate(profile):
    """All-time stages completed per quarter (rate line chart)."""
    from trophies.models import StageCompletionEvent
    from django.db.models import Count, Min
    from django.db.models.functions import TruncQuarter
    from django.utils import timezone

    now = timezone.now()

    first = StageCompletionEvent.objects.filter(
        profile=profile,
    ).aggregate(first=Min('completed_at'))['first']
    if not first:
        return {'has_data': False}

    start_year = first.year
    labels = _build_quarterly_labels(start_year, now.year)
    num_q = len(labels)

    def _quarter_idx(dt):
        return (dt.year - start_year) * 4 + (dt.month - 1) // 3

    raw = list(
        StageCompletionEvent.objects
        .filter(profile=profile)
        .annotate(quarter=TruncQuarter('completed_at'))
        .values('quarter')
        .annotate(count=Count('id'))
        .order_by('quarter')
    )

    quarterly = [0] * num_q
    for entry in raw:
        idx = _quarter_idx(entry['quarter'])
        if 0 <= idx < num_q:
            quarterly[idx] = entry['count']

    current_q_idx = _quarter_idx(now)
    n = min(current_q_idx + 1, num_q)
    total = sum(quarterly[:n])
    avg = round(total / n, 1) if n else 0
    peak = max(quarterly[:n]) if quarterly[:n] else 0

    return {
        'has_data': True,
        'labels': labels[:n],
        'monthly': quarterly[:n],
        'total': f"{total:,}",
        'avg': avg,
        'peak': peak,
    }


def provide_badge_series_overview(profile):
    """All-time badge series status: stage progress, tier grid, series XP radar, badge type/stages radars."""
    return {
        'stage_progress': _build_stage_progress_data(profile),
        'series_radar': _build_series_xp_radar_data(profile),
        'type_radar': _build_badge_type_radar_data(profile),
        'stages_radar': _build_series_stages_radar_data(profile),
        'stage_type_breakdown': _build_stage_type_breakdown_data(profile),
    }


def provide_badge_visualizations(profile, settings=None):
    """Combined premium badge visualization module."""
    from django.utils import timezone

    settings = settings or {}
    now = timezone.now()
    year_val = settings.get('year', '')
    is_all = year_val == 'all'

    try:
        year = int(year_val) if year_val and not is_all else now.year
    except (ValueError, TypeError):
        year = now.year

    # Stage progress is always all-time (cumulative, not year-specific)
    if is_all:
        return {
            'year': 'all',
            'year_label': 'All Time',
            'current_year': now.year,
            'xp_growth': _build_all_time_badge_xp_growth(profile),
            'stages_by_series': _build_all_time_stages_by_series(profile),
            'badge_growth': _build_all_time_badge_growth(profile),
            'badge_rate': _build_all_time_badge_earning_rate(profile),
            'stage_growth': _build_all_time_stage_growth(profile),
            'stage_rate': _build_all_time_stage_completion_rate(profile),
        }

    return {
        'year': year,
        'year_label': str(year),
        'current_year': now.year,
        'xp_growth': _build_badge_xp_growth_data(profile, year),
        'stages_by_series': _build_stages_by_series_data(profile, year),
        'badge_growth': _build_badge_growth_data(profile, year),
        'badge_rate': _build_badge_earning_rate_data(profile, year),
        'stage_growth': _build_stage_growth_data(profile, year),
        'stage_rate': _build_stage_completion_rate_data(profile, year),
    }


# ---------------------------------------------------------------------------
# Module Registry
# ---------------------------------------------------------------------------

DASHBOARD_MODULES = [
    {
        'slug': 'trophy_snapshot',
        'name': 'Trophy Snapshot',
        'description': 'Your trophy collection at a glance: platinums, golds, completion rate, and more.',
        'category': 'at_a_glance',
        'template': 'trophies/partials/dashboard/trophy_snapshot.html',
        'provider': provide_trophy_snapshot,
        'requires_premium': False,
        'load_strategy': 'server',
        'default_order': 1,
        'default_settings': {},
        'configurable_settings': [],
        'cache_ttl': 0,
        'default_size': 'medium',
        'allowed_sizes': ['small', 'medium', 'large'],
    },
    {
        'slug': 'recent_platinums',
        'name': 'Recent Platinums',
        'description': 'Your latest platinum conquests with rarity and earn dates.',
        'category': 'at_a_glance',
        'template': 'trophies/partials/dashboard/recent_platinums.html',
        'provider': provide_recent_platinums,
        'requires_premium': False,
        'load_strategy': 'lazy',
        'default_order': 2,
        'default_settings': {'limit': 6},
        'configurable_settings': [
            {'key': 'limit', 'label': 'Items to show', 'type': 'select', 'default': 6,
             'options': [{'value': 3, 'label': '3'}, {'value': 6, 'label': '6'}, {'value': 10, 'label': '10'}]},
        ],
        'cache_ttl': 300,
        'default_size': 'medium',
        'allowed_sizes': ['small', 'medium', 'large'],
    },
    {
        'slug': 'challenge_hub',
        'name': 'Challenge Hub',
        'description': 'Track your A-Z, Calendar, and Genre challenge progress all in one place.',
        'category': 'progress',
        'template': 'trophies/partials/dashboard/challenge_hub.html',
        'provider': provide_challenge_hub,
        'requires_premium': False,
        'load_strategy': 'lazy',
        'default_order': 3,
        'default_settings': {},
        'configurable_settings': [],
        'cache_ttl': 300,
        'default_size': 'large',
        'allowed_sizes': ['medium', 'large'],
    },
    {
        'slug': 'badge_progress',
        'name': 'Badge Progress',
        'description': 'Badges you are closest to earning. Keep pushing, hunter!',
        'category': 'badges',
        'template': 'trophies/partials/dashboard/badge_progress.html',
        'provider': provide_badge_progress,
        'requires_premium': False,
        'load_strategy': 'lazy',
        'default_order': 4,
        'default_settings': {'limit': 4},
        'configurable_settings': [
            {'key': 'limit', 'label': 'Items to show', 'type': 'select', 'default': 4,
             'options': [{'value': 2, 'label': '2'}, {'value': 4, 'label': '4'}, {'value': 6, 'label': '6'}]},
        ],
        'cache_ttl': 600,
        'default_size': 'medium',
        'allowed_sizes': ['small', 'medium', 'large'],
    },
    {
        'slug': 'recent_badges',
        'name': 'Recent Badges',
        'description': 'Your latest badge conquests. Celebrate every tier!',
        'category': 'badges',
        'template': 'trophies/partials/dashboard/recent_badges.html',
        'provider': provide_recent_badges,
        'requires_premium': False,
        'load_strategy': 'lazy',
        'default_order': 5,
        'default_settings': {'limit': 6},
        'configurable_settings': [
            {'key': 'limit', 'label': 'Items to show', 'type': 'select', 'default': 6,
             'options': [{'value': 3, 'label': '3'}, {'value': 6, 'label': '6'}, {'value': 9, 'label': '9'}]},
        ],
        'cache_ttl': 600,
        'default_size': 'medium',
        'allowed_sizes': ['small', 'medium', 'large'],
    },
    {
        'slug': 'recent_activity',
        'name': 'Recent Activity',
        'description': 'Your latest trophy earns and badge awards in one feed.',
        'category': 'at_a_glance',
        'template': 'trophies/partials/dashboard/recent_activity.html',
        'provider': provide_recent_activity,
        'requires_premium': False,
        'load_strategy': 'lazy',
        'default_order': 6,
        'default_settings': {'limit': 8},
        'configurable_settings': [
            {'key': 'limit', 'label': 'Items to show', 'type': 'select', 'default': 8,
             'options': [{'value': 5, 'label': '5'}, {'value': 8, 'label': '8'}, {'value': 12, 'label': '12'}]},
        ],
        'cache_ttl': 300,
        'default_size': 'medium',
        'allowed_sizes': ['small', 'medium', 'large'],
    },
    {
        'slug': 'monthly_recap_preview',
        'name': 'Monthly Recap',
        'description': 'A sneak peek at your current month. How are you doing?',
        'category': 'highlights',
        'template': 'trophies/partials/dashboard/monthly_recap_preview.html',
        'provider': provide_monthly_recap_preview,
        'requires_premium': False,
        'load_strategy': 'lazy',
        'default_order': 7,
        'default_settings': {},
        'configurable_settings': [],
        'cache_ttl': 1800,
        'default_size': 'medium',
        'allowed_sizes': ['small', 'medium', 'large'],
    },
    {
        'slug': 'quick_settings',
        'name': 'Quick Settings',
        'description': 'Adjust your profile settings without leaving the dashboard.',
        'category': 'at_a_glance',
        'template': 'trophies/partials/dashboard/quick_settings.html',
        'provider': provide_quick_settings,
        'requires_premium': False,
        'load_strategy': 'server',
        'default_order': 8,
        'default_settings': {},
        'configurable_settings': [],
        'cache_ttl': 0,
        'default_size': 'medium',
        'allowed_sizes': ['small', 'medium', 'large'],
    },
    {
        'slug': 'badge_stats',
        'name': 'Badge Stats',
        'description': 'Badge collection analytics: tier breakdown, rarest badge, and completion rate.',
        'category': 'badges',
        'template': 'trophies/partials/dashboard/badge_stats.html',
        'provider': provide_badge_stats,
        'requires_premium': False,
        'load_strategy': 'lazy',
        'default_order': 9,
        'default_settings': {},
        'configurable_settings': [],
        'cache_ttl': 600,
        'default_size': 'medium',
        'allowed_sizes': ['small', 'medium', 'large'],
    },
    {
        'slug': 'badge_xp_leaderboard',
        'name': 'Badge XP & Leaderboard',
        'description': 'Your badge XP stats and leaderboard position. How do you stack up?',
        'category': 'badges',
        'template': 'trophies/partials/dashboard/badge_xp_leaderboard.html',
        'provider': provide_badge_xp_leaderboard,
        'requires_premium': False,
        'load_strategy': 'lazy',
        'default_order': 10,
        'default_settings': {},
        'configurable_settings': [],
        'cache_ttl': 600,
        'default_size': 'medium',
        'allowed_sizes': ['small', 'medium', 'large'],
    },
    {
        'slug': 'country_xp_leaderboard',
        'name': 'Country XP Leaderboard',
        'description': 'How you rank in your country. Local glory awaits.',
        'category': 'badges',
        'template': 'trophies/partials/dashboard/country_xp_leaderboard.html',
        'provider': provide_country_xp_leaderboard,
        'requires_premium': False,
        'load_strategy': 'lazy',
        'default_order': 11,
        'default_settings': {},
        'configurable_settings': [],
        'cache_ttl': 600,
        'default_size': 'small',
        'allowed_sizes': ['small', 'medium'],
    },
    {
        'slug': 'az_challenge',
        'name': 'A-Z Challenge',
        'description': 'Your full 26-letter A-Z challenge grid. Every letter, every platinum.',
        'category': 'progress',
        'template': 'trophies/partials/dashboard/az_challenge.html',
        'provider': provide_az_challenge,
        'requires_premium': False,
        'load_strategy': 'lazy',
        'default_order': 11,
        'default_settings': {},
        'configurable_settings': [],
        'cache_ttl': 300,
        'default_size': 'medium',
        'allowed_sizes': ['medium', 'large'],
    },
    {
        'slug': 'genre_challenge',
        'name': 'Genre Challenge',
        'description': 'Genre slots, subgenre tags, and bonus progress. Master every genre!',
        'category': 'progress',
        'template': 'trophies/partials/dashboard/genre_challenge.html',
        'provider': provide_genre_challenge,
        'requires_premium': False,
        'load_strategy': 'lazy',
        'default_order': 12,
        'default_settings': {},
        'configurable_settings': [],
        'cache_ttl': 300,
        'default_size': 'medium',
        'allowed_sizes': ['medium', 'large'],
    },
    {
        'slug': 'calendar_challenge',
        'name': 'Platinum Calendar',
        'description': 'Your full 365-day platinum calendar. Every month, every day.',
        'category': 'progress',
        'template': 'trophies/partials/dashboard/calendar_challenge.html',
        'provider': provide_calendar_challenge,
        'requires_premium': False,
        'load_strategy': 'lazy',
        'default_order': 13,
        'default_settings': {},
        'configurable_settings': [],
        'cache_ttl': 300,
        'default_size': 'large',
        'allowed_sizes': ['medium', 'large'],
    },
    {
        'slug': 'completion_milestones',
        'name': 'Almost There',
        'description': 'Games you are closest to 100%. The finish line is in sight!',
        'category': 'progress',
        'template': 'trophies/partials/dashboard/completion_milestones.html',
        'provider': provide_completion_milestones,
        'requires_premium': False,
        'load_strategy': 'lazy',
        'default_order': 14,
        'default_settings': {'limit': 6, 'threshold': 90},
        'configurable_settings': [
            {'key': 'limit', 'label': 'Items to show', 'type': 'select', 'default': 6,
             'options': [{'value': 3, 'label': '3'}, {'value': 6, 'label': '6'}, {'value': 10, 'label': '10'}]},
            {'key': 'threshold', 'label': 'Min. progress', 'type': 'select', 'default': 90,
             'options': [{'value': 80, 'label': '80%'}, {'value': 90, 'label': '90%'}, {'value': 95, 'label': '95%'}]},
        ],
        'cache_ttl': 600,
        'default_size': 'medium',
        'allowed_sizes': ['small', 'medium', 'large'],
    },
    {
        'slug': 'milestone_tracker',
        'name': 'Milestone Tracker',
        'description': 'Track your next milestones. See what you are closest to earning.',
        'category': 'progress',
        'template': 'trophies/partials/dashboard/milestone_tracker.html',
        'provider': provide_milestone_tracker,
        'requires_premium': False,
        'load_strategy': 'lazy',
        'default_order': 15,
        'default_settings': {'limit': 6},
        'configurable_settings': [
            {'key': 'limit', 'label': 'Items to show', 'type': 'select', 'default': 6,
             'options': [{'value': 3, 'label': '3'}, {'value': 6, 'label': '6'}, {'value': 9, 'label': '9'}]},
        ],
        'cache_ttl': 600,
        'default_size': 'medium',
        'allowed_sizes': ['small', 'medium', 'large'],
    },
    {
        'slug': 'my_reviews',
        'name': 'My Reviews',
        'description': 'Your reviews at a glance. Helpful votes, replies, and recent posts.',
        'category': 'highlights',
        'template': 'trophies/partials/dashboard/my_reviews.html',
        'provider': provide_my_reviews,
        'requires_premium': False,
        'load_strategy': 'lazy',
        'default_order': 16,
        'default_settings': {'limit': 6},
        'configurable_settings': [
            {'key': 'limit', 'label': 'Reviews to show', 'type': 'select', 'default': 6,
             'options': [{'value': 3, 'label': '3'}, {'value': 6, 'label': '6'}, {'value': 9, 'label': '9'}]},
        ],
        'cache_ttl': 600,
        'default_size': 'medium',
        'allowed_sizes': ['small', 'medium', 'large'],
    },
    {
        'slug': 'rarity_showcase',
        'name': 'Rarity Showcase',
        'description': 'Your rarest earned trophies. Bragging rights for the dedicated hunter.',
        'category': 'highlights',
        'template': 'trophies/partials/dashboard/rarity_showcase.html',
        'provider': provide_rarity_showcase,
        'requires_premium': False,
        'load_strategy': 'lazy',
        'default_order': 17,
        'default_settings': {'limit': 6},
        'configurable_settings': [
            {'key': 'limit', 'label': 'Trophies to show', 'type': 'select', 'default': 6,
             'options': [{'value': 4, 'label': '4'}, {'value': 6, 'label': '6'}, {'value': 8, 'label': '8'}]},
        ],
        'cache_ttl': 600,
        'default_size': 'medium',
        'allowed_sizes': ['small', 'medium', 'large'],
    },
    {
        'slug': 'rate_my_games',
        'name': 'Rate My Games',
        'description': 'Platinums waiting for your rating. Help the community with your insights!',
        'category': 'highlights',
        'template': 'trophies/partials/dashboard/rate_my_games.html',
        'provider': provide_rate_my_games,
        'requires_premium': False,
        'load_strategy': 'lazy',
        'default_order': 18,
        'default_settings': {},
        'configurable_settings': [],
        'cache_ttl': 1800,
        'default_size': 'small',
        'allowed_sizes': ['small', 'medium'],
    },
    {
        'slug': 'badge_showcase',
        'name': 'Badge Showcase',
        'description': 'Choose your featured badge. This badge appears on your profile card and public profile.',
        'category': 'share',
        'template': 'trophies/partials/dashboard/badge_showcase.html',
        'provider': provide_badge_showcase,
        'requires_premium': False,
        'load_strategy': 'lazy',
        'default_order': 10,
        'default_settings': {},
        'configurable_settings': [],
        'cache_ttl': 0,
        'default_size': 'medium',
        'allowed_sizes': ['small', 'medium', 'large'],
    },
    {
        'slug': 'profile_card_preview',
        'name': 'Profile Card',
        'description': 'Preview and download your shareable profile card image.',
        'category': 'share',
        'template': 'trophies/partials/dashboard/profile_card_preview.html',
        'provider': provide_profile_card_preview,
        'requires_premium': False,
        'load_strategy': 'lazy',
        'default_order': 11,
        'default_settings': {},
        'configurable_settings': [],
        'cache_ttl': 0,
        'default_size': 'large',
        'allowed_sizes': ['medium', 'large'],
    },
    {
        'slug': 'recent_platinum_card',
        'name': 'Latest Platinum',
        'description': 'Preview and download the share card for your most recent platinum.',
        'category': 'share',
        'template': 'trophies/partials/dashboard/recent_platinum_card.html',
        'provider': provide_recent_platinum_card,
        'requires_premium': False,
        'load_strategy': 'lazy',
        'default_order': 12,
        'default_settings': {},
        'configurable_settings': [],
        'cache_ttl': 600,
        'default_size': 'large',
        'allowed_sizes': ['medium', 'large'],
    },
    {
        'slug': 'challenge_share_cards',
        'name': 'Challenge Cards',
        'description': 'Preview and download share cards for your active challenges.',
        'category': 'share',
        'template': 'trophies/partials/dashboard/challenge_share_cards.html',
        'provider': provide_challenge_share_cards,
        'requires_premium': False,
        'load_strategy': 'lazy',
        'default_order': 13,
        'default_settings': {},
        'configurable_settings': [],
        'cache_ttl': 600,
        'default_size': 'large',
        'allowed_sizes': ['medium', 'large'],
    },
    {
        'slug': 'recap_share_card',
        'name': 'Recap Card',
        'description': 'Preview and download the share card for your latest monthly recap.',
        'category': 'share',
        'template': 'trophies/partials/dashboard/recap_share_card.html',
        'provider': provide_recap_share_card,
        'requires_premium': False,
        'load_strategy': 'lazy',
        'default_order': 14,
        'default_settings': {},
        'configurable_settings': [],
        'cache_ttl': 1800,
        'default_size': 'large',
        'allowed_sizes': ['medium', 'large'],
    },
    {
        'slug': 'platinum_grid_cta',
        'name': 'Platinum Grid',
        'description': 'Build a shareable grid image of your platinum trophy collection.',
        'category': 'share',
        'template': 'trophies/partials/dashboard/platinum_grid_cta.html',
        'provider': provide_platinum_grid_cta,
        'requires_premium': False,
        'load_strategy': 'lazy',
        'default_order': 15,
        'default_settings': {},
        'configurable_settings': [],
        'cache_ttl': 600,
        'default_size': 'medium',
        'allowed_sizes': ['small', 'medium', 'large'],
    },
    # --- Premium modules ---
    {
        'slug': 'advanced_stats',
        'name': 'Advanced Stats',
        'description': 'Deep dive into your trophy hunting patterns: velocity, rarity, platforms, and completion.',
        'category': 'premium',
        'template': 'trophies/partials/dashboard/advanced_stats.html',
        'provider': provide_advanced_stats,
        'requires_premium': True,
        'load_strategy': 'lazy',
        'default_order': 2,
        'default_settings': {'range': 'all'},
        'configurable_settings': [
            {'key': 'range', 'label': 'Date Range', 'type': 'select', 'default': 'all',
             'options': [
                 {'value': '7d', 'label': '7 Days'},
                 {'value': '30d', 'label': '30 Days'},
                 {'value': '90d', 'label': '90 Days'},
                 {'value': '1y', 'label': '1 Year'},
                 {'value': 'all', 'label': 'All Time'},
             ]},
        ],
        'cache_ttl': 1800,
        'default_size': 'large',
        'allowed_sizes': ['medium', 'large'],
    },
    {
        'slug': 'premium_settings',
        'name': 'Premium Settings',
        'description': 'Manage your theme and background art right from the dashboard.',
        'category': 'premium',
        'template': 'trophies/partials/dashboard/premium_settings.html',
        'provider': provide_premium_settings,
        'requires_premium': True,
        'load_strategy': 'lazy',
        'default_order': 3,
        'default_settings': {},
        'configurable_settings': [],
        'cache_ttl': 0,
        'default_size': 'medium',
        'allowed_sizes': ['medium', 'large'],
    },
    {
        'slug': 'trophy_visualizations',
        'name': 'Trophy Visualizations',
        'description': 'Premium analytics suite: trophy heatmap, genre radar, year comparison, and progress tracking.',
        'category': 'premium',
        'template': 'trophies/partials/dashboard/trophy_visualizations.html',
        'provider': provide_trophy_visualizations,
        'requires_premium': True,
        'load_strategy': 'lazy',
        'default_order': 4,
        'default_settings': {'year': ''},
        'configurable_settings': [],
        'cache_ttl': 1800,
        'default_size': 'large',
        'allowed_sizes': ['large'],
    },
    {
        'slug': 'advanced_badge_stats',
        'name': 'Advanced Badge Stats',
        'description': 'Deep badge analytics: earning velocity, series depth, XP breakdown, types, and rarest badges.',
        'category': 'premium',
        'template': 'trophies/partials/dashboard/advanced_badge_stats.html',
        'provider': provide_advanced_badge_stats,
        'requires_premium': True,
        'load_strategy': 'lazy',
        'default_order': 8,
        'default_settings': {'range': 'all'},
        'configurable_settings': [
            {'key': 'range', 'label': 'Date Range', 'type': 'select', 'default': 'all',
             'options': [
                 {'value': '7d', 'label': '7 Days'},
                 {'value': '30d', 'label': '30 Days'},
                 {'value': '90d', 'label': '90 Days'},
                 {'value': '1y', 'label': '1 Year'},
                 {'value': 'all', 'label': 'All Time'},
             ]},
        ],
        'cache_ttl': 1800,
        'default_size': 'large',
        'allowed_sizes': ['medium', 'large'],
    },
    {
        'slug': 'badge_series_overview',
        'name': 'Badge Series Overview',
        'description': 'All-time badge series status: stage progress, tier completion grid, and series XP radar.',
        'category': 'premium',
        'template': 'trophies/partials/dashboard/badge_series_overview.html',
        'provider': provide_badge_series_overview,
        'requires_premium': True,
        'load_strategy': 'lazy',
        'default_order': 8,
        'default_settings': {},
        'configurable_settings': [],
        'cache_ttl': 1800,
        'default_size': 'large',
        'allowed_sizes': ['medium', 'large'],
    },
    {
        'slug': 'badge_visualizations',
        'name': 'Badge Visualizations',
        'description': 'Premium badge timeline: stages by series, XP growth, badges vs stages, and completion rate.',
        'category': 'premium',
        'template': 'trophies/partials/dashboard/badge_visualizations.html',
        'provider': provide_badge_visualizations,
        'requires_premium': True,
        'load_strategy': 'lazy',
        'default_order': 9,
        'default_settings': {'year': ''},
        'configurable_settings': [],
        'cache_ttl': 1800,
        'default_size': 'large',
        'allowed_sizes': ['large'],
    },
]

# Pre-built lookup dict for O(1) slug access
_MODULE_LOOKUP = {mod['slug']: mod for mod in DASHBOARD_MODULES}


def _validate_registry():
    """Validate module descriptors at import time. Catches config errors early."""
    seen_slugs = set()
    for mod in DASHBOARD_MODULES:
        slug = mod['slug']
        assert slug not in seen_slugs, f"Duplicate dashboard module slug: {slug}"
        seen_slugs.add(slug)

        assert mod.get('default_size') in VALID_SIZES, \
            f"Invalid default_size '{mod.get('default_size')}' for module {slug}"
        assert all(s in VALID_SIZES for s in mod.get('allowed_sizes', [])), \
            f"Invalid allowed_sizes for module {slug}"
        assert mod.get('load_strategy') in VALID_LOAD_STRATEGIES, \
            f"Invalid load_strategy '{mod.get('load_strategy')}' for module {slug}"
        assert callable(mod.get('provider')), \
            f"Provider for module {slug} is not callable"

        # Pre-compute whether provider accepts size/settings parameters
        sig = inspect.signature(mod['provider'])
        mod['_accepts_size'] = 'size' in sig.parameters
        mod['_accepts_settings'] = 'settings' in sig.parameters

_validate_registry()


def get_module_registry():
    """Return the full list of registered module descriptors."""
    return DASHBOARD_MODULES


def get_module_by_slug(slug):
    """Return a single module descriptor by slug, or None."""
    return _MODULE_LOOKUP.get(slug)


def get_valid_slugs():
    """Return set of all registered module slugs."""
    return set(_MODULE_LOOKUP)


def _get_preview_settings(slug):
    """Per-module settings overrides for preview rendering."""
    from django.utils import timezone
    if slug == 'trophy_visualizations':
        return {'year': str(timezone.now().year - 1)}
    if slug == 'badge_visualizations':
        return {'year': 'all'}
    return {}


def get_premium_preview_html(slug):
    """
    Get pre-rendered preview HTML for a premium module using the showcase profile.

    Renders the module template with real data from SHOWCASE_PROFILE_ID and caches
    the result for 24 hours. Used to show blurred previews to free users.
    """
    from django.template.loader import render_to_string
    from trophies.models import Profile

    cache_key = f'dashboard:preview:{slug}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    mod = get_module_by_slug(slug)
    if not mod or not mod.get('requires_premium'):
        return None

    try:
        profile = Profile.objects.select_related('user').get(id=SHOWCASE_PROFILE_ID)
    except Profile.DoesNotExist:
        return None

    # Call provider directly with preview overrides (bypasses settings validation)
    overrides = _get_preview_settings(slug)
    provider_fn = mod['provider']
    try:
        sig = inspect.signature(provider_fn)
        if 'settings' in sig.parameters:
            # Merge defaults with overrides
            settings = {**mod.get('default_settings', {}), **overrides}
            data = provider_fn(profile, settings=settings)
        else:
            data = provider_fn(profile)
    except Exception:
        logger.exception("Preview provider for %s failed", slug)
        return None

    if not data:
        return None

    try:
        html = render_to_string(mod['template'], {'data': data})
    except Exception:
        logger.exception("Failed to render premium preview for %s", slug)
        return None

    cache.set(cache_key, html, 86400)  # 24 hours
    return html


# ---------------------------------------------------------------------------
# Size Helpers
# ---------------------------------------------------------------------------

def get_effective_size(module_descriptor, module_settings):
    """
    Return the effective size for a module, considering user overrides.

    Falls back to default_size if the user's chosen size is invalid
    or not in allowed_sizes.
    """
    slug = module_descriptor['slug']
    default = module_descriptor.get('default_size', 'medium')
    allowed = module_descriptor.get('allowed_sizes', list(VALID_SIZES))

    user_settings = module_settings.get(slug, {}) if module_settings else {}
    user_size = user_settings.get('size')

    if user_size and user_size in allowed:
        return user_size
    return default


def get_size_grid_class(size):
    """Return the CSS grid class string for a given size."""
    return SIZE_GRID_CLASSES.get(size, SIZE_GRID_CLASSES['medium'])


def get_effective_settings(module_descriptor, module_settings):
    """
    Return effective settings for a module, merging user overrides with defaults.

    Each setting in configurable_settings has a 'default' value. User overrides
    from module_settings[slug] take precedence when present and valid.
    """
    slug = module_descriptor['slug']
    defaults = module_descriptor.get('default_settings', {})
    user_settings = module_settings.get(slug, {}) if module_settings else {}

    # Start with defaults, overlay user overrides
    effective = {**defaults}
    configurable = module_descriptor.get('configurable_settings', [])
    configurable_keys = {s['key'] for s in configurable}

    # Pass through overrides for keys in default_settings but not in configurable_settings
    # (e.g., inline year selector sends values that aren't in the customize panel options)
    for key, val in user_settings.items():
        if key in defaults and key not in configurable_keys:
            effective[key] = val

    for setting in configurable:
        key = setting['key']
        if key in user_settings:
            user_val = user_settings[key]
            # Validate select options
            if setting['type'] == 'select':
                valid_values = [opt['value'] for opt in setting.get('options', [])]
                if user_val in valid_values:
                    effective[key] = user_val
            elif setting['type'] == 'toggle':
                if isinstance(user_val, bool):
                    effective[key] = user_val
        elif key not in effective:
            effective[key] = setting['default']

    return effective


def validate_module_size(slug, size):
    """
    Validate that a size is allowed for a given module.
    Returns (is_valid, error_message).
    """
    mod = get_module_by_slug(slug)
    if not mod:
        return False, f'Unknown module: {slug}'

    allowed = mod.get('allowed_sizes', list(VALID_SIZES))
    if size not in allowed:
        return False, f'Size "{size}" not allowed for {slug}. Allowed: {", ".join(allowed)}'

    return True, None


# ---------------------------------------------------------------------------
# Module Ordering & Filtering
# ---------------------------------------------------------------------------

def get_ordered_modules(config, is_premium):
    """
    Build the display-ordered list of modules for a user.

    Applies premium filtering, user ordering, and visibility preferences.
    Each returned module dict is enriched with 'effective_size' and 'grid_class'.
    New modules not yet in the user's saved order are appended at their
    default_order position.
    """
    # Filter out premium modules for free users
    available = [
        mod for mod in DASHBOARD_MODULES
        if not mod['requires_premium'] or is_premium
    ]

    # Filter out hidden modules
    hidden = set(config.hidden_modules) if config.hidden_modules else set()
    visible = [mod for mod in available if mod['slug'] not in hidden]

    # Apply custom ordering (premium only)
    if is_premium and config.module_order:
        mod_lookup = {mod['slug']: mod for mod in visible}
        ordered = []

        for slug in config.module_order:
            if slug in mod_lookup:
                ordered.append(mod_lookup.pop(slug))

        remaining = sorted(mod_lookup.values(), key=lambda m: m['default_order'])
        ordered.extend(remaining)
    else:
        ordered = sorted(visible, key=lambda m: m['default_order'])

    # Enrich each module with effective size and grid class
    module_settings = config.module_settings or {}
    enriched = []
    for mod in ordered:
        enriched_mod = {**mod}
        size = get_effective_size(mod, module_settings)
        enriched_mod['effective_size'] = size
        enriched_mod['grid_class'] = get_size_grid_class(size)
        enriched.append(enriched_mod)

    return enriched


def get_tabs_for_customize(config, is_premium):
    """
    Return all modules grouped by their effective tab assignment for the customize panel.

    Unlike get_dashboard_tabs() which filters hidden modules, this includes ALL modules
    (with is_hidden flag) so users can toggle them back on. Returns an ordered list of
    tab dicts matching the tab bar order.
    """
    tab_config = config.tab_config or {}
    hidden = set(config.hidden_modules) if config.hidden_modules else set()
    module_settings = config.module_settings or {}

    # Build module-to-tab mapping (same logic as get_dashboard_tabs)
    module_tab_map = {mod['slug']: mod['category'] for mod in DASHBOARD_MODULES}
    if is_premium:
        overrides = tab_config.get('module_tab_overrides', {})
        if isinstance(overrides, dict):
            for slug, tab_slug in overrides.items():
                if slug in module_tab_map:
                    module_tab_map[slug] = tab_slug

    # Build tab structures
    tab_order = tab_config.get('tab_order', DEFAULT_TAB_ORDER) if is_premium else DEFAULT_TAB_ORDER
    tabs = {}
    for cat_slug in DEFAULT_TAB_ORDER:
        tabs[cat_slug] = {
            'slug': cat_slug,
            'name': CATEGORY_DISPLAY_NAMES.get(cat_slug, cat_slug.replace('_', ' ').title()),
            'short_name': CATEGORY_SHORT_NAMES.get(cat_slug),
            'icon': TAB_ICONS.get(cat_slug, 'star'),
            'is_custom': False,
            'modules': [],
        }

    # Add custom tabs
    if is_premium:
        custom_tabs = tab_config.get('custom_tabs', {})
        if isinstance(custom_tabs, dict):
            for tab_slug, tab_data in custom_tabs.items():
                if tab_slug in DEFAULT_TAB_ORDER:
                    continue
                if not isinstance(tab_data, dict):
                    logger.warning("Skipping malformed custom tab %s (expected dict, got %s)", tab_slug, type(tab_data).__name__)
                    continue
                tabs[tab_slug] = {
                    'slug': tab_slug,
                    'name': str(tab_data.get('name', 'Custom Tab'))[:20],
                    'short_name': str(tab_data.get('name', 'Custom Tab'))[:20],
                    'icon': tab_data.get('icon', 'star') if tab_data.get('icon') in VALID_TAB_ICONS else 'star',
                    'is_custom': True,
                    'modules': [],
                }

    # Assign ALL modules to tabs (including hidden, for toggle-back)
    for mod in DASHBOARD_MODULES:
        is_locked = mod['requires_premium'] and not is_premium
        size = get_effective_size(mod, module_settings)
        settings = get_effective_settings(mod, module_settings if is_premium else {})
        entry = {
            **mod,
            'is_hidden': mod['slug'] in hidden,
            'is_locked': is_locked,
            'effective_size': size,
            'effective_settings': settings,
        }

        target_tab = module_tab_map.get(mod['slug'], mod['category'])
        if target_tab not in tabs:
            target_tab = mod['category']
        tabs[target_tab]['modules'].append(entry)

    # Order modules within each tab
    if is_premium and config.module_order:
        order_lookup = {slug: i for i, slug in enumerate(config.module_order)}
        for tab in tabs.values():
            tab['modules'].sort(key=lambda m: order_lookup.get(m['slug'], m['default_order']))
    else:
        for tab in tabs.values():
            tab['modules'].sort(key=lambda m: m['default_order'])

    # Build ordered list
    ordered = []
    seen = set()
    for tab_slug in tab_order:
        if tab_slug in tabs and tab_slug not in seen:
            seen.add(tab_slug)
            ordered.append(tabs[tab_slug])
    for tab_slug in DEFAULT_TAB_ORDER:
        if tab_slug not in seen:
            seen.add(tab_slug)
            ordered.append(tabs[tab_slug])
    # Append any custom tabs not in tab_order
    for tab_slug, tab_data in tabs.items():
        if tab_slug not in seen:
            ordered.append(tab_data)

    return ordered


CATEGORY_DISPLAY_NAMES = {
    'at_a_glance': 'At a Glance',
    'progress': 'Progress & Challenges',
    'badges': 'Badges & Achievements',
    'share': 'Share & Export',
    'highlights': 'Highlights & History',
    'premium': 'Premium',
}

# Short names for tab bar (fits without scrolling)
CATEGORY_SHORT_NAMES = {
    'at_a_glance': 'At a Glance',
    'progress': 'Progress',
    'badges': 'Badges',
    'share': 'Share',
    'highlights': 'Highlights',
    'premium': 'Premium',
}

# Default tab order and icons (SVG path data for inline rendering)
DEFAULT_TAB_ORDER = ['premium', 'at_a_glance', 'progress', 'badges', 'highlights', 'share']

TAB_ICONS = {
    'at_a_glance': 'trophy_cup',
    'progress': 'chart',
    'badges': 'medal',
    'share': 'share',
    'highlights': 'star',
    'premium': 'crown',
}

VALID_TAB_ICONS = {'trophy_cup', 'target', 'chart', 'medal', 'users', 'share', 'star', 'crown', 'heart'}


def _category_display_name(key):
    return CATEGORY_DISPLAY_NAMES.get(key, key.replace('_', ' ').title())


def get_dashboard_tabs(config, is_premium):
    """
    Build the tab structure for the dashboard.

    Returns an ordered list of tab dicts, each containing its modules.
    Default tabs come from CATEGORY_DISPLAY_NAMES. Premium users can have
    custom tabs and module overrides stored in config.tab_config.
    """
    tab_config = config.tab_config or {}
    active_tab = tab_config.get('active_tab', 'at_a_glance')
    hidden = set(config.hidden_modules) if config.hidden_modules else set()
    module_settings = config.module_settings or {}

    # Build module-to-tab mapping
    # Start with defaults from module descriptors
    module_tab_map = {mod['slug']: mod['category'] for mod in DASHBOARD_MODULES}

    # Build default tabs
    tab_order = tab_config.get('tab_order', DEFAULT_TAB_ORDER) if is_premium else DEFAULT_TAB_ORDER
    tabs = {}
    for cat_slug in DEFAULT_TAB_ORDER:
        tabs[cat_slug] = {
            'slug': cat_slug,
            'name': CATEGORY_DISPLAY_NAMES.get(cat_slug, cat_slug.replace('_', ' ').title()),
            'short_name': CATEGORY_SHORT_NAMES.get(cat_slug),
            'icon': TAB_ICONS.get(cat_slug, 'star'),
            'is_custom': False,
            'modules': [],
        }

    # Add custom tabs (premium only, validated)
    if is_premium:
        custom_tabs = tab_config.get('custom_tabs', {})
        if isinstance(custom_tabs, dict):
            for tab_slug, tab_data in custom_tabs.items():
                # Prevent collision with default tab slugs
                if tab_slug in DEFAULT_TAB_ORDER:
                    continue
                if not isinstance(tab_data, dict):
                    continue
                tabs[tab_slug] = {
                    'slug': tab_slug,
                    'name': str(tab_data.get('name', 'Custom Tab'))[:50],
                    'icon': tab_data.get('icon', 'star') if tab_data.get('icon') in VALID_TAB_ICONS else 'star',
                    'is_custom': True,
                    'modules': [],
                }

    # Apply premium tab overrides (validated: target tab must exist)
    if is_premium:
        overrides = tab_config.get('module_tab_overrides', {})
        if isinstance(overrides, dict):
            for slug, tab_slug in overrides.items():
                if slug in module_tab_map and tab_slug in tabs:
                    module_tab_map[slug] = tab_slug

    # Assign modules to tabs
    for mod in DASHBOARD_MODULES:
        slug = mod['slug']
        is_preview = mod['requires_premium'] and not is_premium

        # Skip hidden modules (but never skip premium previews)
        if slug in hidden and not is_preview:
            continue

        target_tab = module_tab_map.get(slug, mod['category'])
        if target_tab not in tabs:
            target_tab = mod['category']  # Fallback to default category

        # Enrich module
        size = get_effective_size(mod, module_settings)
        settings = get_effective_settings(mod, module_settings if is_premium else {})
        enriched = {
            **mod,
            'effective_size': size,
            'grid_class': get_size_grid_class(size),
            'effective_settings': settings,
            'is_preview': is_preview,
        }

        # Force server-rendered for previews (no lazy load)
        if is_preview:
            enriched['load_strategy'] = 'server'

        tabs[target_tab]['modules'].append(enriched)

    # Apply custom module ordering within each tab
    if is_premium and config.module_order:
        order_lookup = {slug: i for i, slug in enumerate(config.module_order)}
        for tab in tabs.values():
            tab['modules'].sort(key=lambda m: order_lookup.get(m['slug'], m['default_order']))
    else:
        for tab in tabs.values():
            tab['modules'].sort(key=lambda m: m['default_order'])

    # Build ordered tab list
    ordered_tabs = []
    seen = set()
    for tab_slug in tab_order:
        if tab_slug in tabs and tab_slug not in seen:
            seen.add(tab_slug)
            ordered_tabs.append(tabs[tab_slug])

    # Append any tabs not in tab_order (new default tabs or custom tabs)
    for tab_slug in DEFAULT_TAB_ORDER:
        if tab_slug not in seen:
            seen.add(tab_slug)
            ordered_tabs.append(tabs[tab_slug])
    for tab_slug in tabs:
        if tab_slug not in seen:
            seen.add(tab_slug)
            ordered_tabs.append(tabs[tab_slug])

    # Set active tab
    active_found = False
    for tab in ordered_tabs:
        tab['is_active'] = (tab['slug'] == active_tab)
        if tab['is_active']:
            active_found = True

    # Fallback: if active tab not found, activate the first one
    if not active_found and ordered_tabs:
        ordered_tabs[0]['is_active'] = True

    return ordered_tabs


# ---------------------------------------------------------------------------
# Data Providers: Server-Rendered Modules
# ---------------------------------------------------------------------------

def get_server_module_data(profile, modules):
    """
    Batch-fetch context dicts for all server-rendered modules.

    Returns {slug: context_dict} for modules with load_strategy == 'server'.
    """
    data = {}
    for mod in modules:
        if mod['load_strategy'] != 'server':
            continue
        provider_fn = mod['provider']
        try:
            data[mod['slug']] = provider_fn(profile)
        except Exception:
            logger.exception("Dashboard provider for %s failed for profile %s",
                             mod['slug'], profile.id)
            data[mod['slug']] = {'error': True}
    return data


def get_lazy_module_data(profile, slug, size=None, module_settings=None):
    """
    Fetch context for a single lazy-loaded module.

    Checks Django cache first; falls back to provider on miss.
    Settings and size are passed to providers that accept them.
    Cache keys include a settings hash so different configs are cached independently.
    Returns the context dict or None if the module doesn't exist.
    """
    mod = get_module_by_slug(slug)
    if not mod:
        return None

    effective_size = size or mod.get('default_size', 'medium')
    effective = get_effective_settings(mod, module_settings or {})

    # Build cache key that includes settings so different configs cache separately
    import hashlib
    settings_hash = hashlib.md5(str(sorted(effective.items())).encode()).hexdigest()[:8]
    cache_key = _module_cache_key(slug, profile.id, settings_hash)
    ttl = mod.get('cache_ttl', DEFAULT_CACHE_TTL)

    if ttl > 0:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    provider_fn = mod['provider']
    try:
        if mod.get('_accepts_settings'):
            data = provider_fn(profile, settings=effective)
        elif mod.get('_accepts_size'):
            data = provider_fn(profile, size=effective_size)
        else:
            data = provider_fn(profile)
    except Exception:
        logger.exception("Dashboard provider for %s failed for profile %s",
                         slug, profile.id)
        return {'error': True}

    if ttl > 0:
        try:
            cache.set(cache_key, data, ttl)
            # Track this key so invalidate_dashboard_cache can delete it directly
            _track_cache_key(profile.id, cache_key, ttl)
        except Exception:
            logger.debug("Could not cache dashboard module %s (non-serializable data)", slug)

    return data


# ---------------------------------------------------------------------------
# Cache Helpers
# ---------------------------------------------------------------------------

def _module_cache_key(slug, profile_id, size=None):
    if size:
        return f"dashboard:mod:{slug}:{profile_id}:{size}"
    return f"dashboard:mod:{slug}:{profile_id}"


def _cache_key_tracker(profile_id):
    """Key that stores the set of active dashboard cache keys for a profile."""
    return f"dashboard:active_keys:{profile_id}"


def _track_cache_key(profile_id, cache_key, ttl):
    """Register a cache key so invalidate_dashboard_cache can find it."""
    tracker_key = _cache_key_tracker(profile_id)
    try:
        existing = cache.get(tracker_key) or set()
        existing.add(cache_key)
        # TTL slightly longer than the longest module cache to outlive all entries
        cache.set(tracker_key, existing, max(ttl + 60, 3660))
    except Exception:
        pass  # Non-critical: worst case, stale cache until natural expiry


def invalidate_dashboard_cache(profile_id):
    """Delete all dashboard module cache keys for a profile.

    Uses a tracked key set for fast O(1) invalidation. Falls back to
    bare keys (without settings hash) as a safety net.
    Called frequently (every sync, badge check, etc.) so must be fast.
    """
    tracker_key = _cache_key_tracker(profile_id)
    tracked_keys = cache.get(tracker_key) or set()

    keys_to_delete = list(tracked_keys)
    keys_to_delete.append(tracker_key)  # Clear the tracker itself

    # Also include bare keys (without settings hash) as a safety net
    import hashlib
    for mod in DASHBOARD_MODULES:
        if mod.get('cache_ttl', DEFAULT_CACHE_TTL) > 0:
            slug = mod['slug']
            keys_to_delete.append(_module_cache_key(slug, profile_id))
            # Default settings hash (most common variant)
            defaults = mod.get('default_settings', {})
            if defaults:
                settings_hash = hashlib.md5(str(sorted(defaults.items())).encode()).hexdigest()[:8]
                keys_to_delete.append(_module_cache_key(slug, profile_id, settings_hash))

    if keys_to_delete:
        cache.delete_many(keys_to_delete)


def force_flush_dashboard_cache(profile_id):
    """Force-delete ALL dashboard cache keys for a profile using pattern matching.

    Uses Redis SCAN via delete_pattern() per module, which is slower than
    invalidate_dashboard_cache but guaranteed to find all key variants.
    Only use from management commands, NOT from hot paths (sync, badge checks).
    """
    tracker_key = _cache_key_tracker(profile_id)
    cache.delete(tracker_key)

    for mod in DASHBOARD_MODULES:
        if mod.get('cache_ttl', DEFAULT_CACHE_TTL) > 0:
            slug = mod['slug']
            cache.delete(_module_cache_key(slug, profile_id))
            try:
                cache.delete_pattern(f"dashboard:mod:{slug}:{profile_id}:*")
            except Exception:
                pass
