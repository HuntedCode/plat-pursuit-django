"""Badge earn-lifecycle: diff (pure) + apply (writes).

`diff()` compares the engine's DesiredState against the profile's currently-held UserGroupBadge rows and yields
the minimal set of changes — pure, so it's unit-testable like the engine. Binary model: award (create the held
row) / revoke (delete it) / update (resync is_holo + earned_at, and mastery). There is NO permanent earn_rank;
the earners rank is derived live from earned_at (current holders ordered by completion date), so awards need no
ordering. A series' Full Set / Series Master are applied after the badges (`apply_series_rewards`).
`apply_changes()` executes changes in one transaction and OWNS the earned_count denorm (no signals); the
eval->diff->apply entry points also recompute badge XP. See docs/design/rebuild/badge-backend-rebuild.md.
"""
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from django.db import transaction
from django.db.models import F
from django.db.models.functions import Greatest
from django.utils import timezone

from trophies.models import GroupBadge, SeriesAward, UserGroupBadge
from trophies.services import moment_service
from trophies.services.moment_service import record_badge as record_badge_moment
from trophies.services.badge_engine import evaluate_series_rewards
from trophies.services.badge_orchestrator import (
    resolve_group_badges, build_catalog, evaluate_with_catalog,
)
from trophies.services.badge_adapters import (
    grant_series_title, revoke_series_title_if_orphaned, emit_badge_earned, announce_badges_earned,
)
from trophies.services.badge_xp import recompute_standing

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CurrentBadge:
    is_holo: bool
    earned_at: Optional[datetime]
    mastered: bool = False


@dataclass(frozen=True)
class BadgeChange:
    group_badge_id: int
    action: str          # 'award' | 'revoke' | 'update'
    holo: bool
    earned_date: Optional[datetime] = None
    mastery: str = ''    # 'gained' | 'lost' | '' (unchanged)
    mastered_date: Optional[datetime] = None


def _empty_result() -> dict:
    """The apply summary's shape: what was awarded, revoked, updated, and which badges became mastered."""
    return {'awarded': [], 'revoked': [], 'updated': [], 'mastered': []}


def diff(desired: dict, current: dict) -> list:
    """Pure. desired: {group_badge_id: GroupBadgeResult}; current: {group_badge_id: CurrentBadge} for the rows
    the profile CURRENTLY holds. Binary model: hold iff base_earned. Only evaluated badges (desired's keys) are
    considered. 'update' resyncs a held row's is_holo / earned_at (the latter shifts when the badge iteration
    changes, keeping the leaderboard sort honest) and its mastery.

    Mastery changes on the TRANSITION only. Its date is "Mastered since", stamped when it is gained and kept
    while it holds, so a recomputed date never re-stamps it (the undated-earn churn, see badge-system.md)."""
    changes = []
    for gb_id, res in desired.items():
        cur = current.get(gb_id)
        if res.base_earned:
            if cur is None:
                changes.append(BadgeChange(gb_id, 'award', res.holo, res.earned_date,
                                           'gained' if res.mastered else '', res.mastered_date))
                continue
            mastery = ('gained' if res.mastered and not cur.mastered
                       else 'lost' if cur.mastered and not res.mastered else '')
            if cur.is_holo != res.holo or cur.earned_at != res.earned_date or mastery:
                changes.append(BadgeChange(gb_id, 'update', res.holo, res.earned_date, mastery, res.mastered_date))
            # else: held, and holo + earned_at + mastery already match -> no change
        else:
            if cur is not None:
                changes.append(BadgeChange(gb_id, 'revoke', False))
            # else: not held and shouldn't be -> no change
    return changes


@transaction.atomic
def apply_changes(profile, changes, gb_map: dict) -> dict:
    """Execute changes for ONE profile. gb_map: {group_badge_id: GroupBadge with `series` select_related}.
    Binary model: award creates the held row, revoke deletes it, update resyncs is_holo/earned_at. This layer
    OWNS the earned_count denorm (no signals). Returns a summary; `mastered` lists the badges that BECAME
    mastered in this run."""
    result = _empty_result()
    for ch in changes:
        gb = gb_map[ch.group_badge_id]
        if ch.mastery == 'gained':
            # Celebrated (when finished recently) and reported for the announcement, on either path below.
            moment_service.record_mastered(profile, gb, ch.mastered_date)
            result['mastered'].append(gb.id)
        if ch.action == 'award':
            UserGroupBadge.objects.create(
                profile=profile, group_badge=gb, is_holo=ch.holo,
                earned_at=ch.earned_date or timezone.now(),   # current-iteration completion, not the sync time
                mastered_at=(ch.mastered_date or timezone.now()) if ch.mastery == 'gained' else None,
                # Mirrored from Profile so the earners rank can gate and slice on its own table. Stamped
                # at birth because the propagation signal only fires on CHANGE -- a badge awarded after a
                # hunter linked (or moved country) would otherwise keep the defaults forever.
                is_linked=bool(getattr(profile, 'is_linked', False)),
                country_code=getattr(profile, 'country_code', '') or '',
            )
            GroupBadge.objects.filter(id=gb.id).update(earned_count=F('earned_count') + 1)
            grant_series_title(profile.id, gb.series)
            emit_badge_earned(profile.id, gb.id)
            # Its celebration, played on the hunter's next My Pursuit page (moment_service). Every award path
            # comes through here, nightly and rechecks included: a badge earned is news however it was noticed.
            record_badge_moment(profile, gb, ch.earned_date)
            result['awarded'].append(gb.id)
        elif ch.action == 'revoke':
            UserGroupBadge.objects.filter(profile=profile, group_badge=gb).delete()
            # Clamped at 0. `earned_count` carries a `>= 0` check constraint, so a bare `- 1` turns any
            # drift between the held rows and this denorm into an IntegrityError -- and because the whole
            # apply runs in one transaction, that aborts the ENTIRE evaluation, not just the counter.
            # Drift is reachable: a cutover backfill or admin surgery can create a hold without touching
            # the denorm. A counter that under-reports by one is a cosmetic rarity error; a 500 that
            # discards a hunter's whole badge evaluation is not.
            GroupBadge.objects.filter(id=gb.id).update(
                earned_count=Greatest(F('earned_count') - 1, 0)
            )
            revoke_series_title_if_orphaned(profile.id, gb.series)
            result['revoked'].append(gb.id)
        elif ch.action == 'update':
            # Held badge stays held: resync the live flags. earned_at moves when the iteration changes; mastery
            # only on its transition (its date is when the CURRENT mastery began).
            fields = {'is_holo': ch.holo, 'earned_at': ch.earned_date or timezone.now()}
            if ch.mastery:
                fields['mastered_at'] = (ch.mastered_date or timezone.now()) if ch.mastery == 'gained' else None
            UserGroupBadge.objects.filter(profile=profile, group_badge=gb).update(**fields)
            result['updated'].append(gb.id)
    return result


@transaction.atomic
def apply_series_rewards(profile, desired, group_badges) -> list:
    """A series' Full Set and Series Master, from its editions' results (`badge_engine.evaluate_series_rewards`).
    Binary like a hold: gained creates the `SeriesAward`, lost deletes it. Returns what was GAINED, as
    [(kind, series, finished_at)], for the announcement.

    Needs EVERY live edition of a series in `desired`, which every entry point gives (they scope by series, see
    `evaluate_for_touched_games`); a partial list would read a Full Set as lost. One read of the hunter's awards
    for the run's series."""
    editions, series = defaultdict(list), {}
    for gb in group_badges:
        if gb.id in desired:
            editions[gb.series_id].append(desired[gb.id])
            series[gb.series_id] = gb.series
    if not editions:
        return []
    held = set(SeriesAward.objects.filter(profile=profile, series_id__in=list(editions))
               .values_list('series_id', 'kind'))
    gained, lost = [], []
    for sid, results in editions.items():
        r = evaluate_series_rewards(results)
        for kind, has, when in ((SeriesAward.KIND_FULL_SET, r.full_set, r.full_set_date),
                                (SeriesAward.KIND_SERIES_MASTER, r.series_master, r.series_master_date)):
            if has and (sid, kind) not in held:
                SeriesAward.objects.create(profile=profile, series_id=sid, kind=kind, since=when or timezone.now())
                moment_service.record_series_reward(profile, series[sid], kind, when)
                gained.append((kind, series[sid], when))
            elif not has and (sid, kind) in held:
                lost.append((sid, kind))
    for sid, kind in lost:
        SeriesAward.objects.filter(profile=profile, series_id=sid, kind=kind).delete()
    return gained


def _news_rewards(result, desired, gained, gb_map) -> list:
    """The run's new rewards worth announcing: only the ones finished recently, the rule their moments use, so
    a veteran's old mastery noticed for the first time is not announced as news."""
    rewards = [('mastered', gb_map[gb_id], gb_map[gb_id].series) for gb_id in result['mastered']
               if moment_service.is_recent_reward(desired[gb_id].mastered_date)]
    return rewards + [(kind, None, series) for kind, series, when in gained if moment_service.is_recent_reward(when)]


def _plan_with_catalog(profile, catalog):
    """Diff one profile against a PRE-BUILT catalog (no catalog re-fetch). Returns (changes, desired) -- the
    desired state is also fed to the XP recompute. Per-profile DB cost: the two completion reads (in
    evaluate_with_catalog) + one current-state read here."""
    desired = evaluate_with_catalog(profile, catalog)
    gb_ids = [gb.id for gb in catalog['group_badges']]
    current = {
        ugb.group_badge_id: CurrentBadge(ugb.is_holo, ugb.earned_at, ugb.mastered_at is not None)
        for ugb in UserGroupBadge.objects.filter(profile=profile, group_badge_id__in=gb_ids)
    }
    return diff(desired, current), desired


def plan(profile, group_badges=None):
    """Evaluate + diff WITHOUT writing (no XP recompute either). Returns (changes, gb_map). Public so a
    --dry-run runner can preview what apply would do."""
    group_badges = resolve_group_badges(group_badges)
    if not group_badges:
        return [], {}
    catalog = build_catalog(group_badges)
    gb_map = {gb.id: gb for gb in group_badges}
    changes, _ = _plan_with_catalog(profile, catalog)
    return changes, gb_map


def evaluate_and_apply(profile, group_badges=None, notify=False) -> dict:
    """Single-profile entry point: evaluate, diff, apply, recompute XP. Returns the apply summary.

    `notify` is OPT-IN and defaults off. The announcement belongs to a caller reacting to a hunter actually
    playing, not to the function: a default of True would make any batch caller a webhook storm.
    """
    group_badges = resolve_group_badges(group_badges)
    if not group_badges:
        return _empty_result()
    catalog = build_catalog(group_badges)
    gb_map = {gb.id: gb for gb in group_badges}
    changes, desired = _plan_with_catalog(profile, catalog)
    result = apply_changes(profile, changes, gb_map)
    gained = apply_series_rewards(profile, desired, group_badges)

    # Announced BEFORE the standing recompute, and that ordering is deliberate. `apply_changes` is atomic
    # and has committed by here, so the badges are durable. `recompute_standing` is atomic too, but it is
    # a SEPARATE transaction that can still roll back on its own (a timeout is the likely failure), and
    # the announcement must not be inside it. Announcing after it meant a failure there swallowed the
    # announcement -- and permanently, because `awarded` is a TRANSITION: the next sync sees the badge
    # already held, emits no change, and the hunter is never told about a badge they earned.
    #
    # ONE consolidated announcement for the whole run, not one per badge from inside apply_changes, which
    # would ping a hunter three times for finishing a three-edition series.
    if notify:
        awarded = [gb_map[gb_id] for gb_id in result['awarded'] if gb_id in gb_map]
        rewards = _news_rewards(result, desired, gained, gb_map)
        if awarded or rewards:
            announce_badges_earned(profile, awarded, rewards)

    recompute_standing(profile.id, desired, group_badges)   # XP tracks current progress, not just earns
    return result


def evaluate_for_sync(profile, profilegame_ids) -> dict:
    """What `_job_sync_complete` calls. Never raises.

    A separate named function rather than a try/except inline in `token_keeper`, for two reasons. The
    inline version wrapped the IMPORT as well as the call, so an ImportError or a renamed kwarg degraded to
    a log line on every sync forever with the boards quietly frozen. And a block buried in a 300-line job
    method is not reachable by a test -- deleting the whole thing passed the entire suite.

    Announces, as of cutover 5b. It was silent while the legacy engine ran immediately before it in
    `_job_sync_complete` and sent its own embed to the same webhook -- announcing then would have pinged a
    hunter twice for one act, once tier-shaped and once edition-shaped. The legacy engine is gone, so this
    is the only voice left. Re-announcement is guarded durably in `announce_badges_earned`.
    """
    try:
        return evaluate_for_touched_games(profile, profilegame_ids, notify=True)
    except Exception:
        logger.exception('group-badge evaluation failed for profile %s', getattr(profile, 'id', None))
        return _empty_result()


def evaluate_for_touched_games(profile, profilegame_ids, notify=True) -> dict:
    """The SYNC entry point: evaluate only the series the just-synced games belong to.

    Scoped, because a sync touches a handful of games and the catalogue is the whole badge system --
    re-evaluating every live badge on every sync would put the cost of the catalogue on every hunter who
    plays anything. Same narrowing the legacy `check_profile_badges` does, from the same starting point.

    Scoped by SERIES, never by individual badge, and that is load-bearing rather than incidental:
    `recompute_standing` REPLACES a series' standing from only the editions it is handed, so a scope that
    caught Ultra HD but not Legacy HD would undercount that series' XP and drop the sibling edition's
    `group_progress`. Filtering `series__series_slug__in=...` returns every live edition of every touched
    series by construction.

    A badge authored after a hunter's last sync does not evaluate for them until they next touch one of its
    games -- the legacy path has the same property. The nightly `evaluate_badges --all` closes that gap
    (it goes through `evaluate_and_apply_batch`, which has no announcement path at all), and
    `evaluate_badges --series <slug>` is the targeted form.

    EVERY membership path is followed (`stage_has_concept_q`). A Stage reaches its games through `concepts`,
    through `concept_bundles -> concepts` and through `trophy_groups -> concept`, and matching only
    `concepts` cannot merely miss the occasional bundled game, it misses EVERY one of them. `build_catalog`
    walks them all; so must this, or a hunter who finishes a bundled game gets no evaluation at all and
    nothing errors.
    """
    from trophies.models import ProfileGame, Stage
    from trophies.services.stage_membership import stage_has_concept_q

    if not profilegame_ids:
        return _empty_result()

    concept_ids = list(
        ProfileGame.objects.filter(id__in=profilegame_ids, profile=profile)
        .filter(game__concept__isnull=False)
        .values_list('game__concept_id', flat=True).distinct()
    )
    series_slugs = list(
        Stage.objects.filter(stage_has_concept_q(concept_ids)).values_list('series_slug', flat=True).distinct()
    )
    if not series_slugs:
        return _empty_result()

    return evaluate_and_apply(
        profile,
        GroupBadge.objects.filter(is_live=True, series__series_slug__in=series_slugs),
        notify=notify,
    )


def evaluate_and_apply_batch(profiles, group_badges=None, catalog=None) -> Counter:
    """Process many profiles. The immutable catalog is built ONCE and reused per profile (no per-profile
    re-fetch). No rank stamping: earners rank is derived live from earned_at, so awards need no ordering.
    Returns count totals.

    `catalog` lets a caller that has ALREADY built one hand it over. `evaluate_badges` builds a catalog
    for `recompute_required_stages` immediately before calling this, and without the parameter the six
    prefetch queries over the whole stage graph simply ran twice. Threading the resolved LIST through
    removed the duplicate resolve but not the duplicate build, which the commit message claimed it had.
    """
    group_badges = resolve_group_badges(group_badges)
    totals = Counter()
    if not group_badges:
        return totals
    catalog = catalog if catalog is not None else build_catalog(group_badges)
    gb_map = {gb.id: gb for gb in group_badges}

    for profile in profiles:
        # Per-profile insulation. Without it the first raising profile aborts the WHOLE batch, and the
        # callers swallow that at series granularity: `detect_dlc_and_refresh` logs one FAILED line and
        # advances its watermark anyway, so every remaining hunter in that series is left stale and is
        # never re-swept. The legacy refresh guarded each (profile, badge) pair for the same reason.
        try:
            changes, desired = _plan_with_catalog(profile, catalog)
            if changes:
                for key, ids in apply_changes(profile, changes, gb_map).items():
                    totals[key] += len(ids)
            totals['series_rewards'] += len(apply_series_rewards(profile, desired, group_badges))
            recompute_standing(profile.id, desired, group_badges)   # XP tracks progress, not just earns
        except Exception:
            totals['failed'] += 1
            logger.exception('badge batch failed for profile %s', getattr(profile, 'id', None))
    return totals
