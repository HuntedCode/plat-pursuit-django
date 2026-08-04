"""Batched, whale-safe assembly of badge-LIST cards for the grouping-badge system.

The list page (Gallery + Series views) renders many badges at once, so this NEVER calls the per-series
badge_detail_service.get_badge_detail (a heavy single-series pass -- catalog build, games/ratings/contracts,
stage journeys, per-group user stats). Instead it does a fixed handful of BULK queries for a page of group
badges -- pursuer counts for live rarity, the viewer's holds -- and builds each card's medallion frame from the
shared group_medallion_layers helper. Zero per-card queries, no engine pass; the query count does not grow with
the number of cards. Rarity is derived live (see badge_rarity); state on the list is binary (earned / not),
since per-badge in-progress is engine-derived and not whale-safe across a catalog.
"""
from django.db.models import Count

from trophies.models import SeriesBadgeStanding, UserGroupBadge
from trophies.services.badge_detail_service import group_medallion_layers
from trophies.services.badge_rarity import group_rarity


def _list_frame(gb, tier, layers, held, is_holo) -> dict:
    """A SHOWCASE medallion frame for a list card: the full-colour display piece (ownership is shown by a
    separate card marker, not by desaturating the art). No progress meter / engraving rank -- those need the
    engine / Redis and stay off the catalog wall. A mastered (holo) hold shimmers, as a personal flourish."""
    series = gb.series
    return {
        'tier': tier,
        'state': 'earned',                          # showcase full-colour; the card carries the owned marker
        'art_layers': layers,
        'is_holographic': bool(held and is_holo),
        'series_name': series.name,
        'franchise': series.franchise.name if series.franchise_id else None,
        'collection': series.collection.name if series.collection_id else None,
        'developer': series.developer.name if series.developer_id else None,
        'stages_total': gb.required_stages,
        'set_number': gb.set_number,
        'badge_id': gb.id,
    }


def build_list_cards(group_badges, profile) -> list:
    """Cards for a page of GroupBadges (already select_related'd on series + platform_group), in the SAME order.
    Each card: {group_badge, series, platform_group, frame, rarity_pct, rarity_class, earned, is_holo,
    earned_count}. Two bulk queries beyond the caller's queryset (pursuer counts + the viewer's holds) --
    whale-safe regardless of page size."""
    group_badges = list(group_badges)
    if not group_badges:
        return []

    # Live-rarity denominator: pursuers per series (SeriesBadgeStanding rows = xp>0 progress), one grouped query.
    slugs = {gb.series.series_slug for gb in group_badges}
    participants = dict(
        SeriesBadgeStanding.objects.filter(series_slug__in=slugs)
        .values('series_slug').annotate(n=Count('id'))
        .values_list('series_slug', 'n')
    )

    # The viewer's holds (earned + holo), one query. Anonymous -> no holds -> everything reads as not-earned.
    holds = {}
    if profile:
        holds = dict(
            UserGroupBadge.objects.filter(profile=profile, group_badge__in=group_badges)
            .values_list('group_badge_id', 'is_holo')
        )

    cards = []
    for gb in group_badges:
        held = gb.id in holds
        is_holo = bool(holds.get(gb.id, False))
        tier, layers = group_medallion_layers(gb)
        pct, cls = group_rarity(gb.earned_count, participants.get(gb.series.series_slug, 0))
        cards.append({
            'group_badge': gb,
            'series': gb.series,
            'platform_group': gb.platform_group,
            'frame': _list_frame(gb, tier, layers, held, is_holo),
            'rarity_pct': pct,
            'rarity_class': cls,
            'earned': held,
            'is_holo': is_holo,
            'earned_count': gb.earned_count,
        })
    return cards
