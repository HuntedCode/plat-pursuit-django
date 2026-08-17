from django.db.models import Count, Q, Sum
from django.utils import timezone
from datetime import timedelta
from trophies.models import (
    Profile, EarnedTrophy, Game, Trophy, Stage, Concept,
    BadgeSeries, GroupBadge, UserGroupBadge, SeriesBadgeStanding,
)
from trophies.services.badge_xp import XP_PER_STAGE, XP_BADGE_COMPLETION_BONUS

def compute_community_stats():
    now = timezone.now()
    week_ago = now - timedelta(days=7)

    profile_counts = Profile.objects.aggregate(
        total=Count('id'),
        weekly=Count('id', filter=Q(created_at__gte=week_ago))
    )
    game_counts = Game.objects.aggregate(
        total=Count('id'),
        weekly=Count('id', filter=Q(created_at__gte=week_ago))
    )
    # Site-wide earned-trophy + platinum TOTALS read from the nightly denorms (Trophy.earned_count /
    # Game.plats_earned_count, kept fresh by recalc_earn_rates) rather than a full-table EarnedTrophy
    # aggregate. The live scan -- especially the platinum variant's join to Trophy -- scales with the
    # whale table and blew the statement timeout on the hourly heartbeat cron. These denorms are the same
    # source the game cards use, so the numbers stay consistent; they trail live by up to a nightly cycle,
    # which is fine for an hourly-cached community ribbon. WEEKLY counts stay live but are date-bounded, so
    # they ride the earned_date_time index (earned_trophy_earned_time_idx) instead of a full scan.
    trophy_counts = {
        'total': Trophy.objects.aggregate(t=Sum('earned_count'))['t'] or 0,
        'weekly': EarnedTrophy.objects.filter(earned=True, earned_date_time__gte=week_ago).count(),
    }
    platinum_counts = {
        'total': Game.objects.aggregate(t=Sum('plats_earned_count'))['t'] or 0,
        'weekly': EarnedTrophy.objects.filter(
            earned=True, trophy__trophy_type='platinum', earned_date_time__gte=week_ago
        ).count(),
    }

    # --- Badge figures, on the grouping-badge subsystem (2026-08 cutover) ---
    # A series counts as live when it has at least one live GroupBadge. `weekly` is new SERIES, so it reads
    # the series' own created_at, not the group badge's: adding an Ultra HD edition to an existing series is
    # not a new badge on the ribbon.
    live_series_slugs = list(
        BadgeSeries.objects.filter(group_badges__is_live=True)
        .distinct().values_list('series_slug', flat=True)
    )
    badge_series_counts = BadgeSeries.objects.filter(series_slug__in=live_series_slugs).aggregate(
        total=Count('id'),
        weekly=Count('id', filter=Q(created_at__gte=week_ago)),
    )

    # Total badge XP across all hunters, summed from the PER-SERIES standings restricted to live series.
    #
    # Not `Sum(ProfileBadgeStanding.total_xp)`, which is the obvious read and is wrong here:
    # `recompute_standing` re-sums that grand total from ALL of a profile's SeriesBadgeStanding rows, and
    # it only ever deletes rows for the series it was handed (always live ones). A series that goes
    # dormant therefore leaves its XP in every holder's total forever, so the ribbon would advertise XP
    # from badges nobody can see. Every other badge figure on this page gates on `is_live`; this is the
    # one that could not do it by filtering the same table.
    badge_xp = SeriesBadgeStanding.objects.filter(
        series_slug__in=live_series_slugs
    ).aggregate(total=Sum('xp'))

    # Unique concepts across all badge stages
    unique_concepts_total = Concept.objects.filter(
        stages__series_slug__isnull=False
    ).distinct().count()

    # Badges held across all hunters. Editions do NOT overlap (a group badge belongs to exactly one
    # platform group), so a flat row count is the honest total -- no per-user DISTINCT needed, unlike the
    # legacy tier model where four tiers of one series had to collapse to one.
    #
    # `weekly` reads created_at (when WE awarded it), NOT earned_at (when the hunter finished the games).
    # The legacy column meant award time, so repointing onto `earned_at` silently changed the question:
    # a series shipped today and awarded to hunters who platted it in 2019 would have reported zero.
    badges_earned_counts = {
        'total': UserGroupBadge.objects.filter(group_badge__is_live=True).count(),
        'weekly': UserGroupBadge.objects.filter(
            group_badge__is_live=True, created_at__gte=week_ago
        ).count(),
    }

    # --- Catalog stats: what the collection OFFERS, independent of who earned it ---
    # Stages per live series (stage 0 is the non-counting base stage). Counted once per SERIES: the stage
    # list is series-level, and every edition of a series works the same stages.
    stages_by_series = dict(
        Stage.objects.filter(series_slug__in=live_series_slugs)
        .exclude(stage_number=0)
        .values('series_slug')
        .annotate(n=Count('id'))
        .values_list('series_slug', 'n')
    )
    badge_stages_total = sum(stages_by_series.values())

    # Total earnable badge XP over the live catalog: per live GROUP badge (XP accrues per edition, so a
    # two-edition series is worth twice a one-edition series), stages * XP_PER_STAGE + the flat bonus.
    #
    # This is an UPPER BOUND, deliberately. True XP counts only GATING stages, and whether a stage gates
    # depends on per-game obtainability within that edition -- resolvable only by building the full
    # catalog, which is far too heavy for an hourly cron. A stage with no obtainable game in an edition
    # is over-counted here. The legacy figure approximated too (it trusted `required_stages`), and this
    # is a headline ribbon number, not an accounting figure.
    live_groups_by_series = dict(
        GroupBadge.objects.filter(is_live=True)
        .values('series__series_slug')
        .annotate(n=Count('id'))
        .values_list('series__series_slug', 'n')
    )
    # Both dicts are keyed by series and bounded by CATALOG size (hundreds), never by user data, so this
    # loop is not the profile-scoped Python aggregation the performance rule forbids.
    badge_earnable_xp = sum(
        editions * (stages_by_series.get(slug, 0) * XP_PER_STAGE + XP_BADGE_COMPLETION_BONUS)
        for slug, editions in live_groups_by_series.items()
    )

    return {
        'profiles': {
            'total': profile_counts['total'],
            'weekly': profile_counts['weekly'],
        },
        'trophies': {
            'total': trophy_counts['total'],
            'weekly': trophy_counts['weekly'],
        },
        'games': {
            'total': game_counts['total'],
            'weekly': game_counts['weekly'],
        },
        'platinums': {
            'total': platinum_counts['total'],
            'weekly': platinum_counts['weekly'],
        },
        'badge_series': {
            'total': badge_series_counts['total'],
            'weekly': badge_series_counts['weekly'],
        },
        'badge_stages': {
            'total': badge_stages_total,
        },
        'badge_earnable_xp': {
            'total': badge_earnable_xp,
        },
        'badge_xp': {
            'total': badge_xp['total'] or 0,
        },
        'concepts': {
            'total': unique_concepts_total,
        },
        'badges_earned': {
            'total': badges_earned_counts['total'],
            'weekly': badges_earned_counts['weekly'],
        },
    }
