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
from core.services.completion_card_service import (
    DISCIPLINE_COLOURS, JOB_ICON_PATHS, TIER_DISPLAY,
)

logger = logging.getLogger(__name__)

#: How many recently-earned medallions the Collection band shows. Sized so the strip plus the
#: count/chase/catalog blocks fill the band's full width (his call: the bottom should fill);
#: each is one subject-art image to cache and base64 into the render, so the cap is a budget too.
RECENT_MEDALLION_CAP = 8


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
        # Holdings beyond the strip, for the "+N" chip that says the shelf continues.
        'more': max(0, earned - len(medallions)),
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

    # The rarest and latest platinums as MINI CARDS (cover art + name + figure), off the denormed
    # FKs -- one PK lookup each, no scan. The cover chain needs the Game + Concept + IGDBMatch in
    # hand (display_image_url is IGDB-first), so this is the one place the card selects a concept
    # join -- with the mandatory raw_response defer riding along.
    def _plat_mini(pk):
        if not pk:
            return None
        et = (
            EarnedTrophy.objects.filter(pk=pk)
            .select_related('trophy__game__concept__igdb_match')
            .defer('trophy__game__concept__igdb_match__raw_response')
            .first()
        )
        if not et or not et.trophy or not et.trophy.game:
            return None
        game = et.trophy.game
        concept = game.concept
        rate = et.trophy.trophy_earn_rate
        return {
            'name': (concept.unified_title if concept else '') or game.title_name,
            'cover_url': game.display_image_url or '',
            'earn_rate': round(float(rate), 2) if rate is not None else None,
            'earned_at': et.earned_date_time,
        }

    rarest_plat = _plat_mini(profile.rarest_plat_id)
    # For a hunter with one platinum (or a rarest that IS the latest) two cards would show the
    # same game twice; the latest card simply drops, and its query with it.
    latest_plat = None
    if profile.recent_plat_id and profile.recent_plat_id != profile.rarest_plat_id:
        latest_plat = _plat_mini(profile.recent_plat_id)

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

    # The highest-leveled job, with its real Lucide glyph in its discipline's colour -- the grid's
    # sixth slot. Only once real XP exists: the level-1 floor makes every untouched job "level 1",
    # and crowning an arbitrary one would be a claim the hunter never earned.
    top_job = None
    if jobs.get('total_xp'):
        tiles = [t for d in jobs.get('disciplines', []) for t in d.get('jobs', [])]
        started = [t for t in tiles if t.get('started')]
        if started:
            t = max(started, key=lambda t: t.get('level', 0))
            top_job = {
                'name': t.get('name', ''),
                'level': t.get('level', 0),
                'icon_paths': JOB_ICON_PATHS.get(t.get('icon', ''), ''),
                'colour': DISCIPLINE_COLOURS.get(t.get('disc_slug'), '#9da5b1'),
            }

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
        'total_unearned': snap.get('total_unearned', 0),
        'trophy_level': snap.get('trophy_level', 0),
        'avg_progress': snap.get('avg_progress') or 0,
        'tier_counts': tier_counts,
        'rarest_plat': rarest_plat,
        'latest_plat': latest_plat,

        'pursuer_level': hero.get('pursuer_level') or 0,
        'rank_label': (hero.get('pursuer_rank') or {}).get('label', ''),
        'ring': ring,
        'top_job': top_job,
        'jobs_played': jobs_played,
        'jobs_total': jobs.get('total') or 0,
        'tiers_earned': (career_ctx or {}).get('tiers_earned') or 0,
        # Compact ("2.6M"); zeros render honestly -- the career row is a fixed shape.
        'career_xp_compact': (career_ctx or {}).get('total_xp_compact') or '0',

        'badges': _badge_summary(profile),
    }
