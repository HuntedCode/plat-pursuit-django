"""Assembles the badge-detail page context from the NEW grouping-badge models + the sealed engine.

Replaces the legacy tier-based BadgeDetailView data layer. A series is now N parallel PlatformGroup badges
(Legacy HD / Ultra HD), not a 4-tier ladder. The viewer's per-group state + progress come from ONE whale-safe
engine pass (badge_orchestrator.evaluate_with_catalog); XP/progress are computed live from that same pass (so
they match the per-group numbers); live RANKS come from badge_leaderboards (stored standings).
"""
from dataclasses import dataclass
from typing import Optional

from django.templatetags.static import static

from trophies.models import UserGroupBadge, SeriesBadgeStanding, Game, ProfileGame
from trophies.services.badge_orchestrator import build_catalog, evaluate_with_catalog
from trophies.services.badge_xp import compute_series_standings, XP_PER_STAGE, XP_BADGE_COMPLETION_BONUS
from trophies.services.rating_service import RatingService
from trophies.services import badge_leaderboards as lb

# Default medallion metal per platform group until backing_key is set in admin (user pick 2026-08):
# Legacy HD -> gold, Ultra HD -> platinum.
_GROUP_BACKING = {'legacy-hd': 'gold', 'ultra-hd': 'platinum'}
# Legacy tier backdrops double as the group plates for now (bronze/silver/gold/platinum = 1..4).
_TIER_BACKDROP = {'bronze': 1, 'silver': 2, 'gold': 3, 'platinum': 4}


@dataclass
class GroupView:
    """One platform-group badge for the selector + its panel."""
    group_badge: object          # GroupBadge (id, set_number, effective_funded_by, ... for the template)
    platform_group: object       # PlatformGroup (name, key, medallion_shape, ...)
    art: dict                    # GroupBadge.art_layers()
    state: str                   # 'holo' | 'earned' | 'in_progress' | 'none'
    is_holo: bool
    earned_at: object            # datetime or None
    earners_rank: Optional[int]  # LIVE earners position (the medallion-back value), or None if not held
    earned_count: int
    rarity_pct: Optional[float]
    rarity_class: str
    rarity_rank: Optional[int]
    stages_cleared: int          # viewer's base-satisfied gating stages
    gating_count: int            # required gating stages for this group
    holo_satisfied_count: int
    progress_pct: int            # 0-100, for the Horizon bar
    segments: list               # one bool per gating stage (True = cleared) for the segmented Horizon
    # Badge-specific facts (this platform group only): games that route to it, its ratings, its XP.
    games_count: int
    avg_difficulty: Optional[float]
    avg_hours: Optional[float]
    xp_on_offer: int
    stages: list                 # the group's stage journey (list of stage dicts) -- see _group_journey
    frame: dict                  # medallion frame dict for components/badge_medallion.html


@dataclass
class BadgeDetail:
    series: object
    groups: list                 # [GroupView], ordered by platform sort_order
    has_multiple_groups: bool    # drives whether the selector renders
    viewer_state: str            # best across groups: holo > earned > in_progress > none
    series_xp: int               # live from this pass
    series_progress_pct: int     # live, furthest-along across groups
    series_rank: Optional[int]   # stored (relative to all earners), or None
    target_profile: object       # whose state is shown (may be None for anon)


def _contract_map(concept_ids) -> dict:
    """{concept_id: contract dict} for the game cards' contract band (name / slug / XP / jobs + family-blended
    band colours). Mirrors the old view's _game_contract. Concepts with no live contract are absent."""
    from trophies.services.contract_service import contract_by_concept_map, CONTRACT_XP_TOTAL
    out = {}
    for cid, contract in contract_by_concept_map(set(concept_ids), live_only=True).items():
        jobs = list(contract.jobs.all())
        disc = list(dict.fromkeys(j.discipline for j in jobs if j.discipline))
        band_bg, accent = '', ''
        if disc:
            stops = [f'color-mix(in oklab, var(--disc-{d}) 15%, var(--pp-bg-1))' for d in disc]
            if len(stops) == 1:
                stops *= 2   # a gradient needs >=2 stops; repeat -> a clean solid
            band_bg = f'linear-gradient(105deg, {", ".join(stops)})'
            accent = f'var(--disc-{disc[0]})'
        out[cid] = {
            'name': contract.name, 'slug': contract.slug,
            'xp': contract.xp_total_override or CONTRACT_XP_TOTAL,
            'jobs': jobs, 'band_bg': band_bg, 'accent': accent,
        }
    return out


def _group_stats(gb, result, catalog, ratings_map) -> dict:
    """Facts for THIS group's badge: the games that route to its platforms, their community difficulty/hours
    (cached per concept), and the XP on offer for this group. A concept counts when it has a game whose
    platforms intersect the group's -- so Legacy HD and Ultra HD get different games / ratings / XP."""
    platforms = set(gb.platform_group.platforms)
    concepts = set()
    for st in catalog['stages']:
        for c in st.concepts.all():
            if any(set(g.title_platform or []) & platforms for g in c.games.all()):
                concepts.add(c)
        for b in st.concept_bundles.all():
            for c in b.concepts.all():
                if any(set(g.title_platform or []) & platforms for g in c.games.all()):
                    concepts.add(c)

    diffs, hours = [], []
    for c in concepts:
        avg = ratings_map.get(c.id)
        if avg:
            if avg.get('avg_difficulty'):
                diffs.append(avg['avg_difficulty'])
            if avg.get('avg_hours'):
                hours.append(avg['avg_hours'])

    stage_count = sum(1 for st in catalog['stages'] if st.stage_number > 0)
    gating = result.gating_count if result else (gb.required_stages or stage_count)
    return {
        'games_count': len(concepts),
        'avg_difficulty': round(sum(diffs) / len(diffs), 1) if diffs else None,
        'avg_hours': round(sum(hours) / len(hours), 1) if hours else None,
        'xp_on_offer': gating * XP_PER_STAGE + XP_BADGE_COMPLETION_BONUS,
    }


_STATE_ORDER = {'none': 0, 'in_progress': 1, 'earned': 2, 'holo': 3}
# GroupView.state -> the medallion component's state vocabulary (it has no 'holo'/'none').
_MED_STATE = {'holo': 'earned', 'earned': 'earned', 'in_progress': 'in_progress', 'none': 'unearned'}


def _medallion_frame(gv: GroupView, series, target_profile) -> dict:
    """Map a GroupView onto the frame dict components/badge_medallion.html reads (reused unchanged, so the
    Collection/Case pages that still pass legacy frames keep working)."""
    art = gv.art
    pg = gv.platform_group
    tier = pg.backing_key or _GROUP_BACKING.get(pg.key, 'gold')   # data-tier drives the medallion coloring
    # Fall back to the tier backdrop plate when the group has no background_image set -- without a plate the
    # subject's white rim-light traces the bare art (the "white border"); the plate also gives the tier metal.
    backdrop = art['backdrop']
    if not backdrop and tier in _TIER_BACKDROP:
        backdrop = static(f"images/badges/backdrops/{_TIER_BACKDROP[tier]}_backdrop.png")
    layers = [url for url in (backdrop, art['main']) if url]
    owner = None
    if target_profile and gv.state in ('earned', 'holo'):
        owner = target_profile.display_psn_username or target_profile.psn_username
    return {
        'tier': tier,
        'state': _MED_STATE[gv.state],
        'art_layers': layers,
        'is_holographic': gv.is_holo,
        'series_name': series.name,
        'franchise': series.franchise.name if series.franchise_id else None,
        'collection': series.collection.name if series.collection_id else None,
        'developer': series.developer.name if series.developer_id else None,
        'stages_total': gv.gating_count,
        'stages_done': gv.stages_cleared,
        'progress_pct': gv.progress_pct,
        'segments': [i < gv.stages_cleared for i in range(gv.gating_count)],
        'set_number': gv.group_badge.set_number,
        'engraving_rank': gv.earners_rank,   # live earners position (was a permanent stamp in the legacy frame)
        'owner_name': owner,
        'badge_id': gv.group_badge.id,
    }


def _game_entry(game, profile_games, ratings_map, contract_map) -> dict:
    """A game card's data bundle: the game + the viewer's ProfileGame + community ratings + guide flag + its
    home contract. Shared by the stage grid and bundle members."""
    return {
        'game': game, 'pgame': profile_games.get(game.id),
        'ratings': ratings_map.get(game.concept_id),
        'has_guide': bool(game.concept and game.concept.guide_slug),
        'contract': contract_map.get(game.concept_id),
    }


def _stage_bundles(st, platforms, games_map, profile_games, ratings_map, contract_map) -> list:
    """Episodic bundles on a stage: a grouped set of concepts that TOGETHER satisfy the stage. Each member is a
    concept + its qualifying games for this group; member 'done' = a whole-game 100% on any of its games."""
    bundles = []
    for b in st.concept_bundles.all():
        members, completed = [], 0
        for concept in b.concepts.all():
            entries = [
                _game_entry(games_map[cg.id], profile_games, ratings_map, contract_map)
                for cg in concept.games.all()
                if cg.id in games_map and (set(games_map[cg.id].title_platform or []) & platforms)
            ]
            if not entries:
                continue
            done = any(e['pgame'] and e['pgame'].progress == 100 for e in entries)
            completed += 1 if done else 0
            members.append({'concept': concept, 'games': entries, 'done': done})
        if members:
            bundles.append({
                'label': b.label or 'Bundle', 'members': members,
                'completed_members': completed, 'total_members': len(members),
                'is_satisfied': completed == len(members),
            })
    return bundles


def _group_journey(gb, result, catalog, games_map, profile_games, ratings_map, contract_map) -> list:
    """The stage spine for THIS group: each stage that has a game routing to the group's platforms, with the
    qualifying games split obtainable vs delisted, plus per-game completion. Stage completion comes from the
    engine's per-stage result (base_satisfied). The first not-complete gating stage is flagged 'is_next'."""
    platforms = set(gb.platform_group.platforms)
    exclude_delisted = gb.platform_group.exclude_delisted
    stage_results = {sr.stage_number: sr for sr in (result.stages if result else [])}

    out = []
    for st in sorted(catalog['stages'], key=lambda s: s.stage_number):
        if st.stage_number <= 0:
            continue                          # stage 0 = tangential; not part of the journey
        obtainable, delisted = [], []
        for c in st.concepts.all():
            for cg in c.games.all():
                game = games_map.get(cg.id)
                if not game or not (set(game.title_platform or []) & platforms):
                    continue                  # routes to a different platform group
                entry = _game_entry(game, profile_games, ratings_map, contract_map)
                gates = game.is_obtainable and not (game.is_delisted and exclude_delisted)
                (obtainable if gates else delisted).append(entry)
        bundles = _stage_bundles(st, platforms, games_map, profile_games, ratings_map, contract_map)
        if not (obtainable or delisted or bundles):
            continue                          # nothing routes to this group in this stage -> not shown
        sr = stage_results.get(st.stage_number)
        any_progress = any(e['pgame'] and e['pgame'].progress for e in (obtainable + delisted))
        state = 'complete' if (sr and sr.base_satisfied) else ('partial' if any_progress else 'todo')
        out.append({
            'stage': st, 'obtainable_games': obtainable, 'delisted_games': delisted,
            'bundles': bundles, 'completion_state': state, 'is_next': False,
        })

    for s in out:                             # mark the first unfinished stage as "up next"
        if s['completion_state'] != 'complete':
            s['is_next'] = True
            break
    return out


def _group_view(gb, result, hold, target_profile, series, catalog, games_map, profile_games,
                ratings_map, contract_map) -> GroupView:
    is_holo = bool(hold and hold.is_holo)
    if hold:
        state = 'holo' if is_holo else 'earned'
    elif result and result.base_satisfied_count > 0:
        state = 'in_progress'
    else:
        state = 'none'
    stage_count = sum(1 for st in catalog['stages'] if st.stage_number > 0)
    gating = result.gating_count if result else (gb.required_stages or stage_count)
    cleared = result.base_satisfied_count if result else 0
    holo_cnt = result.holo_satisfied_count if result else 0
    # A live earners position only exists while the viewer currently holds the badge.
    rank = lb.earners_rank(target_profile.id, gb.id) if (hold and target_profile) else None
    stats = _group_stats(gb, result, catalog, ratings_map)
    gv = GroupView(
        group_badge=gb, platform_group=gb.platform_group, art=gb.art_layers(),
        state=state, is_holo=is_holo, earned_at=(hold.earned_at if hold else None),
        earners_rank=rank, earned_count=gb.earned_count,
        rarity_pct=gb.rarity_pct, rarity_class=gb.rarity_class, rarity_rank=gb.rarity_rank,
        stages_cleared=cleared, gating_count=gating, holo_satisfied_count=holo_cnt,
        progress_pct=(round(100 * cleared / gating) if gating else 0),
        segments=[i < cleared for i in range(gating)],
        games_count=stats['games_count'], avg_difficulty=stats['avg_difficulty'],
        avg_hours=stats['avg_hours'], xp_on_offer=stats['xp_on_offer'],
        stages=_group_journey(gb, result, catalog, games_map, profile_games, ratings_map, contract_map),
        frame={},
    )
    gv.frame = _medallion_frame(gv, series, target_profile)
    return gv


def get_badge_detail(series, target_profile) -> BadgeDetail:
    """Build the detail context for `series` (a BadgeSeries) as seen by `target_profile` (or None for anon)."""
    group_badges = list(
        series.group_badges.filter(is_live=True)
        .select_related('series', 'platform_group').order_by('platform_group__sort_order', 'id')
    )

    # Build the catalog even for anon -- the hero stats + stage journey need it.
    catalog = build_catalog(group_badges) if group_badges else None
    desired, holds = {}, {}
    games_map, profile_games, ratings_map, contract_map = {}, {}, {}, {}
    if catalog:
        # Full game rows (display fields) for the stage-journey cards -- the catalog's games are .only()'d.
        games_map = {
            g.id: g for g in Game.objects.filter(id__in=catalog['game_ids'])
            .select_related('concept', 'concept__igdb_match').defer('concept__igdb_match__raw_response')
        }
        # Community ratings (cached, per concept) + the game's home contract, built once for all cards.
        concepts = {g.concept for g in games_map.values() if g.concept_id}
        ratings_map = {c.id: RatingService.get_cached_community_averages(c) for c in concepts}
        contract_map = _contract_map({c.id for c in concepts})
        if target_profile:
            desired = evaluate_with_catalog(target_profile, catalog)
            holds = {
                u.group_badge_id: u
                for u in UserGroupBadge.objects.filter(profile=target_profile, group_badge__in=group_badges)
            }
            profile_games = {
                pg.game_id: pg
                for pg in ProfileGame.objects.filter(profile=target_profile, game_id__in=catalog['game_ids'])
            }

    groups = [_group_view(gb, desired.get(gb.id), holds.get(gb.id), target_profile, series, catalog,
                          games_map, profile_games, ratings_map, contract_map)
              for gb in group_badges]
    viewer_state = max((g.state for g in groups), key=lambda s: _STATE_ORDER[s], default='none')

    # Series XP + progress LIVE from this pass (matches the per-group numbers); rank is stored (relative).
    series_xp, series_progress_pct, series_rank = 0, 0, None
    if desired:
        results_by_series = {series.series_slug: [desired[gb.id] for gb in group_badges if gb.id in desired]}
        standing = compute_series_standings(results_by_series).get(series.series_slug)
        if standing:
            series_xp = standing.xp
            series_progress_pct = round(standing.progress_bp / 100)
        if target_profile and series_xp > 0:
            series_rank = lb.series_rank(series.series_slug, target_profile.id)

    return BadgeDetail(
        series=series, groups=groups, has_multiple_groups=len(groups) > 1,
        viewer_state=viewer_state, series_xp=series_xp, series_progress_pct=series_progress_pct,
        series_rank=series_rank, target_profile=target_profile,
    )
