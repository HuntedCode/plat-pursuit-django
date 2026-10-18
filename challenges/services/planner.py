"""The Challenge Optimizer: plans a hunter's open A-Z and Job Coverage squares together. Read-only.

THE SPLIT. This module gathers the data and shapes the answer; `plan_solver` does the optimising on plain
data; `challenge_service.assign` stays the only writer, and Apply goes through it line by line. So a stale
plan can only ever produce a refusal, never a bad write -- the same contract `picker` keeps.

THE POOL IS THE EXPENSIVE PART, AND IT IS CACHED. Every live contract the hunter has not completed (on their
platforms, from their backlog or not), with the hunter's progress, every square key it fits in each planned
run type, and the uncleared badge stages it advances. That is a few catalogue-bounded scalar reads plus one
bounded badge evaluation, cached per (profile, options) for `POOL_TTL`. It does NOT depend on the runs'
current state: `fits` lists every key a contract fits, open or not, and the solver ignores keys that are not
open squares. So filling a square never makes the cache wrong. A re-solve (lock, exclude, pin, "different
games") skips the pool scan and the badge evaluation, but it is not free: it re-reads both runs' slots, the
bulk hatch counts and the importer check, a dozen or so small queries, plus the solve itself (~1s at full
size, see `plan_solver`).

THE CACHED PAYLOAD IS JSON-SAFE, because production's cache serializes to JSON: integer dict keys come back
as strings there (LocMem in tests pickles, which hides it). So the stored form is lists of pairs, rebuilt
on read by `_hydrate`. It carries no contract display fields (names, slugs, covers come from the DB per
response); a badge stage's names are stored ONCE per stage, and each contract refers to its stages by key.

ONE RULE PER QUESTION, all borrowed:
- "fits this square": `eligibility.fitting_keys_by_type`
- "completed": `eligibility.not_completed_by`
- "on these platforms": `eligibility.on_platforms`
- "progress": `contracts_service.max_progress_expression`, the Job Board's figure (bundle-aware)
- "uncleared badge stage": the badge engine itself (`badge_orchestrator.evaluate_with_catalog`)
- "hatch open": `eligibility.hatch_open_keys`, parity-pinned against `hatch_is_open`

BADGE STAGES NEVER COST A FILLED SQUARE. The badge bar (base list at 100%) is not the contract bar (platinum or
100%), so a plan line lists the stages a game counts toward. Under Bonus, badge games only choose between plans that
fill the same squares with the same number of games; under Priority they fill as many squares as they can before
the fewest games decide (`plan_solver`'s tier order). The pool is ranked badge games first (most stages first),
then progress, then name, so pruning keeps what the solver would pick.
"""
import hashlib
import json
from dataclasses import dataclass, field

from django.core.cache import cache
from django.db.models import Q
from django.db.models.functions import Lower

from challenges.models import CHALLENGE_TYPE_AZ, CHALLENGE_TYPE_JOBS
from challenges.services import challenge_service as svc
from challenges.services import build_line, eligibility, picker
from challenges.services.plan_solver import Candidate, Problem, solve
from trophies.models import (
    BadgeSeries, Contract, EarnedContract, GroupBadge, PlatformGroup, SeriesBadgeStanding, UserGroupBadge,
)
from trophies.services import badge_orchestrator
from trophies.services.contract_service import contract_by_concept_map
from trophies.services.contracts_service import max_progress_expression

#: The run types the planner plans. The Plat Calendar fills itself and has no squares to choose.
PLANNABLE_TYPES = (CHALLENGE_TYPE_AZ, CHALLENGE_TYPE_JOBS)

SOURCE_BACKLOG = 'backlog'
SOURCE_NEW = 'new'
SOURCE_BOTH = 'both'
SOURCES = (SOURCE_BOTH, SOURCE_BACKLOG, SOURCE_NEW)

#: How badge stages weigh against the challenges (the hunter's choice). BONUS: badge games only choose between plans
#: that fill the same squares with the same number of games. PRIORITY: badge games fill as many squares as they can,
#: even when that takes an extra game (never at the cost of a filled square). See `plan_solver`'s tiers.
#: ONLY (owner, 2026-10-16): the plan is built from badge games alone, so a square no badge game fits stays open;
#: Bonus and Priority fill those with any game. Must-haves still beat it, like every other filter. Ranked as
#: Priority, since every candidate is a badge game.
WEIGHT_BONUS = 'bonus'
WEIGHT_PRIORITY = 'priority'
WEIGHT_ONLY = 'only'
BADGE_WEIGHTS = (WEIGHT_BONUS, WEIGHT_PRIORITY, WEIGHT_ONLY)

#: What the plan does with squares that already hold a game the hunter has not finished (owner, 2026-10-18).
#: KEEP plans the open squares and suggests swaps. REPLAN ("Plan around them") puts every unfinished square in the
#: plan, a pick changing only when that is clearly better. FRESH ("Start fresh") plans them as if they were open: a
#: pick stays only where it is still the best fit.
PICKS_KEEP = 'keep'
PICKS_REPLAN = 'replan'
PICKS_FRESH = 'fresh'
PICKS_CHOICES = (PICKS_KEEP, PICKS_REPLAN, PICKS_FRESH)

#: Every badge type a hunter can select as a whole, in the Collection's own section order.
BADGE_TYPES = tuple(key for key, _ in BadgeSeries.BADGE_TYPES)

#: The most editions a hunter can pick one by one (a whole type or "All badges" covers more without a list).
MAX_PICKED_BADGES = 60


#: Seconds a built pool lives. Long enough for a review session of tweaks, short enough that a sync's
#: new progress shows up on the next visit.
POOL_TTL = 15 * 60
POOL_CACHE_PREFIX = 'planner:pool'
#: Bumped whenever the cached payload's shape, or what it may hold, changes, so a deploy never reads an old pool
#: (5: unobtainable contracts left out, 2026-10-17).
POOL_SCHEMA = 5

#: Pruning before the solve: how many candidates each open square keeps, in pool order, unpenalized first.
#: Doubles (a game that fits an open square in BOTH runs) are what the optimisation is about, so they get the
#: larger budget. For an A-Z square any budget of 1+ preserves the best fill, because a letter's games fit
#: no other letter. A JOB square competes: one game fits up to six jobs. So a job square keeps at least as
#: many as the run has open squares, which guarantees pruning never costs a filled square (some kept game
#: is always free for it in that run).
KEEP_DOUBLES = 20
KEEP_SINGLES = 4

UNPLACED_FINISHED = 'finished'       # the hunter has completed it
UNPLACED_GONE = 'gone'               # no longer on the Job Board
UNPLACED_NO_SQUARE = 'no_square'     # fits no square the plan fills (or not the square it was pinned to)
UNPLACED_IN_RUN = 'in_run'           # the only squares it fits are in runs that already hold it
UNPLACED_CONFLICT = 'conflict'       # fits, but other pins or better uses took its squares
UNPLACED_SQUARE_CLOSED = 'square_closed'   # pinned to a square that is no longer open or unfinished
UNPLACED_LEFT_OPEN = 'left_open'     # the only open squares it fits are ones the hunter is leaving open


@dataclass(frozen=True)
class PlannerOptions:
    """The hunter's answers. The badge step is a UNION of sources, any mix of them (owner, 2026-10-16): the
    editions they have started, every edition of whole badge types, everything, and editions picked by hand.
    None of them on means badges play no part."""
    types: tuple = PLANNABLE_TYPES
    platforms: tuple = ()
    source: str = SOURCE_BOTH
    badge_started: bool = True
    badge_all: bool = False
    badge_types: tuple = ()
    badge_picks: tuple = ()            # GroupBadge ids
    badge_weight: str = WEIGHT_BONUS
    picks: str = PICKS_KEEP

    @property
    def replan_picks(self):
        """Are the unfinished squares in the plan? Plan around them and Start fresh both say yes."""
        return self.picks in (PICKS_REPLAN, PICKS_FRESH)

    @property
    def fresh_picks(self):
        return self.picks == PICKS_FRESH

    @property
    def picked_badges(self):
        """The hand-picked editions: deduplicated, sorted, capped. The digest and the evaluation both read THIS,
        so two orderings of one choice can never share a key and build different pools."""
        return tuple(sorted(set(self.badge_picks)))[:MAX_PICKED_BADGES]

    @property
    def chosen_types(self):
        return tuple(sorted(set(self.badge_types) & set(BADGE_TYPES)))

    @property
    def badges_on(self):
        return bool(self.badge_started or self.badge_all or self.chosen_types or self.picked_badges)

    @property
    def badges_only(self):
        """Badge games alone? Only with a badge source on: "Only" with no badges selected would plan nothing."""
        return self.badges_on and self.badge_weight == WEIGHT_ONLY

    @property
    def stages_first(self):
        """Rank as Priority? Only with a badge source on: with none (the None chip, or nothing ticked) the weight is
        greyed out on the page, and it must not still reorder the solver's tiers (rerolls and swaps would differ)."""
        return self.badges_on and self.badge_weight in (WEIGHT_PRIORITY, WEIGHT_ONLY)

    def digest(self):
        """A stable key for the pool cache. Sorted, so chip order in a request cannot split it. The weight and
        `picks` are NOT in it: they change how the solver plans, not what the pool holds."""
        payload = json.dumps({
            'types': sorted(self.types), 'platforms': sorted(self.platforms), 'source': self.source,
            # "All badges" COVERS started and every type, so with it on those two say nothing about the pool:
            # leaving them in would split one pool across two keys (and make "the same plan" look different).
            'badges': ([False, True, [], list(self.picked_badges)] if self.badge_all
                       else [self.badge_started, False, list(self.chosen_types), list(self.picked_badges)]),
        }, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class Tweaks:
    """The review-step adjustments. All are contract ids, except `square_pins` ({(type, key): id}), `left_open`
    ({(type, key)}): squares the hunter is leaving open to fill themselves (owner, 2026-10-17), and `locks`
    ({(type, key)}): the square pins that LOCK the hunter's own pick there (owner, 2026-10-18). A lock is a square
    pin naming the square's occupant, marked so: once the square holds something else (filled by hand, or
    finished) the lock is stale and dropped, where an unmarked pin would put its game back over the new one."""
    pins: frozenset = frozenset()
    square_pins: dict = field(default_factory=dict)
    excluded: frozenset = frozenset()
    penalized: frozenset = frozenset()
    left_open: frozenset = frozenset()
    locks: frozenset = frozenset()


@dataclass
class RunState:
    challenge: object
    open_keys: list
    unfinished: dict      # {key: contract_id} assigned, not completed
    in_run: frozenset     # every contract id placed in the run
    snapshots: dict = field(default_factory=dict)   # {key: contract_slug} the slot's own snapshot, unfinished only
    names: dict = field(default_factory=dict)       # {key: contract_name} the same squares' snapshot names


@dataclass
class Plan:
    #: {(type, key): contract id} for open squares, unfinished squares the hunter pinned a swap into, and when the
    #: picks are planned (`PlannerOptions.replan_picks`) every unfinished square, its pick included where it stays.
    assignments: dict
    #: {(type, key): (current id, suggested id, [other squares the suggested game holds])} for unfinished
    #: squares. Solved ON TOP of `assignments`, so a plan square it lists is the plan's own. It may also list
    #: another unfinished square (that square's own swap, or the game staying where it already is) or an open
    #: square the swap solve could fill; `_plan_json` sorts those out for the card.
    swaps: dict
    #: {contract id: reason code} for pins the plan could not place.
    unplaced_pins: dict
    #: Open squares the plan leaves empty: nothing on the hunter's terms is left for them.
    empty: list
    #: Open squares the hunter is leaving open (`Tweaks.left_open`) that are still open today.
    left_open: list
    #: {type: [keys]} open squares where the <=3 hatch is open (notes, never auto-applied).
    hatch_keys: dict
    #: Whether the A-Z history importer could fill something (a note, never auto-applied).
    importer_note: bool
    #: {contract id: candidate dict} for every contract the plan mentions.
    candidates: dict
    #: Unfinished squares in `assignments` whose pick stays (picks planned): a line, but nothing to apply.
    staying: frozenset = frozenset()


# --- pool -------------------------------------------------------------------------------------------------

def pool_cache_key(profile, options):
    return f'{POOL_CACHE_PREFIX}:v{POOL_SCHEMA}:{profile.id}:{options.digest()}'


def get_pool(profile, options, *, build_if_missing=True, line_ticket=None, join_line=True):
    """The pool, from the cache or freshly built. Returns (pool, built) so the view can meter builds;
    (None, False) on a miss when `build_if_missing` is off. A whole-catalogue build may raise
    `build_line.LineWait` instead (the hunter waits their turn, re-sending `line_ticket`), or `build_line.LineBusy`
    when `join_line` is off."""
    key = pool_cache_key(profile, options)
    stored = cache.get(key)
    if stored is not None:
        return _hydrate(stored), False
    if not build_if_missing:
        return None, False
    stored = build_pool(profile, options, line_ticket=line_ticket, join_line=join_line)
    cache.set(key, stored, POOL_TTL)
    return _hydrate(stored), True


def build_pool(profile, options, line_ticket=None, join_line=True):
    """The STORED form: {'candidates': [[id, progress, fits]] in preference order, 'stages': [[id, [stage ref]]],
    'stage_names': {key: [series, series name, number, title]}, 'unobtainable': [id]} (the unobtainable contracts
    that passed the filters and were left out, so the plan can tell them from ones outside the filters).

    The stage map is kept whole beside the list so a pin from outside the pool gets its stages without a
    second badge evaluation, which is the expensive half of a build.

    A BROAD badge source ("All badges", a whole type) is a whole-catalogue build, so the WHOLE build runs in a
    site-wide slot (`build_line`), waiting in a fair line when none is free. Every broad build takes it, even one
    whose selection turns out empty: a ticket is used up by exactly one build, whatever that build finds."""
    if options.badge_all or options.chosen_types:
        with build_line.slot(line_ticket, user_id=profile.user_id, join=join_line):
            return _build_pool(profile, options)
    return _build_pool(profile, options)


def _build_pool(profile, options):
    stages = _badge_stages(profile, options)
    rows = _candidates(_rows(_filtered_contracts(profile, options.platforms, options.source)), options.types)
    # NEVER SUGGEST AN UNOBTAINABLE CONTRACT (owner, 2026-10-17): no game can finish it. Delisted ones stay. A
    # hunter may still name one as a must-have (`pin_candidates` reads outside the pool).
    unobtainable = picker.unobtainable_contract_ids()
    # Remembered, so a must-have among them is not called "outside your filters": it passed them.
    dropped = [c['id'] for c in rows if c['id'] in unobtainable]
    rows = [c for c in rows if c['id'] not in unobtainable]
    rows = _rank(rows, stages, options.source)
    # COMPACT: "All badges" hands nearly every contract some stages, and each stage's names repeat across all of
    # its games. Names go in `stage_names` once; a contract stores [key, part, editions] per stage.
    names, refs = {}, []
    for cid, items in stages.items():
        row = []
        for st in items:
            key = f"{st['series']}#{st['stage']}"
            names[key] = [st['series'], st['series_name'], st['stage'], st['title']]
            row.append([key, st['part'], st['editions']])
        refs.append([cid, row])
    return {'candidates': [[c['id'], c['progress'], c['fits']] for c in rows], 'stages': refs, 'stage_names': names,
            'unobtainable': dropped}


def _hydrate(stored):
    """The stored pool as the planner reads it: candidate dicts with their stages attached, int-keyed."""
    names = stored.get('stage_names', {})

    def expand(ref):
        key, part, editions = ref
        series, series_name, number, title = names[key]
        return {'series': series, 'series_name': series_name, 'stage': number, 'title': title, 'part': part,
                'editions': editions}

    stages = {int(cid): [expand(ref) for ref in refs] for cid, refs in stored['stages']}
    candidates = [{'id': cid, 'progress': progress, 'fits': fits, 'stages': stages.get(cid, [])}
                  for cid, progress, fits in stored['candidates']]
    return {'candidates': candidates, 'stages': stages, 'unobtainable': set(stored.get('unobtainable', ()))}


def pin_candidates(profile, options, pool, ids):
    """Candidate dicts for pinned contracts the pool does not hold. A PIN BEATS THE PLATFORM AND SOURCE
    FILTERS (owner, 2026-10-16), so neither applies here; completion still does, because `assign` would
    refuse a completed contract (outside the hatch/importer, which the planner never auto-applies)."""
    if not ids:
        return []
    rows = _candidates(_rows(_filtered_contracts(profile, (), SOURCE_BOTH).filter(pk__in=ids)), options.types)
    for cand in rows:
        cand['stages'] = pool['stages'].get(cand['id'], [])
    return rows


def _rows(contracts):
    return list(contracts.order_by(Lower('name'), 'pk').values_list('pk', 'max_progress'))


def _filtered_contracts(profile, platforms, source):
    # `live_contracts`, NOT the Job Board's `annotated_contracts`: the board drops jobless contracts, and a
    # jobless contract still fills an A-Z letter. Only the board's progress FIGURE is borrowed.
    contracts = eligibility.not_completed_by(profile, eligibility.live_contracts()).annotate(
        max_progress=max_progress_expression(profile))
    if platforms:
        contracts = eligibility.on_platforms(contracts, platforms)
    if source == SOURCE_BACKLOG:
        contracts = contracts.filter(max_progress__gt=0)
    elif source == SOURCE_NEW:
        contracts = contracts.filter(max_progress=0)
    return contracts


def _candidates(rows, types):
    ids = [pk for pk, _ in rows]
    fits = {t: eligibility.fitting_keys_by_type(t, ids) for t in types}
    out = []
    for pk, progress in rows:
        fit = {t: sorted(fits[t].get(pk, ())) for t in types}
        if any(fit.values()):
            out.append({'id': pk, 'progress': progress or 0, 'fits': fit, 'stages': []})
    return out


def _prefers_progress(source):
    """Progress ranks a plan only when the hunter's own games are in it. On a "new games" plan every
    candidate is at 0, so the tier would be all zeros anyway."""
    return source != SOURCE_NEW


def _rank(candidates, stages, source):
    """Order in the solver's (Bonus) tier order: badge stages, then progress (when it counts), then the database's
    `Lower(name), pk` order (a stable sort keeps it). Matching the solver is what lets pruning keep the
    candidates the solver would choose."""
    use_progress = _prefers_progress(source)
    candidates.sort(key=lambda c: (-len(stages.get(c['id'], ())), -c['progress'] if use_progress else 0))
    return candidates


# --- badge stages -----------------------------------------------------------------------------------------

def started_edition_ids(profile):
    """The LIVE editions (GroupBadge ids) the hunter has started and not finished: 0 < cleared < gating in their
    per-edition read-model (`SeriesBadgeStanding.group_progress`, the Collection's own source), minus the ones
    they HOLD (the read-model can stop tracking an edition once it is earned, so a stale pair is not proof).

    PER EDITION, not per series: the series-level `progress_bp` is the BEST edition's figure, so reading it hid
    an unfinished Ultra HD behind a finished Legacy HD. Shape-guarded like the Collection's reader: a malformed
    pair is skipped, never a 500 on the page that shows these. Three queries, bounded by the hunter's rows."""
    rows = SeriesBadgeStanding.objects.filter(profile=profile, progress_bp__gt=0).values_list('series_slug', 'group_progress')
    started = set()
    for slug, progress in rows:
        if not isinstance(progress, dict):
            continue
        for key, pair in progress.items():
            if (isinstance(pair, (list, tuple)) and len(pair) == 2
                    and all(isinstance(n, int) and not isinstance(n, bool) for n in pair) and 0 < pair[0] < pair[1]):
                started.add((slug, key))
    if not started:
        return []
    held = set(UserGroupBadge.objects.filter(profile=profile).values_list('group_badge_id', flat=True))
    slugs = {slug for slug, _ in started}
    return [gid for gid, slug, key in GroupBadge.objects.filter(is_live=True, series__series_slug__in=slugs)
            .order_by('series__series_slug', 'platform_group__sort_order', 'pk')
            .values_list('id', 'series__series_slug', 'platform_group__key')
            if (slug, key) in started and gid not in held]


def selected_group_badges(profile, options):
    """The live editions the hunter's badge sources add up to, ready for the engine.

    Started, whole types and "All badges" are scoped to editions on the chosen platforms: a Legacy HD stage is no
    reason to recommend a game to a PS5 hunter. HAND PICKS ARE NOT scoped: a hunter who picked an edition meant
    it, the way a must-have beats the platform filter. Platforms that no edition covers at all (PSVR alone) do
    not scope: scoping to nothing would quietly count nothing while the step says "Counting: All badges"."""
    if not options.badges_on:
        return []
    broad = GroupBadge.objects.filter(is_live=True)
    if not options.badge_all:
        q = Q(pk__in=started_edition_ids(profile)) if options.badge_started else Q(pk__in=[])
        if options.chosen_types:
            q |= Q(series__badge_type__in=options.chosen_types)
        broad = broad.filter(q)
    if options.platforms and _platforms_have_editions(options.platforms):
        broad = broad.filter(platform_group__platforms__overlap=list(options.platforms))
    ids = set(broad.values_list('id', flat=True)) | set(
        GroupBadge.objects.filter(is_live=True, pk__in=options.picked_badges).values_list('id', flat=True))
    if not ids:
        return []
    return badge_orchestrator.resolve_group_badges(GroupBadge.objects.filter(pk__in=ids))


def _platforms_have_editions(platforms):
    return PlatformGroup.objects.filter(group_badges__is_live=True, platforms__overlap=list(platforms)).exists()


def _badge_stages(profile, options):
    """{contract id: [stage dict]} for every uncleared, required stage of the selected editions.

    A stage is uncleared when the badge engine says it GATES for a selected edition and is not satisfied.
    Satisfaction is cross-platform, so one stage is listed once however many selected editions it gates; the
    dict names those editions. Bundle members are included as "part" of their stage, because the engine counts a
    bundle as one unit; a direct member of the same stage wins. Concepts map to contracts through
    `contract_by_concept_map`."""
    group_badges = selected_group_badges(profile, options)
    if not group_badges:
        return {}
    # THE CALLER'S INTENT picks the read shape, as `badge_orchestrator` asks of its callers (it removed a guessed
    # size threshold for exactly this reason): a broad source is a whole-catalogue question, started editions and
    # picks are a handful. (A broad build's site-wide slot is taken by `build_pool`, around the whole build.)
    return _stages_for(profile, group_badges, whole_catalogue=bool(options.badge_all or options.chosen_types))


def _stages_for(profile, group_badges, *, whole_catalogue):
    catalog = badge_orchestrator.build_catalog(group_badges, whole_catalogue=whole_catalogue)
    results = badge_orchestrator.evaluate_with_catalog(profile, catalog)

    uncleared = {}   # (series slug, stage number) -> [edition names it gates, uncleared]
    for gb in group_badges:
        for stage in results[gb.id].stages:
            if stage.gates and not stage.base_satisfied:
                uncleared.setdefault((gb.series.series_slug, stage.stage_number), []).append(gb.platform_group.name)
    if not uncleared:
        return {}

    names = {gb.series.series_slug: gb.series.name for gb in group_badges}
    members = {}   # concept id -> [(stage key, via bundle)]
    titles = {}
    for st in catalog['stages']:
        stage_key = (st.series_slug, st.stage_number)
        if stage_key not in uncleared:
            continue
        titles[stage_key] = st.title
        for concept in st.concepts.all():
            members.setdefault(concept.id, []).append((stage_key, False))
        for bundle in st.concept_bundles.all():
            for concept in bundle.concepts.all():
                members.setdefault(concept.id, []).append((stage_key, True))

    by_contract = {}
    for concept_id, contract in contract_by_concept_map(members, prefetch_jobs=False).items():
        seen = by_contract.setdefault(contract.id, {})
        for stage_key, via_bundle in members[concept_id]:
            # `part` stays True only if every route to this stage is through a bundle.
            seen[stage_key] = seen.get(stage_key, True) and via_bundle
    return {
        contract_id: [
            {'series': slug, 'series_name': names[slug], 'stage': number, 'title': titles[(slug, number)],
             'part': part, 'editions': sorted(set(uncleared[(slug, number)]))}
            for (slug, number), part in sorted(stage_map.items())
        ]
        for contract_id, stage_map in by_contract.items()
    }


# --- runs -------------------------------------------------------------------------------------------------

def run_states(profile, types):
    """{type: RunState} for the requested types that have an active run.

    `in_run` matches `assign`'s twin guard, BOTH clauses: the live FK, and a dead snapshot (FK nulled by a
    deleted contract) whose slug a contract now carries again. Otherwise the plan could offer a game Apply
    refuses as "already in another square". `snapshots` is the slot's own `contract_slug`, which is what Apply
    compares against: a staff rename leaves the snapshot stale and the live slug different."""
    states = {}
    for challenge_type in types:
        run = svc.active_run(profile, challenge_type)
        if run is None:
            continue
        open_keys, unfinished, snapshots, names, in_run, dead = [], {}, {}, {}, set(), set()
        for key, contract_id, slug, name, done in run.slots.values_list(
                'key', 'contract_id', 'contract_slug', 'contract_name', 'is_completed'):
            if contract_id is not None:
                in_run.add(contract_id)
            elif slug:
                dead.add(slug)
            if done:
                continue
            if not slug:
                open_keys.append(key)
            elif contract_id is not None:
                unfinished[key] = contract_id
                snapshots[key] = slug
                names[key] = name
        if dead:
            in_run |= set(Contract.objects.filter(slug__in=dead).values_list('pk', flat=True))
        states[challenge_type] = RunState(run, open_keys, unfinished, frozenset(in_run), snapshots, names)
    return states


# --- plan -------------------------------------------------------------------------------------------------

def make_plan(profile, options, pool, states, tweaks=Tweaks()):
    """Solve the open squares, then (if any square is unfinished) a second solve ON TOP of that plan that may
    suggest swapping unfinished squares. When the picks are planned (`options.replan_picks`: Plan around them, Start
    fresh) it is ONE solve instead, over the open AND unfinished squares: see below.

    `pool` is `get_pool`'s hydrated pool; pins missing from it are fetched by `pin_candidates`.

    SQUARE PINS, three cases. On an open square: the hunter's pick for it. On an unfinished square: an
    accepted swap, so that square joins the primary solve as if it were open, holding the pinned game (its old
    occupant stays in `in_run` and is never planned into another square of that run: Apply replaces games in
    place). When the picks are planned the same pin is simply the hunter's pick for that square. Naming the
    square's own occupant means "keep it": dropped under Keep (nothing plans that square), and when the picks are
    planned it LOCKS the pick there through re-solves. On a square that is no longer open or unfinished: reported as
    `UNPLACED_SQUARE_CLOSED`."""
    occupants = {(t, k): cid for t, s in states.items() for k, cid in s.unfinished.items()}
    open_squares = [(t, k) for t, s in states.items() for k in s.open_keys]
    plannable = set(open_squares) | set(occupants)
    replan = options.replan_picks
    # LEFT OPEN (owner, 2026-10-17): the hunter will fill these themselves, so neither solve touches them. It
    # beats a square pin on the same square (the latest word wins, as with "not this one"). Only an OPEN square
    # can be left open: once the hunter fills it by hand it is theirs, planned like any unfinished square, and
    # the entry is forgotten (the plan door saves only what `Plan.left_open` returns).
    open_set = set(open_squares)
    left_open = [sq for sq in sorted(tweaks.left_open) if sq in open_set]
    skipped = set(left_open)
    open_squares = [sq for sq in open_squares if sq not in skipped]

    square_pins, closed, locked_picks = {}, set(), {}
    for square, cid in tweaks.square_pins.items():
        # "NOT THIS ONE" BEATS A PIN, first, so an excluded game never displaces the pick on its square.
        if square in skipped or cid in tweaks.excluded:
            continue
        if square in tweaks.locks and occupants.get(square) != cid:
            continue   # a STALE lock: the square holds something else now (or is finished)
        if square not in plannable:
            closed.add(cid)
        elif occupants.get(square) != cid:
            square_pins[square] = cid
        elif replan:
            locked_picks[square] = cid   # a LOCKED PICK: held on its own square through re-solves
    pinned_unfinished = [sq for sq in occupants if sq in square_pins]
    open_squares += pinned_unfinished
    occupants = {sq: cid for sq, cid in occupants.items() if sq not in square_pins}
    in_run = {t: s.in_run for t, s in states.items()}

    # "NOT THIS ONE" BEATS A PIN: the hunter's latest word on a game wins, so an excluded game is never
    # re-fetched as a pin (square pins were filtered above).
    pins = frozenset(tweaks.pins) - tweaks.excluded
    unpicked = []
    if replan:
        # "NOT THIS ONE" ON A PICK (picks planned): the hunter wants it changed. Its square is planned like an open
        # one, and the game, still in its run, can never come back to it. It beats a lock, as with any game.
        unpicked = [sq for sq, cid in occupants.items() if cid in tweaks.excluded]
        open_squares += unpicked
        occupants = {sq: cid for sq, cid in occupants.items() if cid not in tweaks.excluded}
        locked_picks = {sq: cid for sq, cid in locked_picks.items() if sq in occupants}
    all_pins = set(pins) | set(square_pins.values())
    candidates = [c for c in pool['candidates'] if c['id'] not in tweaks.excluded and _allowed(c, options)]
    have = {c['id'] for c in candidates}
    candidates += pin_candidates(profile, options, pool, all_pins - have)
    by_id = {c['id']: c for c in candidates}
    prefer = _prefers_progress(options.source)
    stages_first = options.stages_first

    # Every occupant's candidate data, read once: one outside the pool keeps its stages (`_occupants_outside`).
    known = {**by_id, **_occupants_outside(profile, options, pool, occupants, by_id)} if occupants else by_id

    def occupant_solve(held, keep, keep_occupants=True):
        """The open AND unfinished squares, each occupant a candidate that may stay (the keep tier, unless
        `keep_occupants` is off). `held`: the square pins it must honour; `keep`: the pins pruning must keep."""
        squares = open_squares + list(occupants)
        pruned = _prune(candidates, squares, keep, tweaks.penalized, in_run, held)
        return solve(Problem(
            squares=tuple(squares), candidates=_with_occupants(pruned, occupants, known),
            pins=pins, square_pins=held, in_run=in_run, occupants=occupants, keep_occupants=keep_occupants,
            penalized=frozenset(tweaks.penalized), prefer_progress=prefer, stages_first=stages_first,
        ))

    if replan:
        # THE PICKS ARE PLANNED (owner, 2026-10-18): every unfinished square joins the one solve, its game a
        # candidate that may stay. "Plan around them" keeps the keep tier, so a pick changes only when a higher tier
        # wants its square, exactly as a swap is suggested: a pin, a filled square, a saved game, and under Priority
        # a badge game. "Start fresh" drops it, so a pick stays only on its merits. Either way a pick that stays is
        # a line too (`staying`, nothing to apply), so the hunter sees the whole plan; nothing is left to suggest.
        primary = occupant_solve({**square_pins, **locked_picks}, all_pins, keep_occupants=not options.fresh_picks)
    else:
        primary = solve(Problem(
            squares=tuple(open_squares),
            candidates=_prune(candidates, open_squares, all_pins, tweaks.penalized, in_run, square_pins),
            pins=pins, square_pins=square_pins, in_run=in_run,
            penalized=frozenset(tweaks.penalized), prefer_progress=prefer, stages_first=stages_first,
        ))
    assignments = primary.assignments

    swaps = {}
    if occupants and not replan:
        # THE PRIMARY PLAN IS FIXED: every square it filled is square-pinned here, so a suggested game's other
        # squares are the plan's own and a swap can never contradict a line the hunter sees above it.
        held = {**primary.assignments, **square_pins}
        swap = occupant_solve(held, all_pins | set(held.values()))
        for sq, cid in occupants.items():
            new = swap.assignments.get(sq, cid)
            if new != cid:
                also = [other for other, placed in swap.assignments.items() if placed == new and other != sq]
                swaps[sq] = (cid, new, also)

    unplaced = {cid: _why_unplaced(cid, by_id, states, square_pins, skipped, replan)
                for cid in primary.unplaced_pins}
    unplaced.update(_missing_reasons(profile, [cid for cid in unplaced if cid not in by_id]))
    placed = set(primary.assignments.values())
    unplaced.update({cid: UNPLACED_SQUARE_CLOSED for cid in closed if cid not in placed})

    mentioned = set(assignments.values()) | {new for _, new, _ in swaps.values()}
    return Plan(
        assignments=assignments,
        swaps=swaps,
        unplaced_pins=unplaced,
        # A pinned unfinished square the pin could not take still holds its game, so it is not "empty". A "not this
        # one" pick nothing else can replace IS: still open says so, rather than the square vanishing.
        empty=[sq for sq in open_squares if sq not in primary.assignments and sq not in pinned_unfinished],
        left_open=left_open,
        hatch_keys={t: sorted(eligibility.hatch_open_keys(profile, s.challenge, s.open_keys))
                    for t, s in states.items() if s.open_keys},
        importer_note=_importer_note(profile, states),
        candidates={cid: known[cid] for cid in mentioned if cid in known},
        staying=frozenset(sq for sq, cid in occupants.items() if assignments.get(sq) == cid),
    )


def _allowed(cand, options):
    """May this pool candidate be planned under these answers? Everything, unless the hunter asked for badge games
    only. (Pins and a square's own occupant are not pool candidates here: they are always allowed.)"""
    return bool(cand['stages']) or not options.badges_only


def alternatives(pool, states, square, excluded=frozenset(), options=None, partners=(), left_open=frozenset(),
                 badge_only=False):
    """{'both': [...], 'rest': [...], 'partner': [...]} of (candidate dict, [open squares it also fits in the
    OTHER run, and its picks when the picks are planned]) for one square and its line, EVERY one of them (owner, 2026-10-17:
    the sheet shows everything, paged by its door). Reads only the cached pool and the run state: the "Other options" sheet costs no pool scan.

    PARTNERS are the other squares of the plan line the hunter opened it from (owner, 2026-10-17), one at most (a
    line holds one square per run). `both`: games that fit the square AND the partner (the sheet pins one to the
    whole line). `rest`: games for the square alone (all of them when there is no partner). `partner`: games for
    the partner alone, that the square cannot take (the sheet pins one to the partner). `both` keeps pool order (its
    other fits are moot); the other two list doubles first, then pool order. A partner is never repeated in `also`,
    and it may be an unfinished square (an accepted swap), so it is matched on fit, not on being open. Pass the
    line's own game in `excluded` (it fits every partner). A square the hunter is leaving open (`left_open`) is never
    "also": the plan will not fill it. `badge_only` (the sheet's "Counts toward your badges") keeps only games that
    clear a stage of the hunter's badges.

    A game already in a run is never offered for that run's squares, here or in `also` (it cannot be placed
    there twice); excluded games are not offered at all, because the hunter said "not this one". With badge
    games only (`options`), neither is a game that clears no stage."""
    run_type, key = square
    state = states.get(run_type)
    if state is None:
        return {'both': [], 'rest': [], 'partner': []}
    partners = [(t, k) for t, k in partners if t in states and t != run_type]

    def offered(cand):
        return (cand['id'] not in excluded and (options is None or _allowed(cand, options))
                and (not badge_only or cand['stages']))

    replan = options is not None and options.replan_picks

    def also_of(cand, own_run, skip):
        # When the picks are planned the other run's picks are squares the plan fills too (the door passes a
        # locked one in `left_open`: it takes nothing else).
        return [(t, k) for t, s in states.items() if t != own_run and cand['id'] not in s.in_run
                for k in (list(s.open_keys) + sorted(s.unfinished) if replan else s.open_keys)
                if k in cand['fits'].get(t, ()) and (t, k) not in skip and (t, k) not in left_open]

    found = []
    for cand in pool['candidates']:
        if cand['id'] in state.in_run or key not in cand['fits'].get(run_type, ()) or not offered(cand):
            continue
        covers = bool(partners) and all(k in cand['fits'].get(t, ()) and cand['id'] not in states[t].in_run
                                        for t, k in partners)
        found.append((cand, also_of(cand, run_type, partners), covers))
    rest = sorted(((c, a) for c, a, covers in found if not covers), key=lambda row: not row[1])   # doubles first

    partner_only = []
    if len(partners) == 1:
        (p_run, p_key), = partners
        taken = {c['id'] for c, _, _ in found}
        p_state = states[p_run]
        for cand in pool['candidates']:
            if (cand['id'] in taken or cand['id'] in p_state.in_run or p_key not in cand['fits'].get(p_run, ())
                    or not offered(cand)):
                continue
            partner_only.append((cand, also_of(cand, p_run, {square})))
        partner_only.sort(key=lambda row: not row[1])   # doubles first
    return {'both': [(c, a) for c, a, covers in found if covers], 'rest': rest, 'partner': partner_only}


def _prune(pool, squares, pins, penalized, in_run, square_pins):
    """The solver's candidates: every pin, plus per open square its best few in pool order (unpenalized
    first). See `KEEP_DOUBLES` for the budgets and why a job square's scales with the run's open squares.

    Fits the solver would refuse are not counted against a budget: a game already in a run cannot use that
    run's squares, a square-pinned square takes only its pin, and a square-pinned game takes no other square
    of its run."""
    open_by_type = {}
    for t, k in squares:
        if (t, k) not in square_pins:
            open_by_type.setdefault(t, set()).add(k)
    # A game square-pinned in a run may stand nowhere else in that run (the solver's `fixed_square` rule), so
    # like a game already in the run it must not use that run's other budgets.
    fixed_in = {(t, cid) for (t, _), cid in square_pins.items()}

    def limit(t, is_double):
        base = KEEP_DOUBLES if is_double else KEEP_SINGLES
        return base if t == CHALLENGE_TYPE_AZ else max(base, len(open_by_type.get(t, ())))

    keep = set(pins)
    budgets = {}
    for cand in sorted(pool, key=lambda c: c['id'] in penalized):   # stable: pool order within each half
        fits = {t: (set(cand['fits'].get(t, ())) & keys)
                if cand['id'] not in in_run.get(t, ()) and (t, cand['id']) not in fixed_in else set()
                for t, keys in open_by_type.items()}
        is_double = sum(1 for keys in fits.values() if keys) >= 2
        for t, keys in fits.items():
            for k in keys:
                used = budgets.get((t, k, is_double), 0)
                if used < limit(t, is_double):
                    budgets[(t, k, is_double)] = used + 1
                    keep.add(cand['id'])
    return tuple(_as_candidate(c) for c in pool if c['id'] in keep)


def _as_candidate(cand):
    return Candidate(id=cand['id'], fits={t: frozenset(k) for t, k in cand['fits'].items()},
                     stages=len(cand['stages']), progress=cand['progress'])


def _occupants_outside(profile, options, pool, occupants, by_id):
    """{id: candidate dict} for occupants the pool does not hold (filtered out, or unobtainable and so never
    suggested), read the way a pin is, so the swap solve still knows their badge stages and progress. Without
    it they arrived with neither, and under Priority a hunter's own badge game looked worth swapping out."""
    missing = set(occupants.values()) - set(by_id)
    return {c['id']: c for c in pin_candidates(profile, options, pool, missing)} if missing else {}


def _with_occupants(pruned, occupants, by_id):
    """`pruned` with every occupant present and able to stay on its own square.

    An occupant may be missing from the pruned set (pruning dropped it, or the pool filtered it out by
    platform or source), may already be in it, and may occupy a square in BOTH runs. So occupants are merged
    by id, never appended twice (the solver refuses duplicate ids), and each gains its own square in `fits`
    (a staff edit to its jobs could otherwise leave it unable to "stay")."""
    own = {}
    for (t, k), cid in occupants.items():
        own.setdefault(cid, {}).setdefault(t, set()).add(k)
    out = []
    for cand in pruned:
        extra = own.pop(cand.id, None)
        if extra:
            fits = dict(cand.fits)
            for t, keys in extra.items():
                fits[t] = frozenset(fits.get(t, frozenset()) | keys)
            cand = Candidate(id=cand.id, fits=fits, stages=cand.stages, progress=cand.progress)
        out.append(cand)
    for cid, keys_by_type in own.items():
        source = by_id.get(cid)
        out.append(Candidate(
            id=cid, fits={t: frozenset(keys) for t, keys in keys_by_type.items()},
            stages=len(source['stages']) if source else 0, progress=source['progress'] if source else 0))
    return tuple(out)


def _why_unplaced(contract_id, by_id, states, square_pins, left_open=frozenset(), replan=False):
    """Why a pin the solver held did not land. A contract absent from `by_id` is answered by
    `_missing_reasons` instead (it never reached the solver). A square the hunter is leaving open is not one it
    could have taken, so a pin whose only open squares are those says so rather than blaming other picks. When the
    picks are planned (`replan`) an unfinished square is one it could have taken too."""
    cand = by_id.get(contract_id)
    if cand is None:
        return UNPLACED_NO_SQUARE
    for (t, k), cid in square_pins.items():
        if cid == contract_id and k not in cand['fits'].get(t, ()):
            return UNPLACED_NO_SQUARE
    fits_somewhere = fits_left_open = False
    for t, state in states.items():
        keys = set(cand['fits'].get(t, ())) & (set(state.open_keys) | (set(state.unfinished) if replan else set()))
        plannable = {k for k in keys if (t, k) not in left_open}
        if plannable:
            if contract_id not in state.in_run:
                return UNPLACED_CONFLICT
            fits_somewhere = True
        elif keys and contract_id not in state.in_run:
            fits_left_open = True
    if fits_left_open:
        return UNPLACED_LEFT_OPEN
    return UNPLACED_IN_RUN if fits_somewhere else UNPLACED_NO_SQUARE


def _missing_reasons(profile, ids):
    """Reasons for pins that never reached the solver: off the Job Board, already finished, or fitting no
    square of the planned types. Two queries, bounded by the pins."""
    if not ids:
        return {}
    live = set(eligibility.live_contracts().filter(pk__in=ids).values_list('pk', flat=True))
    finished = set(EarnedContract.objects.filter(profile=profile, contract_id__in=ids)
                   .values_list('contract_id', flat=True))
    return {cid: (UNPLACED_GONE if cid not in live else UNPLACED_FINISHED if cid in finished
                  else UNPLACED_NO_SQUARE) for cid in ids}


def _importer_note(profile, states):
    state = states.get(CHALLENGE_TYPE_AZ)
    if state is None or not state.open_keys or not svc.importer_is_available(profile, CHALLENGE_TYPE_AZ):
        return False
    return eligibility.completed_contracts_across_keys(profile, state.challenge, state.open_keys).exists()
