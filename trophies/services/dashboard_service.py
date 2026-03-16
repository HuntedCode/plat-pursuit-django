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
    total_earned = profile.total_trophies - profile.total_unearned
    return {
        'total_plats': profile.total_plats,
        'total_golds': profile.total_golds,
        'total_silvers': profile.total_silvers,
        'total_bronzes': profile.total_bronzes,
        'total_trophies': profile.total_trophies,
        'total_earned': total_earned,
        'total_unearned': profile.total_unearned,
        'total_games': profile.total_games,
        'total_completes': profile.total_completes,
        'total_hiddens': profile.total_hiddens,
        'avg_progress': profile.avg_progress,
        'trophy_level': profile.trophy_level,
        'tier': profile.tier,
        'is_plus': profile.is_plus,
        'earn_rate': round(total_earned / profile.total_trophies * 100, 1) if profile.total_trophies else 0,
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
    """Badge XP stats and leaderboard position."""
    from trophies.models import ProfileGamification
    from trophies.services.leaderboard_service import compute_badge_xp_leaderboard

    try:
        gamification = profile.gamification
    except ProfileGamification.DoesNotExist:
        return {'has_stats': False}

    total_xp = gamification.total_badge_xp
    if total_xp == 0:
        return {'has_stats': False}

    leaderboard = compute_badge_xp_leaderboard()
    total_participants = len(leaderboard)

    # Find user's rank
    user_rank = None
    user_entry = None
    display_username = profile.display_psn_username
    for entry in leaderboard:
        if entry['psn_username'] == display_username:
            user_rank = entry['rank']
            user_entry = entry
            break

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

    # Build display entries: top 3 + neighborhood around user
    TOP_N = 5
    NEIGHBORHOOD = 2  # show 2 above and 2 below the user

    entries = []
    user_in_top = user_rank is not None and user_rank <= TOP_N
    show_gap = False

    if user_in_top:
        # User is in the top N: just show the top N
        for e in leaderboard[:TOP_N]:
            entries.append(_format_entry(e))
    elif user_rank is not None:
        # Show top 3 + gap + neighborhood (2 above + user + 2 below)
        for e in leaderboard[:3]:
            entries.append(_format_entry(e))

        user_idx = user_rank - 1  # 0-indexed
        neighborhood_start = max(3, user_idx - NEIGHBORHOOD)
        neighborhood_end = min(total_participants, user_idx + NEIGHBORHOOD + 1)

        show_gap = neighborhood_start > 3
        for e in leaderboard[neighborhood_start:neighborhood_end]:
            entries.append(_format_entry(e))
    else:
        # User not on leaderboard at all: show top 5
        for e in leaderboard[:TOP_N]:
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
    'community': 'Community',
    'highlights': 'Highlights & History',
    'premium': 'Premium',
}

# Short names for tab bar (fits without scrolling)
CATEGORY_SHORT_NAMES = {
    'at_a_glance': 'At a Glance',
    'progress': 'Progress',
    'badges': 'Badges',
    'community': 'Community',
    'highlights': 'Highlights',
    'premium': 'Premium',
}

# Default tab order and icons (SVG path data for inline rendering)
DEFAULT_TAB_ORDER = ['premium', 'at_a_glance', 'progress', 'badges', 'community', 'highlights']

TAB_ICONS = {
    'at_a_glance': 'trophy_cup',
    'progress': 'chart',
    'badges': 'medal',
    'community': 'users',
    'highlights': 'star',
    'premium': 'crown',
}

VALID_TAB_ICONS = {'trophy_cup', 'target', 'chart', 'medal', 'users', 'star', 'crown', 'heart'}


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
        # Skip premium modules for free users
        if mod['requires_premium'] and not is_premium:
            continue

        slug = mod['slug']
        # Skip hidden modules
        if slug in hidden:
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
        }

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


def invalidate_dashboard_cache(profile_id):
    """Delete all dashboard module cache keys for a profile (all sizes)."""
    keys_to_delete = []
    for mod in DASHBOARD_MODULES:
        if mod.get('cache_ttl', DEFAULT_CACHE_TTL) > 0:
            slug = mod['slug']
            for size in mod.get('allowed_sizes', list(VALID_SIZES)):
                keys_to_delete.append(_module_cache_key(slug, profile_id, size))
    if keys_to_delete:
        cache.delete_many(keys_to_delete)
