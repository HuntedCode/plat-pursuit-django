"""Assembles the badge-detail page context from the NEW grouping-badge models + the sealed engine.

Replaces the legacy tier-based BadgeDetailView data layer. A series is now N parallel PlatformGroup badges
(Legacy HD / Ultra HD), not a 4-tier ladder. The viewer's per-group state + progress come from ONE whale-safe
engine pass (badge_orchestrator.evaluate_with_catalog); XP/progress are computed live from that same pass (so
they match the per-group numbers); live RANKS come from badge_leaderboards (stored standings).
"""
import logging
from dataclasses import dataclass
from typing import Optional


from trophies.models import UserGroupBadge, SeriesBadgeStanding, Game, ProfileGame
from trophies.services.badge_engine import GroupInput, _gates as engine_gates, _qualifies as engine_qualifies
from trophies.services.badge_orchestrator import (
    build_catalog, evaluate_with_catalog, _bundle_state, _catalog_game_state,
)
from trophies.services.badge_xp import compute_series_standings, edition_display_state, XP_PER_STAGE, XP_BADGE_COMPLETION_BONUS
from trophies.services.badge_rarity import group_rarity
from trophies.services.rarity import community_size
from trophies.services.rating_service import RatingService
from trophies.services import badge_leaderboards as lb
from trophies.util_modules.assets import safe_static

logger = logging.getLogger(__name__)

# Default medallion metal per platform group until backing_key is set in admin (user pick 2026-08):
# Legacy HD -> gold, Ultra HD -> platinum.
_GROUP_BACKING = {'legacy-hd': 'gold', 'ultra-hd': 'platinum'}
# Legacy tier backdrops double as the group plates for now (bronze/silver/gold/platinum = 1..4).
_TIER_BACKDROP = {'bronze': 1, 'silver': 2, 'gold': 3, 'platinum': 4}


@dataclass
class GroupView:
    """One platform-group badge for the selector + its panel."""
    group_badge: object          # GroupBadge (id, effective_funded_by, ... for the template)
    platform_group: object       # PlatformGroup (name, key, medallion_shape, ...)
    art: dict                    # GroupBadge.art_layers()
    state: str                   # 'holo' | 'earned' | 'in_progress' | 'none'
    is_holo: bool
    earned_at: object            # datetime or None
    earners_rank: Optional[int]  # LIVE earners position (the medallion-back value), or None if not held
    earned_count: int
    rarity_pct: Optional[float]  # % of the series' pursuers who earned this group's badge (live-derived), or None
    rarity_class: str            # bucket of rarity_pct: common | uncommon | rare | mythic (or '' when pending)
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
    stages: list                 # the REQUIRED stage ladder, numbered 1..N -- see _group_journey
    #: In scope for this edition but no longer gating: every game of theirs on this edition's platforms is
    #: unobtainable (or delisted where the group excludes them). They do not count toward completion and
    #: carry no stage number, but they still PAY whoever cleared them, so they are shown in their own
    #: labelled section below the ladder rather than dropped.
    retired_stages: list
    #: Nothing left to gate -- the badge cannot be earned by anyone any more. Held rows are revoked and the
    #: page has to say so, because every "0 of 0 stages / N Points on offer" figure around it otherwise
    #: reads as a live chase.
    is_unearnable: bool
    user_stats: Optional[dict]   # the viewer's per-group My Stats (haul/play/games/stages); None for anon
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
    series_size: Optional[int]   # total profiles with a standing in this series (the rank's denominator)
    community_max_earned: int    # max earned_count across groups (scales the community band's earners bars)
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


def _group_stats(journey, ratings_map) -> dict:
    """Facts for THIS group's badge: how many games it puts on screen and their community difficulty/hours.

    Derived from the JOURNEY rather than re-walking the catalogue with a platform filter, so the headline
    numbers describe the cards a reader can actually see. The two came apart when the ladder stopped
    filtering by platform: "4 games" sat above a ladder showing 7, and the difficulty average described a
    different set than the one on screen.
    """
    concepts = set()
    for s in journey:
        for e in (s['obtainable_games'] + s['delisted_games']):
            if e['game'].concept_id:
                concepts.add(e['game'].concept_id)
        for bundle in s['bundles']:
            for member in bundle['members']:
                concepts.add(member['concept'].id)

    diffs, hours = [], []
    for cid in concepts:
        avg = ratings_map.get(cid)
        if avg:
            if avg.get('avg_difficulty'):
                diffs.append(avg['avg_difficulty'])
            if avg.get('avg_hours'):
                hours.append(avg['avg_hours'])

    return {
        'games_count': len(concepts),
        'avg_difficulty': round(sum(diffs) / len(diffs), 1) if diffs else None,
        'avg_hours': round(sum(hours) / len(hours), 1) if hours else None,
        # `xp_on_offer` moved to `_group_view`: it is priced off the IN-SCOPE stage count, which is exactly
        # what the journey produces (required + retired), and pricing it off `gating_count` under-reported
        # every badge holding a stage that stopped gating -- XP pays for those, the offer did not say so.
    }


_STATE_ORDER = {'none': 0, 'in_progress': 1, 'earned': 2, 'holo': 3}
# GroupView.state -> the medallion component's state vocabulary (it has no 'holo'/'none').
_MED_STATE = {'holo': 'earned', 'earned': 'earned', 'in_progress': 'in_progress', 'none': 'unearned'}


def group_medallion_layers(gb) -> tuple:
    """(tier, art_layers, is_avatar) for a group badge's medallion -- the backing metal + the composed layers
    with a backdrop-plate fallback (without a plate the subject's white rim-light traces the bare art), plus
    whether the subject is a user's avatar (a square/4:3 PSN image -> the template circle-masks + shrinks it).
    Shared by the detail hero frame and the batched list-card frames, so they compose identically."""
    art = gb.art_layers()
    pg = gb.platform_group
    tier = pg.backing_key or _GROUP_BACKING.get(pg.key, 'gold')   # data-tier drives the medallion coloring
    backdrop = art['backdrop']
    if not backdrop and tier in _TIER_BACKDROP:
        backdrop = _backdrop_url(_TIER_BACKDROP[tier])
    return tier, [url for url in (backdrop, art['main']) if url], art['is_avatar']


def _backdrop_url(n):
    """The tier backdrop plate, or None if it cannot be resolved. See util_modules.assets."""
    return safe_static(f"images/badges/backdrops/{n}_backdrop.png")


def _medallion_frame(gv: GroupView, series, target_profile) -> dict:
    """Map a GroupView onto the frame dict components/badge_medallion.html reads (reused unchanged, so the
    Collection/Case pages that still pass legacy frames keep working)."""
    tier, layers, is_avatar = group_medallion_layers(gv.group_badge)
    owner = None
    if target_profile and gv.state in ('earned', 'holo'):
        owner = target_profile.display_psn_username or target_profile.psn_username
    return {
        'tier': tier,
        'state': _MED_STATE[gv.state],
        'art_layers': layers,
        'is_avatar': is_avatar,
        'is_holographic': gv.is_holo,
        'series_name': series.name,
        'franchise': series.franchise.name if series.franchise_id else None,
        'collection': series.collection.name if series.collection_id else None,
        'developer': series.developer.name if series.developer_id else None,
        'stages_total': gv.gating_count,
        'stages_done': gv.stages_cleared,
        'progress_pct': gv.progress_pct,
        'segments': [i < gv.stages_cleared for i in range(gv.gating_count)],
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


def _stage_bundles(st, games_map, profile_games, ratings_map, contract_map) -> list:
    """Episodic bundles on a stage: a grouped set of concepts that TOGETHER satisfy the stage. Each member is a
    concept + ALL of its games; member 'done' = a whole-game 100% on any of them.

    Deliberately NOT filtered to this group's platforms (owner's call, 2026-09). Satisfaction is
    cross-platform now, so hiding the other versions produced the worst of both worlds: a bundle could be
    satisfied by lists the page refused to show, leaving a ticked bundle whose visible members all read
    unfinished. Showing every version is the honest picture; each card carries its own platform chips.

    Takes no `platforms`: nothing in here is platform-scoped any more. The GATING decision is the engine's
    (`_stage_units` + `engine_gates`), not this function's.
    """
    bundles = []
    for b in st.concept_bundles.all():
        members, completed = [], 0
        for concept in b.concepts.all():
            entries = [
                _game_entry(games_map[cg.id], profile_games, ratings_map, contract_map)
                for cg in concept.games.all()
                if cg.id in games_map
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


def _display_playable(game, platforms, exclude_delisted) -> bool:
    """Does this game go in the stage's main grid, or in the collapsed "unobtainable / delisted" list?

    A DISPLAY question, not the gating one. The delisted policy belongs to an EDITION, so it is applied
    only to games on that edition's platforms: a PS3 list that is alive on PS3 must not be filed under
    "unobtainable" on the Ultra HD page just because Ultra HD excludes delisted titles. Doing that put the
    game that satisfied a stage cross-platform inside a collapsed <details> while the grid above it showed
    only untouched cards -- the exact "ticked stage, nothing done" "bug this change set out to fix, one
    bucket deeper.
    """
    if not game.is_obtainable:
        return False
    on_edition = bool(set(game.title_platform or []) & platforms)
    return not (game.is_delisted and exclude_delisted and on_edition)


def _stage_units(st, games_map) -> list:
    """The exact `GameState` list the ENGINE evaluates for this stage -- plain games plus one synthetic
    unit per ConceptBundle, built by the orchestrator's own helpers.

    This exists so the page can answer "is this stage in scope / does it gate" by calling the engine's own
    predicates instead of re-deriving them. A signed-in viewer gets a `StageResult` and needs none of this;
    an ANONYMOUS visitor gets no evaluation at all, and the first attempt at filling that gap was a
    hand-written mirror of `_gates` + `_qualifies` + `_bundle_state`. It drifted on three separate bundle
    shapes -- a member with a delisted sibling, a member with no games, and members sharing no platform --
    and its own pinning test used no bundles at all, so nothing saw it. Two implementations of one rule is
    the bug; this removes the second one rather than testing it harder.

    Costs no queries: everything it walks is prefetched once by `build_catalog`.
    """
    units = []
    for c in st.concepts.all():
        for cg in c.games.all():
            game = games_map.get(cg.id)
            if game is not None:
                units.append(_catalog_game_state(game))
    for b in st.concept_bundles.all():
        bundle = _bundle_state(b, _catalog_game_state)
        if bundle is not None:
            units.append(bundle)
    return units


def _group_journey(gb, result, catalog, games_map, profile_games, ratings_map, contract_map) -> tuple:
    """The stage spine for THIS group, split into `(required, retired)`.

    IN SCOPE (the stage appears at all) is platform-based: a stage with no game on this group's platforms
    credits this badge nothing and is not shown here. Once in scope, EVERY game in the stage is listed
    regardless of platform (owner's call, 2026-09) -- satisfaction is cross-platform, so hiding the other
    versions left a ticked stage whose visible games were all untouched, which reads as a rendering bug.

    REQUIRED vs RETIRED is the gating question. A retired stage is in scope but no longer gates: its games
    on this group's platforms are unobtainable (or delisted where the group excludes them). It cannot be
    required of anyone any more, but it still PAYS whoever cleared it, so it is shown -- below the required
    ladder, in its own labelled section, and without a stage number, because a number reads as "step N of
    this badge" and these are explicitly not steps.

    Required stages carry `display_number`: a fresh 1..N over the REQUIRED ladder only, never `stage_number`.
    Authored numbers have gaps (a stage can be retired, renumbered, or authored out of order), and a badge
    that reads "Stage 1, Stage 2, Stage 5" invites the reader to hunt for the missing ones.
    """
    platforms = set(gb.platform_group.platforms)
    exclude_delisted = gb.platform_group.exclude_delisted
    stage_results = {sr.stage_number: sr for sr in (result.stages if result else [])}
    group_input = GroupInput(frozenset(platforms), exclude_delisted)

    required, retired = [], []
    for st in sorted(catalog['stages'], key=lambda s: s.stage_number):
        if st.stage_number <= 0:
            continue                          # stage 0 = tangential; not part of the journey
        # SCOPE and GATING come from the engine's own predicates over the engine's own units, so the page
        # and the evaluation can never disagree -- including on bundles, whose synthetic platforms are the
        # INTERSECTION of their members' (a bundle needs every member, so it is completable only where all
        # of them run).
        units = _stage_units(st, games_map)
        qualifying = [u for u in units if engine_qualifies(u, group_input)]
        if not qualifying:
            continue                          # nothing on this group's platforms -> credits it nothing
        gates = any(engine_gates(u, group_input) for u in qualifying)

        obtainable, delisted = [], []
        for c in st.concepts.all():
            for cg in c.games.all():
                game = games_map.get(cg.id)
                if not game:
                    continue
                entry = _game_entry(game, profile_games, ratings_map, contract_map)
                bucket = obtainable if _display_playable(game, platforms, exclude_delisted) else delisted
                bucket.append(entry)
        bundles = _stage_bundles(st, games_map, profile_games, ratings_map, contract_map)
        sr = stage_results.get(st.stage_number)
        any_progress = any(e['pgame'] and e['pgame'].progress for e in (obtainable + delisted))
        state = 'complete' if (sr and sr.base_satisfied) else ('partial' if any_progress else 'todo')
        entry = {
            'stage': st, 'obtainable_games': obtainable, 'delisted_games': delisted,
            'bundles': bundles, 'completion_state': state, 'is_next': False,
            'display_number': None,
            # Counted HERE, not in the template. `{{ a|length|add:b|length }}` is not valid Django -- a
            # filter argument cannot itself be filtered -- and it silently evaluated to 0, so every stage
            # header on the page read "0 games". Template arithmetic that fails quietly is exactly the kind
            # this belongs out of.
            'games_shown': len(obtainable) + len(delisted),
            # The stage's HOLO bar: a game in it at 100% including DLC, which is a real reward (it is what
            # makes the badge holographic) and was invisible here -- a platted stage and a 100%'d one
            # rendered identically, so the page asked for something it never acknowledged.
            # `completion_state` deliberately stays three-valued: base completion is what earns the badge,
            # and everything downstream keying on 'complete' should keep meaning exactly that.
            'is_mastered': bool(sr and sr.holo_satisfied),
        }
        (required if gates else retired).append(entry)

    for i, s in enumerate(required, start=1):
        s['display_number'] = i
    out = required

    # "Up next" is a suggestion grounded in the viewer's OWN progress, so only mark it when a profile is on
    # display (result present). Anon has no known progress -> no up-next (every stage would falsely be "next").
    if result:
        for s in out:                         # mark the first unfinished REQUIRED stage as "up next"
            if s['completion_state'] != 'complete':
                s['is_next'] = True
                break
    return required, retired


def _group_user_stats(profile_games, journey, target_profile) -> Optional[dict]:
    """The viewer's My Stats for THIS group's badge: trophy haul, play time, games platted / 100%'d, the
    stage-progress split (platted vs 100%'d), and the first-played / last-trophy span. Everything is read from
    DENORMALIZED ProfileGame fields over the games the JOURNEY lists (a bounded set -- so this is whale-safe,
    NOT a scan of the viewer's whole library). Returns None ONLY for anon (no profile on display); a signed-in viewer
    who owns none of this badge's games gets an all-zeros dict, so the My Stats panel always renders."""
    if target_profile is None:
        return None
    # Over the games the JOURNEY shows, not a platform-filtered re-derivation. Those came apart when
    # satisfaction went cross-platform: a hunter who cleared every stage on PS3 held a mastered Ultra HD
    # badge whose My Stats panel read "0 of 2 platted, 0 games played, 0 trophies, 0 hours" -- the hero and
    # the panel one tap apart, describing the same badge, disagreeing completely. Reading the journey also
    # means the two can no longer drift: whatever is listed is what is counted.
    game_ids = {
        e['game'].id
        for s in journey
        for e in (s['obtainable_games'] + s['delisted_games'])
    }
    for s in journey:
        for bundle in s['bundles']:
            for member in bundle['members']:
                for e in member['games']:
                    game_ids.add(e['game'].id)

    pgs = [profile_games[gid] for gid in game_ids if gid in profile_games]
    haul = {'bronze': 0, 'silver': 0, 'gold': 0, 'platinum': 0}
    trophies_total = games_platted = games_hundred = 0
    playtime = first_played = last_trophy = None
    for pg in pgs:
        et = pg.earned_trophies or {}
        for k in haul:
            haul[k] += et.get(k, 0)
        trophies_total += pg.earned_trophies_count
        if pg.has_plat:
            games_platted += 1
        if pg.progress == 100:
            games_hundred += 1
        if pg.play_duration:
            playtime = pg.play_duration if playtime is None else playtime + pg.play_duration
        if pg.first_played_date_time and (first_played is None or pg.first_played_date_time < first_played):
            first_played = pg.first_played_date_time
        if pg.most_recent_trophy_date and (last_trophy is None or pg.most_recent_trophy_date > last_trophy):
            last_trophy = pg.most_recent_trophy_date

    # Stage split: a stage counts as platted / 100%'d when ANY of its games clears that bar.
    #
    # BUNDLES COUNT TOO. This walked only the loose games, so a stage whose qualifier is a
    # ConceptBundle -- an episodic set, where the stage is satisfied only by finishing EVERY member --
    # could never be reported here at all. A hunter who had done exactly that read "0 of 3 platted" on
    # a badge the hero said they had finished, which is the same hero-vs-panel contradiction the
    # journey rewrite fixed one level up.
    #
    # A bundle is one STAGE qualifier, so it contributes at most one stage either way: platted when
    # every member has a platted game, 100%'d when every member has one at 100% -- the same "every
    # member" rule `_bundle_state` applies for satisfaction, rather than a looser "any member".
    stages_platted = stages_hundred = 0
    for s in journey:
        entries = s['obtainable_games'] + s['delisted_games']
        platted = any(e['pgame'] and e['pgame'].has_plat for e in entries)
        hundred = any(e['pgame'] and e['pgame'].progress == 100 for e in entries)
        for bundle in s['bundles']:
            members = bundle['members']
            if members and all(
                    any(e['pgame'] and e['pgame'].has_plat for e in m['games']) for m in members):
                platted = True
            if members and all(
                    any(e['pgame'] and e['pgame'].progress == 100 for e in m['games'])
                    for m in members):
                hundred = True
        stages_platted += 1 if platted else 0
        stages_hundred += 1 if hundred else 0

    return {
        'haul': haul, 'trophies_total': trophies_total,
        'games_total': len(game_ids), 'games_played': len(pgs),
        'games_platted': games_platted, 'games_hundred': games_hundred,
        'playtime_hours': round(playtime.total_seconds() / 3600) if playtime else 0,
        'first_played': first_played, 'last_trophy': last_trophy,
        'stages_platted': stages_platted, 'stages_hundred': stages_hundred,
    }


def _group_view(gb, result, hold, target_profile, series, catalog, games_map, profile_games,
                ratings_map, contract_map, participants) -> GroupView:
    is_holo = bool(hold and hold.is_holo)
    # `gb.required_stages` straight, with NO `or stage_count`. That fallback treated a correctly-computed
    # ZERO as "not computed yet" and substituted the full stage count, so an unearnable badge advertised a
    # complete chase to anonymous visitors while a signed-in viewer on the same page saw the real 0. The
    # column is a maintained denorm now (recompute_required_stages), so it is the answer, including when it
    # is zero.
    journey, retired = _group_journey(gb, result, catalog, games_map, profile_games, ratings_map, contract_map)
    # ANON reads the LADDER, not the `required_stages` denorm. That column is written only by
    # `evaluate_badges`, so a freshly-authored badge sits at its `default=0` until the nightly -- and with
    # the falsy-zero fallback gone, anon was being shown "This badge can no longer be earned. Every game it
    # asks for has become unobtainable" directly above two numbered, fully obtainable stages. Not merely
    # stale: an affirmative false claim, to every visitor and every crawler, after every authoring session.
    # `len(journey)` is the same number the engine gives a signed-in viewer, computed from the same units.
    gating = result.gating_count if result else len(journey)
    cleared = result.base_satisfied_count if result else 0
    holo_cnt = result.holo_satisfied_count if result else 0
    # Per-edition state via the shared helper (badge_xp.edition_display_state), so this LIVE view and the
    # Collection wall (which reads the materialized standing) can't derive different states from the same
    # numbers. Holo is layered on here (detail-only 'holo'/'none' vocabulary; the wall renders holo off
    # is_holographic), and progress_pct comes from the same helper.
    base_state, progress_pct = edition_display_state(bool(hold), cleared, gating)
    state = ('holo' if is_holo else 'earned') if hold else ('in_progress' if base_state == 'in_progress' else 'none')
    # A live earners position only exists while the viewer currently holds the badge.
    rank = lb.earners_rank(target_profile.id, gb.id) if (hold and target_profile) else None
    # Over the JOURNEY, so the headline figures describe the games actually on screen. Re-deriving a
    # platform-filtered set here left "4 games" and a difficulty average sitting above a ladder showing 7
    # cards -- the same hero-vs-panel disagreement this change fixed for My Stats, one tile to the left.
    stats = _group_stats(journey + retired, ratings_map)
    # Priced off every IN-SCOPE stage, and zero when nothing gates. "On offer" is what the badge is WORTH,
    # not what this viewer can still reach: a retired stage is counted, because a hunter who cleared it
    # while it was alive was paid for it. Deliberately more than `gating_count * XP_PER_STAGE`, which
    # under-reported, and deliberately NOT a promise that every point is still obtainable today.
    xp_on_offer = (0 if gating == 0
                   else (len(journey) + len(retired)) * XP_PER_STAGE + XP_BADGE_COMPLETION_BONUS)
    # Rarity is derived LIVE from the maintained earned_count over the whole COMMUNITY -- no stored
    # fields, no cron (the gb.rarity_* columns are dead scaffolding). Note this is NOT `participants`:
    # that is the series' pursuer base, which still drives series_size / the series rank's "of N".
    # See badge_rarity.
    rarity_pct, rarity_class = group_rarity(gb.earned_count, community_size())
    gv = GroupView(
        group_badge=gb, platform_group=gb.platform_group, art=gb.art_layers(),
        state=state, is_holo=is_holo, earned_at=(hold.earned_at if hold else None),
        earners_rank=rank, earned_count=gb.earned_count,
        rarity_pct=rarity_pct, rarity_class=rarity_class,
        stages_cleared=cleared, gating_count=gating, holo_satisfied_count=holo_cnt,
        progress_pct=progress_pct,
        segments=[i < cleared for i in range(gating)],
        games_count=stats['games_count'], avg_difficulty=stats['avg_difficulty'],
        avg_hours=stats['avg_hours'], xp_on_offer=xp_on_offer,
        stages=journey,
        retired_stages=retired,
        is_unearnable=(gating == 0),
        # BOTH ladders: a retired stage's games are shown on the page and its clear paid XP, so leaving
        # them out would reproduce the same "panel disagrees with the page" bug one level down.
        user_stats=_group_user_stats(profile_games, journey + retired, target_profile),
        frame={},
    )
    gv.frame = _medallion_frame(gv, series, target_profile)
    return gv


def get_badge_detail(series, target_profile) -> BadgeDetail:
    """Build the detail context for `series` (a BadgeSeries) as seen by `target_profile` (or None for anon)."""
    group_badges = list(
        series.group_badges.filter(is_live=True)
        .select_related('series', 'series__artwork_source', 'platform_group').order_by('platform_group__sort_order', 'id')
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

    # The series' PURSUER base: profiles with a SeriesBadgeStanding (recompute_standing keeps only xp>0
    # rows, so this is "made real progress", not "synced once"). One bounded indexed count, driving
    # series_size and the series rank's "of N". No longer the rarity denominator -- that is the whole
    # community now. Computed always (cheap), including for anon.
    # THE SERVICE, not a hand-rolled count. The modal prints "Series rank #N of M" and this is the M;
    # `series_rank` below is `lb.series_board_rank`, whose population is `is_linked`-gated. A raw count
    # here counted the scraped profiles the board does not seat, so the denominator exceeded the board.
    participants = lb.series_board_count(series.series_slug)

    groups = [_group_view(gb, desired.get(gb.id), holds.get(gb.id), target_profile, series, catalog,
                          games_map, profile_games, ratings_map, contract_map, participants)
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
            # The BOARD's rank, not an XP-ordered one. This modal prints "Series rank #N of M" where M is
            # the same population `series_board_count` returns, and the Ranks panel one scroll away prints
            # "You are #N" from `series_board_rank` -- so ranking on a different key set put two different
            # numbers, over the same denominator, one click apart. The per-series board is ordered by
            # progress, so that is what "rank in this series" means.
            series_rank = lb.series_board_rank(series.series_slug, target_profile.id)

    return BadgeDetail(
        series=series, groups=groups, has_multiple_groups=len(groups) > 1,
        viewer_state=viewer_state, series_xp=series_xp, series_progress_pct=series_progress_pct,
        series_rank=series_rank, series_size=participants,
        community_max_earned=max((g.earned_count for g in groups), default=0),
        target_profile=target_profile,
    )
