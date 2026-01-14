"""
Leaderboard service - Handles leaderboard computation and ranking.

This service manages leaderboard calculations for:
- Badge earners (sorted by earn date and tier)
- Progress tracking (trophy counts per badge series)
- Total progress across all badges
- Badge XP rankings
"""
from django.db.models import (
    Window, Q, Max, F, Count, Sum, When, Value, IntegerField,
    Case, OuterRef, Exists, Subquery
)
from django.db.models.functions import RowNumber, Coalesce


# XP Constants for badge progression
BRONZE_STAGE_XP = 250
SILVER_STAGE_XP = 75
GOLD_STAGE_XP = 250
PLAT_STAGE_XP = 75
BADGE_TIER_XP = 3000


def compute_earners_leaderboard(series_slug: str) -> list[dict]:
    """
    Compute earners leaderboard for a badge series.

    Returns users who have earned badges in this series, sorted by:
    1. Highest tier earned (descending)
    2. Earliest earn date for that tier (ascending)
    3. Username (for tiebreaker)

    Only one entry per user is included (their highest tier achievement).

    Args:
        series_slug: The badge series identifier (e.g., 'god-of-war')

    Returns:
        list[dict]: List of dicts with keys:
            - rank: int - Position on leaderboard (1-indexed)
            - psn_username: str - Display username
            - earn_date: str - ISO format date or 'Unknown'
            - avatar_url: str - Profile avatar URL
            - flag: str - User's flag/region
            - highest_tier: int - Highest badge tier earned (1-4)
            - is_premium: bool - Premium user status
    """
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
    ).filter(row_number=1).order_by(
        F('badge__tier').desc(), 'earned_at', 'profile__display_psn_username'
    )

    return [{
        'rank': rank + 1,
        'psn_username': earner.profile.display_psn_username,
        'earn_date': earner.earned_at.isoformat() if earner.earned_at else 'Unknown',
        'avatar_url': earner.profile.avatar_url,
        'flag': earner.profile.flag,
        'highest_tier': earner.badge.tier,
        'is_premium': earner.profile.user_is_premium,
    } for rank, earner in enumerate(earners)]


def compute_progress_leaderboard(series_slug: str) -> list[dict]:
    """
    Compute progress leaderboard for a specific badge series.

    Returns users sorted by trophy counts earned within games associated
    with the badge series. Ranking order:
    1. Platinum trophies (descending)
    2. Gold trophies (descending)
    3. Silver trophies (descending)
    4. Bronze trophies (descending)
    5. Most recent trophy date (ascending - earlier is better)

    Args:
        series_slug: The badge series identifier

    Returns:
        list[dict]: List of dicts with keys:
            - rank: int - Position on leaderboard
            - psn_username: str - Display username
            - flag: str - User's flag/region
            - avatar_url: str - Profile avatar URL
            - trophy_totals: dict - Trophy counts by type
                - plats: int
                - golds: int
                - silvers: int
                - bronzes: int
            - last_earned_date: str - ISO format date or 'Unknown'
            - is_premium: bool - Premium user status
    """
    from trophies.models import Game, Concept, Stage, Profile, EarnedTrophy, UserBadgeProgress

    # Get all games associated with this badge series
    stages = Stage.objects.filter(series_slug=series_slug)
    concepts = Concept.objects.filter(stages__in=stages).distinct()
    games = Game.objects.filter(concept__in=concepts).distinct()

    # Build subqueries for filtering
    badge_sub = UserBadgeProgress.objects.filter(
        profile=OuterRef('pk'), badge__series_slug=series_slug
    )
    trophy_sub = EarnedTrophy.objects.filter(
        profile=OuterRef('pk'), trophy__game__in=games, earned=True
    )

    # Query profiles with progress in this series
    earners = Profile.objects.filter(
        Q(is_linked=True) & (Exists(badge_sub) | Exists(trophy_sub))
    ).annotate(
        plats=Count(
            'earned_trophy_entries__id',
            filter=Q(
                earned_trophy_entries__earned=True,
                earned_trophy_entries__trophy__game__in=games,
                earned_trophy_entries__trophy__trophy_type='platinum'
            )
        ),
        golds=Count(
            'earned_trophy_entries__id',
            filter=Q(
                earned_trophy_entries__earned=True,
                earned_trophy_entries__trophy__game__in=games,
                earned_trophy_entries__trophy__trophy_type='gold'
            )
        ),
        silvers=Count(
            'earned_trophy_entries__id',
            filter=Q(
                earned_trophy_entries__earned=True,
                earned_trophy_entries__trophy__game__in=games,
                earned_trophy_entries__trophy__trophy_type='silver'
            )
        ),
        bronzes=Count(
            'earned_trophy_entries__id',
            filter=Q(
                earned_trophy_entries__earned=True,
                earned_trophy_entries__trophy__game__in=games,
                earned_trophy_entries__trophy__trophy_type='bronze'
            )
        ),
        max_earn_date=Max(
            'earned_trophy_entries__earned_date_time',
            filter=Q(
                earned_trophy_entries__earned=True,
                earned_trophy_entries__trophy__game__in=games
            )
        )
    ).order_by(
        '-plats', '-golds', '-silvers', '-bronzes', 'max_earn_date'
    ).only(
        'display_psn_username', 'flag', 'avatar_url', 'user_is_premium'
    )

    return [{
        'rank': rank + 1,
        'psn_username': earner.display_psn_username,
        'flag': earner.flag,
        'avatar_url': earner.avatar_url,
        'trophy_totals': {
            'plats': earner.plats,
            'golds': earner.golds,
            'silvers': earner.silvers,
            'bronzes': earner.bronzes,
        },
        'last_earned_date': earner.max_earn_date.isoformat() if earner.max_earn_date else 'Unknown',
        'is_premium': earner.user_is_premium,
    } for rank, earner in enumerate(earners)]


def compute_total_progress_leaderboard() -> list[dict]:
    """
    Compute overall progress leaderboard across all badge series.

    Returns users sorted by total trophy counts earned within all badge-related
    games. Uses same sorting as compute_progress_leaderboard but across all series.

    Returns:
        list[dict]: List of dicts with same structure as compute_progress_leaderboard
    """
    from trophies.models import Game, Concept, Stage, Profile, EarnedTrophy

    # Get all games associated with any badge
    stages = Stage.objects.all()
    concepts = Concept.objects.filter(stages__in=stages).distinct()
    games = Game.objects.filter(concept__in=concepts).distinct()

    trophy_sub = EarnedTrophy.objects.filter(
        profile=OuterRef('pk'), trophy__game__in=games, earned=True
    )

    earners = Profile.objects.filter(
        Q(is_linked=True) & Exists(trophy_sub)
    ).annotate(
        plats=Count(
            'earned_trophy_entries__id',
            filter=Q(
                earned_trophy_entries__earned=True,
                earned_trophy_entries__trophy__game__in=games,
                earned_trophy_entries__trophy__trophy_type='platinum'
            )
        ),
        golds=Count(
            'earned_trophy_entries__id',
            filter=Q(
                earned_trophy_entries__earned=True,
                earned_trophy_entries__trophy__game__in=games,
                earned_trophy_entries__trophy__trophy_type='gold'
            )
        ),
        silvers=Count(
            'earned_trophy_entries__id',
            filter=Q(
                earned_trophy_entries__earned=True,
                earned_trophy_entries__trophy__game__in=games,
                earned_trophy_entries__trophy__trophy_type='silver'
            )
        ),
        bronzes=Count(
            'earned_trophy_entries__id',
            filter=Q(
                earned_trophy_entries__earned=True,
                earned_trophy_entries__trophy__game__in=games,
                earned_trophy_entries__trophy__trophy_type='bronze'
            )
        ),
        max_earn_date=Max(
            'earned_trophy_entries__earned_date_time',
            filter=Q(
                earned_trophy_entries__earned=True,
                earned_trophy_entries__trophy__game__in=games
            )
        )
    ).order_by(
        '-plats', '-golds', '-silvers', '-bronzes', 'max_earn_date'
    ).only(
        'display_psn_username', 'flag', 'avatar_url', 'user_is_premium'
    )

    return [{
        'rank': rank + 1,
        'psn_username': earner.display_psn_username,
        'flag': earner.flag,
        'avatar_url': earner.avatar_url,
        'trophy_totals': {
            'plats': earner.plats,
            'golds': earner.golds,
            'silvers': earner.silvers,
            'bronzes': earner.bronzes,
        },
        'last_earned_date': earner.max_earn_date.isoformat() if earner.max_earn_date else 'Unknown',
        'is_premium': earner.user_is_premium,
    } for rank, earner in enumerate(earners)]


def compute_badge_xp_leaderboard() -> list[dict]:
    """
    Compute badge XP leaderboard.

    Calculates total XP for each user based on:
    1. Progress XP: XP from completed concepts per tier
       - Bronze tier: 250 XP per concept
       - Silver tier: 75 XP per concept
       - Gold tier: 250 XP per concept
       - Platinum tier: 75 XP per concept
    2. Badge completion XP: 3000 XP per fully earned badge

    Users are sorted by:
    1. Total XP (descending)
    2. Total badges earned (descending)
    3. Username (alphabetically)

    Returns:
        list[dict]: List of dicts with keys:
            - rank: int - Position on leaderboard
            - psn_username: str - Display username
            - flag: str - User's flag/region
            - avatar_url: str - Profile avatar URL
            - is_premium: bool - Premium user status
            - total_xp: int - Combined progress XP + badge XP
            - total_badges: int - Count of fully earned badges
    """
    from trophies.models import Profile, UserBadgeProgress

    # Check if user has any progress
    progress_sub = UserBadgeProgress.objects.filter(profile=OuterRef('pk'))

    # Calculate progress XP based on tier multipliers
    progress_qs = UserBadgeProgress.objects.filter(
        profile=OuterRef('pk')
    ).values('profile').annotate(
        pxp=Sum(
            Case(
                When(badge__tier=1, then=F('completed_concepts') * Value(BRONZE_STAGE_XP)),
                When(badge__tier=2, then=F('completed_concepts') * Value(SILVER_STAGE_XP)),
                When(badge__tier=3, then=F('completed_concepts') * Value(GOLD_STAGE_XP)),
                When(badge__tier=4, then=F('completed_concepts') * Value(PLAT_STAGE_XP)),
                default=Value(0),
                output_field=IntegerField()
            )
        )
    ).values('pxp')

    earners = Profile.objects.filter(
        Q(is_linked=True) & Exists(progress_sub)
    ).annotate(
        progress_xp=Coalesce(Subquery(progress_qs[:1]), 0),
        badge_count=Count('badges', distinct=True),
        total_xp=F('progress_xp') + F('badge_count') * Value(BADGE_TIER_XP)
    ).filter(
        total_xp__gt=0
    ).order_by('-total_xp', '-badge_count', 'display_psn_username')

    return [
        {
            'rank': rank + 1,
            'psn_username': earner.display_psn_username,
            'flag': earner.flag,
            'avatar_url': earner.avatar_url,
            'is_premium': earner.user_is_premium,
            'total_xp': earner.total_xp,
            'total_badges': earner.badge_count,
        } for rank, earner in enumerate(earners)
    ]
