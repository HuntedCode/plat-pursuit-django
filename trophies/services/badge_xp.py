"""Badge XP + progress: the sealed, swappable standing core for the new grouping-badge subsystem.

DECIDED model (design doc §5): flat XP per gating stage cleared + a flat badge-completion bonus, NO holo XP.
XP accrues PER GROUP BADGE (Legacy HD and Ultra HD are different games) and sums into a per-series total.
Progress (for the "chasers" leaderboard) is the furthest-along fraction over the series' group badges.

`compute_series_standings` is PURE (no ORM) -- fed the engine's per-series GroupBadgeResults, it returns
{series_slug: SeriesStanding}. `recompute_standing` is the one write seam: it recomputes a profile's standing
from scratch off the current DesiredState and upserts SeriesBadgeStanding (per series) + ProfileBadgeStanding
(the grand total) + ProfileEditionStanding (the same totals sliced per platform edition, which is what backs
the edition filter on the boards). Everything is isolated from the legacy ProfileGamification.total_badge_xp.
"""
from dataclasses import dataclass
from collections import defaultdict

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


def _upsert(model, lookup: dict, defaults: dict) -> None:
    """Cheap upsert (UPDATE, else INSERT) -- avoids update_or_create's savepoint + SELECT FOR UPDATE, which is
    heavy in the per-profile recompute. Safe because a profile's recompute is never concurrent with itself."""
    if not model.objects.filter(**lookup).update(**defaults):
        model.objects.create(**lookup, **defaults)


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
    username) resolve full series, which honors this."""
    from django.db.models import Sum
    from trophies.models import ProfileBadgeStanding, ProfileEditionStanding, SeriesBadgeStanding

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
    for gb in group_badges:
        r = desired.get(gb.id)
        if r is not None and r.gating_count > 0:
            group_prog[gb.series.series_slug][gb.platform_group.key] = [r.base_satisfied_count, r.gating_count]
        if r is not None:
            xp = _group_badge_xp(r)
            if xp:
                group_xp[gb.series.series_slug][gb.platform_group.key] = xp

    # Read ONCE for the whole recompute: this seam writes one row per positive series plus the grand
    # total, and the profile's country is the same for all of them.
    country = _country_code(profile_id)

    for slug, s in positive.items():
        _upsert(SeriesBadgeStanding, {'profile_id': profile_id, 'series_slug': slug},
                {'xp': s.xp, 'progress_bp': s.progress_bp,
                 'stages_cleared': s.stages_cleared, 'stages_total': s.stages_total,
                 'group_progress': dict(group_prog.get(slug, {})),
                 'group_xp': dict(group_xp.get(slug, {})),
                 'advanced_at': s.advanced_at,
                 'country_code': country})
    if zeroed:
        SeriesBadgeStanding.objects.filter(profile_id=profile_id, series_slug__in=zeroed).delete()

    total = SeriesBadgeStanding.objects.filter(profile_id=profile_id).aggregate(t=Sum('xp'))['t'] or 0
    if total > 0:
        editions = edition_platforms()
        overall, by_edition = badge_trophy_tallies(profile_id, editions)
        _upsert(ProfileBadgeStanding, {'profile_id': profile_id},
                {'total_xp': total, 'country_code': country, **overall})
        _write_edition_standings(profile_id, country, by_edition)
    else:
        # No badge XP at all means no standing anywhere, editions included -- the boards read
        # ProfileBadgeStanding for the all-editions view and these rows for a slice, and one of them
        # holding a hunter the other has dropped is the kind of disagreement nobody would think to check.
        ProfileBadgeStanding.objects.filter(profile_id=profile_id).delete()
        ProfileEditionStanding.objects.filter(profile_id=profile_id).delete()


def _write_edition_standings(profile_id, country, by_edition):
    """Upsert the profile's per-edition standings, and drop the editions they no longer stand in.

    Per-edition XP is re-summed from EVERY one of the profile's SeriesBadgeStanding rows rather than from
    the results of the call that got us here. That is the same rule the grand total follows and it is what
    makes a scoped `--series` recompute safe: the call only knows about the series it evaluated, but this
    row is profile-wide.

    A row is kept when the profile has EITHER xp or trophies in that edition. Trophies without XP is a real
    state -- badge-game trophies earned without clearing a gating stage -- and it belongs on the Badge
    Trophies board even though it puts nothing on Badge Points.
    """
    from trophies.models import ProfileEditionStanding, SeriesBadgeStanding

    xp_by_edition = defaultdict(int)
    for blob in SeriesBadgeStanding.objects.filter(profile_id=profile_id).values_list('group_xp', flat=True):
        for key, xp in (blob or {}).items():
            xp_by_edition[key] += xp

    keep = []
    for key, counts in by_edition.items():
        xp = xp_by_edition.get(key, 0)
        if not xp and not counts['trophies_total']:
            continue
        keep.append(key)
        _upsert(ProfileEditionStanding, {'profile_id': profile_id, 'platform_group_key': key},
                {'total_xp': xp, 'country_code': country, **counts})
    ProfileEditionStanding.objects.filter(profile_id=profile_id).exclude(platform_group_key__in=keep).delete()


def _country_code(profile_id):
    """The profile's country, denormalized onto its standings so a country slice is an index range scan
    rather than a join-then-filter over a board-ordered scan."""
    from trophies.models import Profile
    return Profile.objects.filter(pk=profile_id).values_list('country_code', flat=True).first() or ''


_TIERS = ('platinum', 'gold', 'silver', 'bronze')


def _tally(rows):
    """{tier: n} -> the trophies_* field dict the standings store."""
    counts = {f'trophies_{tier}': rows.get(tier, 0) for tier in _TIERS}
    counts['trophies_total'] = sum(counts.values())
    return counts


def edition_platforms():
    """{platform_group_key: frozenset(platforms)} for every active edition.

    One query on a table with a handful of rows. Read per recompute rather than memoized: the groups are
    config a curator edits ("adding a group is a row, not a schema change"), and a process-lifetime cache
    would keep a batch run of `evaluate_badges --all` writing against the shape the table had when it
    started.
    """
    from trophies.models import PlatformGroup
    return {
        g.key: frozenset(g.platforms or [])
        for g in PlatformGroup.objects.filter(is_active=True).only('key', 'platforms')
    }


def trophy_groups(profile_id):
    """The grouped aggregate the tallies are built from: [(title_platform, trophy_type, count), ...].

    Named and returned as a list rather than inlined so its SIZE is inspectable, because that size is the
    whole performance argument. The Python loop that consumes it iterates THIS, not EarnedTrophy: Postgres
    does the counting and hands back one row per (distinct platform list x tier present).

    That result set is bounded by the CATALOGUE's platform vocabulary -- roughly a dozen real `title_platform`
    combinations x 4 tiers -- and is the same size for a hunter with 40 trophies and a whale with 250,000.
    It is the shape CLAUDE.md's "Good" example uses, not the `for et in queryset:` one it forbids;
    `test_the_aggregate_does_not_grow_with_the_library` pins it so a future edit cannot quietly turn this
    into a per-row iteration while still reading like an aggregate.

    WHAT THE EDITION SPLIT ACTUALLY COSTS, checked against the emitted SQL rather than assumed:

      + `INNER JOIN trophies_game ON trophy.game_id = game.id`. This is a REAL added join -- the
        `game_id IN (SELECT ...)` below reads Game under its own alias, so the outer query was not already
        joining it. It is a primary-key probe over the rows that SURVIVE that IN test (the profile's
        badge-game trophies), so it is strictly cheaper than the Trophy join on the same path already is.
        A constant factor, not a change in complexity class.
      + `GROUP BY` gains a jsonb column, which hashes less cheaply than the varchar it joins.

    Both land on the SYNC path (inside a per-profile recompute that already reads ProfileGame,
    ProfileTrophyGroup and the stage graph), never on a request. Rendering a board touches none of this:
    it is an indexed ORDER BY plus one hydrate.
    """
    from django.db.models import Count
    from trophies.models import EarnedTrophy, Game

    badge_games = Game.objects.filter(concept__stages__isnull=False)
    return list(
        EarnedTrophy.objects
        .filter(profile_id=profile_id, earned=True, trophy__game__in=badge_games)
        .values('trophy__game__title_platform', 'trophy__trophy_type')
        .annotate(n=Count('id'))
        .values_list('trophy__game__title_platform', 'trophy__trophy_type', 'n')
    )


def badge_trophy_tallies(profile_id, editions=None):
    """The Badge Trophies board's figures: trophies across every badge-stage game, by tier -- overall AND
    per platform edition. Returns (overall_counts, {edition_key: counts}).

    DB-aggregated in ONE grouped query, never iterated -- a whale holds 250k+ EarnedTrophy rows and
    counting them in Python is the documented OOM/timeout pattern.

    The game set is an `IN (subquery)`, and that is LOAD-BEARING: it dedupes by construction, so a game
    sitting in five different badges contributes its trophies exactly once. Rewriting this as a join
    through Stage would multiply each trophy by the number of badges containing its game and produce a
    number that inflates with catalogue growth rather than with play. It would also be slower. See the
    gotchas in docs/design/rebuild/leaderboards-rebuild.md.

    The per-edition split GROUPS BY `title_platform` rather than running a query per edition. The number of
    distinct platform lists in a catalogue is small and does not grow with the population, so this stays
    one query no matter how many editions get seeded -- which is the property that makes a future third
    group free. Routing each list to its editions happens in Python by INTERSECTION, deliberately the same
    rule `badge_engine._qualifies` applies, so an edition's trophies and its badges can never disagree
    about which games belong to it.

    A game qualifying for two editions counts in both. See ProfileEditionStanding on why that is right and
    why the editions therefore do not sum to the overall row.
    """
    editions = editions if editions is not None else edition_platforms()
    rows = trophy_groups(profile_id)

    overall = defaultdict(int)
    per_edition = {key: defaultdict(int) for key in editions}
    for platforms, tier, n in rows:
        overall[tier] += n
        owned = frozenset(platforms or ())
        for key, group_platforms in editions.items():
            if owned & group_platforms:
                per_edition[key][tier] += n

    return _tally(overall), {key: _tally(tiers) for key, tiers in per_edition.items()}


def badge_trophy_counts(profile_id):
    """Overall badge-game trophy counts only. `badge_trophy_tallies` is the one that does the work."""
    return badge_trophy_tallies(profile_id, editions={})[0]
