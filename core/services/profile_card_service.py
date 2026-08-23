"""Data layer for the Profile Card -- the share image for a hunter's whole career.

The third sibling in the share-card family (plat card, recap card): same 1200x630 landscape
artifact, same ground / frame / identity-strip anatomy, but its subject is the HUNTER -- trophy
totals, the badge collection, and the Pursuer's standing -- where the plat card's subject is one
completion. It replaces the retired 2025 profile card, which was deleted with the dashboard; this
one is built on the new systems (grouping badges, Jobs/Contracts, marks) rather than revived.

Whale-safe by construction, which for a whole-career card is the entire game: every figure is a
Profile denorm (`trophy_snapshot` reads zero queries), a materialized read-model (held group-badge
rows), or a catalog-bounded career aggregate (`build_hero_context` -- ~25-row Job catalog).
Nothing here iterates trophies.

Ownership is structural: the card is built FROM a profile and only ever served to that profile's
own user (see the PNG endpoint), so unlike the plat card there is no eligibility predicate --
having a linked profile IS the eligibility.
"""
import logging

from trophies.models import EarnedTrophy, GroupBadge, UserGroupBadge
from trophies.services import career_service, profile_stats_service
from trophies.services.badge_detail_service import group_medallion_layers
from users.services.marks import mark_style

# One colour vocabulary across the family: the card renders with no stylesheet, and these maps are
# the hand-ported token hexes the plat card already keeps in sync with input.css. Imported, not
# copied -- a second copy is a second thing to drift.
from core.services.completion_card_service import DISCIPLINE_COLOURS, TIER_DISPLAY

logger = logging.getLogger(__name__)

#: How many recently-earned medallions the spine band shows. Each one is another image (or two)
#: to cache and base64 into the render; three reads as "a collection", four reads as a list.
RECENT_MEDALLION_CAP = 3


def _badge_summary(profile):
    """The hunter's badge standing: held / catalog counts plus their newest medallions.

    Counts LIVE editions only, matching the Collection gallery's summary (`catalog_total` is the
    same denominator it uses), so the card can never claim a badge the Collection wouldn't show.
    All reads are bounded by holdings, never by trophies.
    """
    from trophies.models import SeriesBadgeStanding
    from trophies.services.collection_service import closest_badge

    catalog_total = GroupBadge.objects.filter(is_live=True).count()
    held = UserGroupBadge.objects.filter(profile=profile, group_badge__is_live=True)
    earned = held.count()
    holo = held.filter(is_holo=True).count()

    # Chases still open: standings short of complete, minus series already held in ANY edition
    # (megamix badges are earned at min_count while progress_bp measures cleared/gating, so a held
    # series can sit under 10000 bp -- the same wrinkle closest_badge excludes held series for; and
    # like closest_badge's exclusion the held set is deliberately NOT live-filtered, so a retired
    # edition still counts as holding the series). The live-series gate mirrors closest_badge's
    # third exclusion: standings survive a series going dormant forever, and a curator smoke-testing
    # an unreleased series against real profiles must not put a phantom chase on their card. All
    # reads bounded by engagement, never trophies.
    held_slugs = set(
        UserGroupBadge.objects.filter(profile=profile)
        .values_list('group_badge__series__series_slug', flat=True)
    )
    chasing = (
        SeriesBadgeStanding.objects
        .filter(profile=profile, progress_bp__lt=10000,
                series_slug__in=GroupBadge.objects.filter(is_live=True).values('series__series_slug'))
        .exclude(series_slug__in=held_slugs)
        .count()
    )

    medallions = []
    recent = (
        held.select_related(
            'group_badge__series', 'group_badge__platform_group', 'group_badge__series__submitted_by',
        )
        .order_by('-earned_at')[:RECENT_MEDALLION_CAP]
    )
    for row in recent:
        _tier, layers, is_avatar = group_medallion_layers(row.group_badge)
        medallions.append({
            # SUBJECT ART ONLY, same call as the plat card's spine: the backdrop plate exists to
            # sit behind a circle mask, and the card shows the badge's own silhouette instead.
            'layers': layers[-1:],
            'is_avatar': is_avatar,
        })

    return {
        'earned': earned,
        'catalog_total': catalog_total,
        'pct': round(earned / catalog_total * 100) if catalog_total else 0,
        'holo': holo,
        'chasing': chasing,
        # The series they're nearest to finishing -- the Collection CTA's own read (or None).
        'closest': closest_badge(profile),
        'medallions': medallions,
    }


def get_card_data(profile):
    """Everything the Profile Card template needs, flat, in the family's shape."""
    career_ctx = career_service.build_hero_context(profile)
    hero = (career_ctx or {}).get('hero') or {}
    snap = profile_stats_service.trophy_snapshot(profile)

    # The ring arcs arrive with server-precomputed stroke-dash geometry (career_service._RING_C);
    # the card only has to attach each discipline's hex, because `var(--disc-*)` does not exist in
    # the renderer's stylesheet-free document.
    ring = [
        {**f, 'colour': DISCIPLINE_COLOURS.get(f.get('slug'), '#9da5b1')}
        for f in (hero.get('ring') or [])
    ]

    # The rarest and latest platinums, off the denormed FKs -- one PK lookup each, no scan, and
    # values_list means the ~30 KB IGDB blob can never ride along.
    def _plat_flavor(pk):
        if not pk:
            return None, None
        row = (
            EarnedTrophy.objects.filter(pk=pk)
            .values_list('trophy__game__concept__unified_title', 'trophy__game__title_name',
                         'trophy__trophy_earn_rate')
            .first()
        )
        if not row:
            return None, None
        unified, title_name, rate = row
        return (unified or title_name), rate

    rarest_name, rarest_rate = _plat_flavor(profile.rarest_plat_id)
    # For a hunter with one platinum (or a rarest that IS the latest) the two clauses would print
    # the same game twice; the latest clause simply drops, and its query with it.
    recent_name = None
    if profile.recent_plat_id and profile.recent_plat_id != profile.rarest_plat_id:
        recent_name, _ = _plat_flavor(profile.recent_plat_id)

    # All four tiers, unconditionally: this is a career, not one game's trophy list, so a zero is
    # a true statement about the hunter rather than a tier the game never defined.
    tier_totals = {
        'platinum': snap.get('total_plats', 0),
        'gold': snap.get('total_golds', 0),
        'silver': snap.get('total_silvers', 0),
        'bronze': snap.get('total_bronzes', 0),
    }
    tier_counts = [
        {'tier': tier, 'count': tier_totals[tier], 'colour': colour}
        for tier, colour in TIER_DISPLAY
    ]

    # Jobs actually touched (personal, unlike jobs['total'] which is the ~24-job catalog size).
    jobs = (career_ctx or {}).get('career') or {}
    jobs_played = sum(d.get('played', 0) for d in jobs.get('disciplines', []))

    return {
        'username': profile.display_psn_username or profile.psn_username,
        'mark': mark_style(profile.display_mark),
        'user_avatar_url': profile.avatar_url or '',
        # The title they're WEARING -- the same worn title every other surface leads with.
        'display_title': hero.get('active_title'),
        'total_games': snap.get('total_games', 0),

        'total_plats': snap.get('total_plats', 0),
        'total_completes': snap.get('total_completes', 0),
        'total_earned': snap.get('total_earned', 0),
        'trophy_level': snap.get('trophy_level', 0),
        'avg_progress': snap.get('avg_progress') or 0,
        'tier_counts': tier_counts,
        'rarest_rate': round(float(rarest_rate), 2) if rarest_rate is not None else None,
        'rarest_name': rarest_name,
        'recent_name': recent_name,

        'pursuer_level': hero.get('pursuer_level') or 0,
        'rank_label': (hero.get('pursuer_rank') or {}).get('label', ''),
        'ring': ring,
        'jobs_played': jobs_played,
        'jobs_total': jobs.get('total') or 0,
        'tiers_earned': (career_ctx or {}).get('tiers_earned') or 0,
        # Compact ("2.6M"); zeros render honestly -- the career row is a fixed shape.
        'career_xp_compact': (career_ctx or {}).get('total_xp_compact') or '0',

        'badges': _badge_summary(profile),
    }
