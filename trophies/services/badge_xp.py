"""Badge XP + progress: the sealed, swappable standing core for the new grouping-badge subsystem.

DECIDED model (design doc §5): flat XP per gating stage cleared + a flat badge-completion bonus, NO holo XP.
XP accrues PER GROUP BADGE (Legacy HD and Ultra HD are different games) and sums into a per-series total.
Progress (for the "chasers" leaderboard) is the furthest-along fraction over the series' group badges.

`compute_series_standings` is PURE (no ORM) -- fed the engine's per-series GroupBadgeResults, it returns
{series_slug: SeriesStanding}. `recompute_standing` is the one write seam: it recomputes a profile's standing
from scratch off the current DesiredState and upserts SeriesBadgeStanding (per series) + ProfileBadgeStanding
(the grand total) + ProfileEditionStanding (the same totals sliced per platform edition, which is what backs
the edition filter on Badge Points). Everything is isolated from the legacy ProfileGamification.total_badge_xp.

NOTHING HERE AGGREGATES A PROFILE'S TROPHIES. It used to: `badge_trophy_tallies` counted every earned trophy
across every badge-stage game to feed a "Badge Trophies" board. That was affordable while this seam ran only
from `evaluate_badges`, and became a full-library scan on every sync the moment the engine was wired into
`sync_complete` -- precisely the inline-aggregate pattern `recalc_earn_rates` was created to undo after the
May 2026 incident. The board now reads Profile's own trophy counters, which are already maintained. Keep it
that way: everything this seam writes is derived from the DesiredState it was handed plus bounded per-profile
reads, and none of it scans a hunter's library.
"""
from dataclasses import dataclass
from collections import defaultdict

from django.db import transaction

# Calibratable constants -- keep all XP magnitudes here so the model is a one-file swap.
# Calibrated to the "1,000,000 Club": over a projected mature catalog of ~400 group badges (~5 gating stages
# each -> ~3,100 XP/badge), a completionist lands ~1.24M, so 1M is reachable but hard (~80% of the catalog),
# with headroom above for two-version + holo elites. See test_million_club_calibration. Revisit if the catalog
# trajectory changes materially.
XP_PER_STAGE = 500                # per gating stage cleared (base-satisfied), a drip as you work a group
XP_BADGE_COMPLETION_BONUS = 600   # flat, once, when the base badge is earned


@dataclass(frozen=True)
class SeriesStanding:
    xp: int
    progress_bp: int          # furthest-along fraction over the series' group badges, basis points (0-10000)
    stages_cleared: int       # the best group's cleared gating stages (for "N of M" display)
    stages_total: int
    advanced_at: object = None  # date the profile reached this standing -- the board's tiebreak (see below)


def _group_badge_xp(result) -> int:
    """XP for ONE group badge from its GroupBadgeResult. base_satisfied_count is the number of GATING stages
    the profile cleared; the bonus lands once the whole base badge is earned. Holo contributes nothing."""
    xp = result.base_satisfied_count * XP_PER_STAGE
    if result.base_earned:
        xp += XP_BADGE_COMPLETION_BONUS
    return xp


def _fraction(result) -> float:
    return result.base_satisfied_count / result.gating_count if result.gating_count else 0.0


def edition_display_state(held: bool, cleared: int, gating: int) -> tuple:
    """Map a viewer's hold + THIS edition's (cleared, gating) gating-stage counts to a per-edition display
    state + percent. The ONE source both the Collection wall (reading the materialized
    SeriesBadgeStanding.group_progress) and the badge-detail live view (badge_detail_service._group_view) share,
    so the wall and the modal can't derive different states from the same numbers. Returns (state, progress_pct)
    with state in {'earned', 'in_progress', 'unearned'}; holo is a separate flag the caller layers on."""
    if held:
        return 'earned', 100
    if cleared > 0:
        return 'in_progress', (round(100 * cleared / gating) if gating else 0)
    return 'unearned', 0


def compute_series_standings(results_by_series: dict) -> dict:
    """Pure. results_by_series: {series_slug: [GroupBadgeResult, ...]}. Returns {series_slug: SeriesStanding}.
    Series XP sums the group badges' XP; progress is the single best group's cleared/gating fraction."""
    out = {}
    for slug, results in results_by_series.items():
        xp = sum(_group_badge_xp(r) for r in results)
        best = max(results, key=_fraction, default=None)
        advanced_at = None
        if best is not None and best.gating_count:
            cleared, total = best.base_satisfied_count, best.gating_count
            progress_bp = round(10000 * cleared / total)
            advanced_at = _advanced_at(best)
        else:
            cleared = total = progress_bp = 0
        out[slug] = SeriesStanding(xp, progress_bp, cleared, total, advanced_at)
    return out


def _advanced_at(result) -> object:
    """The moment a profile reached its CURRENT standing -- the leaderboard's tiebreak.

    A 3-stage badge puts everyone at 1/3 or 2/3 on the same rung, so without this the board sorts ties by
    profile id, which is arbitrary and reads as unranked. Date breaks them the same way the earners board
    already does: whoever got there first is higher.

    Earned vs chasing are DIFFERENT dates, deliberately:

    - Earned -> the group's own `earned_date`, i.e. the moment the badge was completed.
    - Chasing -> the latest gating stage the profile has cleared.

    Using the latest cleared stage for BOTH would be wrong under the `min_count` (megamix) policy, where
    `earned_date` is the date the need-th stage fell: a hunter who kept clearing optional extra stages
    afterwards would have their completion date pushed later and lose rank for doing MORE. Under 'all' the
    two coincide, which is why this only shows up on megamix series.

    No new engine work -- StageResult already carries `base_date`, and it was already being collected for
    the earn ordering.
    """
    if result.base_earned:
        return result.earned_date
    dates = [s.base_date for s in result.stages
             if s.gates and s.base_satisfied and s.base_date is not None]
    return max(dates) if dates else None


def compute_badge_xp(results_by_series: dict) -> tuple:
    """Pure convenience: (total_xp, {series_slug: xp}) derived from the standings."""
    per_series = {slug: s.xp for slug, s in compute_series_standings(results_by_series).items()}
    return sum(per_series.values()), per_series


def monthly_xp(results, tz=None) -> dict:
    """Pure. XP bucketed by the LOCAL month it was earned in: {(year, month): xp}.

    There is no badge-XP ledger and there does not need to be one -- the engine already carries the dates.
    Each gating stage that has been cleared knows WHEN (`StageResult.base_date`, the earliest date a
    qualifying game met the base bar) and each earned badge knows when its bar fell
    (`GroupBadgeResult.earned_date`). So the same two components `_group_badge_xp` SUMS are simply bucketed
    here instead. That coupling is deliberate and load-bearing: any change to how XP is scored has to move
    both, and `test_badge_monthly_xp` pins that these buckets reconcile against the standing total.

    `results` is any iterable of GroupBadgeResult (the engine's DesiredState values).

    Attribution is by COMPLETION date, not by when the badge was created. A hunter who platted Bloodborne
    in 2016 has that stage credited to 2016, even if the Soulsborne series was authored in 2025. That is
    the same rule the badge's own `earned_at` follows, so the earned count and the XP on a recap slide
    always agree with each other. The legacy StageCompletionEvent clamped retroactive credit to
    `badge.created_at` instead; the subsystem does not, and matching it would put XP in a month the
    hunter's earned badges do not appear in.

    Stages and badges with no date contribute nothing -- they cannot be placed in a month. Those are
    exactly the rows that make the reconciliation a `<=` rather than an `==`.
    """
    buckets = defaultdict(int)

    def _key(value):
        if value is None:
            return None
        localized = value.astimezone(tz) if (tz is not None and hasattr(value, 'astimezone')) else value
        return localized.year, localized.month

    for res in results:
        if res.gating_count == 0:
            continue                       # not earnable in this group; contributes no XP at all
        for stage in res.stages:
            if stage.gates and stage.base_satisfied:
                key = _key(stage.base_date)
                if key:
                    buckets[key] += XP_PER_STAGE
        if res.base_earned:
            key = _key(res.earned_date)
            if key:
                buckets[key] += XP_BADGE_COMPLETION_BONUS

    return dict(buckets)


def _results_by_series(desired: dict, group_badges) -> dict:
    """Group the engine's desired {group_badge_id: GroupBadgeResult} by series_slug using the GroupBadge rows."""
    by_series = defaultdict(list)
    for gb in group_badges:
        result = desired.get(gb.id)
        if result is not None:
            by_series[gb.series.series_slug].append(result)
    return by_series


#: Namespace for `recompute_standing`'s per-profile advisory lock. Arbitrary but STABLE -- changing it
#: would let an old and a new deploy recompute the same profile concurrently during a rolling restart.
_RECOMPUTE_LOCK_NS = 4267


def _upsert(model, lookup: dict, defaults: dict) -> None:
    """Cheap upsert (UPDATE, else INSERT) -- avoids update_or_create's savepoint + SELECT FOR UPDATE, which is
    heavy in the per-profile recompute.

    Safe ONLY because `recompute_standing` holds a per-profile lock, which is what actually makes "a
    profile's recompute is never concurrent with itself" true. It was asserted as a fact before the lock
    existed and it was not one: the nightly `evaluate_badges --all` and a hunter's own `sync_complete` are
    separate processes with no interlock, so a hunter syncing while the batch reached them ran two
    recomputes at once. Both would find no row to UPDATE and both would INSERT -- an IntegrityError on the
    unique constraint, aborting the whole recompute.
    """
    if not model.objects.filter(**lookup).update(**defaults):
        model.objects.create(**lookup, **defaults)


@transaction.atomic
def recompute_standing(profile_id, desired: dict, group_badges) -> None:
    """Write seam: recompute the EVALUATED series' standings from the current DesiredState and upsert them.
    Only touches series present in `group_badges` (a scoped --series run leaves other series intact); a series
    that drops to 0 XP is removed. The grand total is re-summed from ALL the profile's series rows, so scoped
    runs keep it correct. Recompute-from-scratch, so it can't drift.

    INVARIANT (load-bearing -- scope by SERIES, never by individual edition): `group_badges` MUST contain EVERY
    live edition of any series it touches. Each series' standing is a full REPLACE computed only from the passed
    editions -- xp is SUMMED over them, progress_bp is the MAX, and group_progress is keyed per edition -- so a
    partial-series call (e.g. a future incremental sync scoped to one changed edition) would undercount xp and
    drop the sibling editions' group_progress. All current callers (evaluate_badges --all / --series / a
    username) resolve full series, which honors this.

    ATOMIC, and serialized per profile by the lock below. This seam writes across FOUR tables and deletes
    from two of them, and its own comments already reason about pairs of rows that must agree -- "one of
    them holding a hunter the other has dropped is the kind of disagreement nobody would think to check".
    Without a transaction, any failure partway (a timeout is the likely one) left exactly that
    disagreement durably on disk. It stays SEPARATE from `apply_changes`'s transaction rather than being
    folded into it, because the announcement between them is deliberately sent while the badges are
    already committed -- see the comment in `evaluate_and_apply`."""
    from django.db import connection
    from django.db.models import Sum
    from trophies.models import (ProfileBadgeStanding, ProfileEditionStanding, SeriesBadgeStanding,
                                 SeriesEditionStanding)

    # Serializes this profile's recomputes against each other, which is the precondition `_upsert` relies
    # on. Two writers reach the same profile in normal operation: the nightly `evaluate_badges --all` and
    # that hunter's own `sync_complete`.
    #
    # An ADVISORY lock, not `select_for_update` on the Profile row, and that choice is load-bearing. The
    # row lock was tried and it collides with the sync path: `Profile.add_to_sync_target` locks the same
    # row with `nowait=True` behind a 5-attempt / 0.2s tenacity retry, so a recompute holding it for more
    # than ~0.8s turns into a `RetryError` inside the sync job -- and `nightly.py`'s own docstring says
    # that overlap is expected. `increment_sync_progress`'s per-tick `save()` would block on it too.
    # An advisory lock takes no row, so every Profile writer is unaffected; it is scoped to the badge
    # recompute and nothing else contends for it.
    #
    # `_xact_` releases at COMMIT or ROLLBACK, so it cannot leak on the error path. Keyed on a constant
    # namespace + the profile id, so two different profiles never wait on each other.
    with connection.cursor() as cur:
        cur.execute('SELECT pg_advisory_xact_lock(%s, %s)', [_RECOMPUTE_LOCK_NS, profile_id])

    standings = compute_series_standings(_results_by_series(desired, group_badges))
    positive = {slug: s for slug, s in standings.items() if s.xp > 0}
    zeroed = [slug for slug, s in standings.items() if s.xp == 0]

    # Per-EDITION read-model {slug: {platform_group_key: [cleared, gating]}} for every EARNABLE edition.
    # The engine already computed these per-group results; materializing them lets the Collection wall read each
    # edition's OWN progress without re-evaluating. Same recompute-from-scratch seam as the rest of the standing.
    #
    # Gated on `gating_count > 0`, NOT on `base_satisfied_count > 0`. Storing only STARTED editions left the
    # wall with no denominator for one you had not touched, so "0 / 5 stages" -- the most motivating number on
    # the card -- had nothing to render and went blank. Deriving that total from the series' Stage count instead
    # was tried and is wrong: gating is PER EDITION (a stage only gates if some game in it runs on that
    # platform group), so a series with 8 stages, 3 of them PS5-only, would tell a Legacy HD hunter "0 / 8" and
    # then drop to "1 / 5" the moment they cleared one -- a denominator that shrinks as you progress.
    #
    # `gating_count == 0` means the badge is not offered in that group at all (every stage's games delisted or
    # unobtainable there), so it is deliberately still skipped: an unearnable edition must advertise no chase.
    group_prog = defaultdict(dict)
    # Per-edition XP for each series, same pass. Only positive entries are stored: a zero contributes
    # nothing to the sum it exists for, and keeping them would grow the blob with every seeded edition.
    group_xp = defaultdict(dict)
    # The per-edition BOARD rows, from the same pass. Only STARTED editions get one
    # (`base_satisfied_count > 0`), which is the board's own membership rule -- `group_prog` above
    # deliberately keeps untouched editions because the Collection needs their denominator, and a board
    # does not. Storing them would roughly double a table the nightly chain writes for every profile.
    #
    # `advanced_at` here is the EDITION's own date. `_advanced_at` has always taken a per-edition
    # `GroupBadgeResult`; `compute_series_standings` computes it for the furthest-along edition and drops
    # the rest, which is what made the per-edition board tiebreak on a date from a different edition.
    edition_rows = defaultdict(list)
    for gb in group_badges:
        r = desired.get(gb.id)
        if r is not None and r.gating_count > 0:
            group_prog[gb.series.series_slug][gb.platform_group.key] = [r.base_satisfied_count, r.gating_count]
        if r is not None:
            xp = _group_badge_xp(r)
            if xp:
                group_xp[gb.series.series_slug][gb.platform_group.key] = xp
            if r.gating_count > 0 and r.base_satisfied_count > 0:
                edition_rows[gb.series.series_slug].append({
                    'platform_group_key': gb.platform_group.key,
                    'xp': xp,
                    'stages_cleared': r.base_satisfied_count,
                    'gating_count': r.gating_count,
                    'advanced_at': _advanced_at(r),
                })

    # Read ONCE for the whole recompute: this seam writes one row per positive series plus the grand
    # total, and both mirrored values are the same for all of them.
    country, is_linked = _mirrored_profile_fields(profile_id)

    for slug, s in positive.items():
        _upsert(SeriesBadgeStanding, {'profile_id': profile_id, 'series_slug': slug},
                {'xp': s.xp, 'progress_bp': s.progress_bp,
                 'stages_cleared': s.stages_cleared, 'stages_total': s.stages_total,
                 'group_progress': dict(group_prog.get(slug, {})),
                 'group_xp': dict(group_xp.get(slug, {})),
                 'advanced_at': s.advanced_at,
                 'country_code': country, 'is_linked': is_linked})
        _write_series_edition_standings(profile_id, slug, edition_rows.get(slug, []), country, is_linked)
    if zeroed:
        SeriesBadgeStanding.objects.filter(profile_id=profile_id, series_slug__in=zeroed).delete()
        # The per-edition rows go with their parent. Two stores that must agree, and this is the half that
        # is easy to forget: a series dropping to zero XP leaves no SeriesBadgeStanding row, so nothing
        # would ever prune these and the edition board would keep ranking a hunter the series board has
        # dropped -- exactly the disagreement the ProfileBadgeStanding branch below warns about.
        SeriesEditionStanding.objects.filter(profile_id=profile_id, series_slug__in=zeroed).delete()

    total = _live_standings(profile_id).aggregate(t=Sum('xp'))['t'] or 0
    if total > 0:
        badges_total, badges_by_edition = badges_held_counts(profile_id)
        _upsert(ProfileBadgeStanding, {'profile_id': profile_id},
                {'total_xp': total, 'country_code': country, 'badges_held': badges_total,
                 'is_linked': is_linked})
        _write_edition_standings(profile_id, country, is_linked, badges_by_edition)
    else:
        # No badge XP at all means no standing anywhere, editions included -- the boards read
        # ProfileBadgeStanding for the all-editions view and these rows for a slice, and one of them
        # holding a hunter the other has dropped is the kind of disagreement nobody would think to check.
        ProfileBadgeStanding.objects.filter(profile_id=profile_id).delete()
        ProfileEditionStanding.objects.filter(profile_id=profile_id).delete()
        SeriesEditionStanding.objects.filter(profile_id=profile_id).delete()


def _write_series_edition_standings(profile_id, series_slug, rows, country, is_linked):
    """Replace ONE series' per-edition board rows for a profile.

    A full replace per (profile, series), which is what keeps this from drifting out of step with its
    parent: an edition a hunter has stopped qualifying for (its last gating game delisted on that
    platform, or the stage that carried it removed) leaves `rows` and is deleted here, rather than
    lingering on the board because nothing thought to prune it.

    Scoped by SERIES, never by edition -- the same invariant `group_progress` rests on, and for the same
    reason: every caller of `recompute_standing` scopes by SERIES, so `group_badges` holds EVERY live
    edition of any series it touches. The rows passed here are therefore the complete set for this series,
    and the delete below cannot remove one that simply was not evaluated. `evaluate_for_sync` is where
    that scoping is decided (`test_badge_sync_wiring` pins it); a badge-scoped call would silently drop
    the omitted edition's board row rather than merely zero a JSON key.
    """
    from trophies.models import SeriesEditionStanding

    keep = []
    for row in rows:
        _upsert(SeriesEditionStanding,
                {'profile_id': profile_id, 'series_slug': series_slug,
                 'platform_group_key': row['platform_group_key']},
                {'xp': row['xp'], 'stages_cleared': row['stages_cleared'],
                 'gating_count': row['gating_count'], 'advanced_at': row['advanced_at'],
                 'country_code': country, 'is_linked': is_linked})
        keep.append(row['platform_group_key'])
    (SeriesEditionStanding.objects
     .filter(profile_id=profile_id, series_slug=series_slug)
     .exclude(platform_group_key__in=keep)
     .delete())


def _live_standings(profile_id):
    """The profile's SeriesBadgeStanding rows for series that still have a LIVE edition.

    Both profile-wide sums go through here, and that liveness gate is the point. `recompute_standing`
    only ever DELETES standings for the series it was handed, and every caller hands it live badges --
    so when a whole series goes dormant, or its `BadgeSeries` is deleted (`series_slug` is a bare
    SlugField, not an FK, so nothing cascades), its row survives forever. Summing it left XP from badges
    nobody can see in every holder's Badge Points total, permanently, with no self-heal: the nightly
    `evaluate_badges --all` is also live-scoped, so it never revisits the orphan either.

    The dormant rows are left in place rather than deleted. Deleting them here would be wrong -- this
    function is called on a scoped recompute that has no business ruling on series it did not evaluate --
    and a dormant series is often dormant temporarily. Made inert instead: the row keeps its history and
    contributes nothing while its badges are unreleased, then counts again the moment one goes live.

    One extra subquery, bounded by catalogue size.
    """
    from trophies.models import BadgeSeries, SeriesBadgeStanding

    live_slugs = BadgeSeries.objects.filter(group_badges__is_live=True).values_list('series_slug', flat=True)
    return SeriesBadgeStanding.objects.filter(profile_id=profile_id, series_slug__in=live_slugs)


def _write_edition_standings(profile_id, country, is_linked, badges_by_edition):
    """Upsert the profile's per-edition standings, and drop the editions they no longer stand in.

    Per-edition XP is re-summed from EVERY one of the profile's SeriesBadgeStanding rows rather than from
    the results of the call that got us here. That is the same rule the grand total follows and it is what
    makes a scoped `--series` recompute safe: the call only knows about the series it evaluated, but this
    row is profile-wide.

    The edition set comes from what the profile actually HAS -- xp keys union badge keys -- rather than
    from the live PlatformGroup table. That dropped a query per recompute and, more importantly, removed
    the last reason this seam needed to know which editions exist: a hunter's row set is a fact about the
    hunter. An edition they hold nothing in has no row, and a deactivated edition's rows fall out on the
    next recompute because neither source names it any more.
    """
    from trophies.models import ProfileEditionStanding, SeriesBadgeStanding

    xp_by_edition = defaultdict(int)
    for blob in _live_standings(profile_id).values_list('group_xp', flat=True):
        for key, xp in (blob or {}).items():
            xp_by_edition[key] += xp

    keep = []
    for key in set(xp_by_edition) | set(badges_by_edition):
        xp = xp_by_edition.get(key, 0)
        badges = badges_by_edition.get(key, 0)
        if not xp and not badges:
            continue
        keep.append(key)
        _upsert(ProfileEditionStanding, {'profile_id': profile_id, 'platform_group_key': key},
                {'total_xp': xp, 'country_code': country, 'badges_held': badges,
                 'is_linked': is_linked})
    ProfileEditionStanding.objects.filter(profile_id=profile_id).exclude(platform_group_key__in=keep).delete()


def _mirrored_profile_fields(profile_id):
    """The Profile columns every standing row carries a copy of: `(country_code, is_linked)`.

    Both are denormalized for the same reason -- they are board PREDICATES, and a predicate on another
    table cannot go in this table's indexes. Read together in ONE query because this seam writes several
    rows per recompute and both values are the same for all of them.
    """
    from trophies.models import Profile
    row = (Profile.objects.filter(pk=profile_id)
           .values_list('country_code', 'is_linked').first()) or ('', False)
    return (row[0] or ''), bool(row[1])


def badges_held_counts(profile_id):
    """Group badges the profile HOLDS: (total, {platform_group_key: n}).

    ONE grouped query for both halves -- the group is the edition key, so the per-edition split and the
    total come out of the same aggregate. Bounded by the number of editions, not by how many badges the
    hunter holds.

    HELD, not "earned in the legacy sense": this reads `UserGroupBadge`, the new subsystem's surface, which
    is what the Collection and the milestones metric already count. `ProfileGamification.total_badges_earned`
    is the retired tier count and a different number; the two must never be shown as the same figure.

    LIVE badges only, matching what XP counts. Without that filter the two figures disagreed: XP is summed
    over the badges the evaluation was scoped to (`is_live=True`), while this counted every held row -- so a
    curator smoke-testing an unreleased badge against a real profile left that hunter permanently reading
    "4,200 points, 7 badges" where only 6 of them produced any points. The whole reason this column exists
    is to make the points figure legible, which it cannot do if it is counting something else.

    Editions do NOT overlap: a group badge belongs to exactly one platform group, so these sum to the total.
    """
    from django.db.models import Count
    from trophies.models import UserGroupBadge

    rows = (
        UserGroupBadge.objects.filter(profile_id=profile_id, group_badge__is_live=True)
        .values('group_badge__platform_group__key')
        .annotate(n=Count('id'))
        .values_list('group_badge__platform_group__key', 'n')
    )
    by_edition = {key: n for key, n in rows if key}
    return sum(n for _, n in rows), by_edition
