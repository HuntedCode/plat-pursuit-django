"""The Challenge Optimizer's solver. PURE: no ORM, no Django, so it is tested on plain data.

THE PROBLEM. A hunter has open squares across up to two runs (A-Z letters, Job Coverage jobs). Each
candidate contract fits some of those squares: exactly one letter (its raw initial) and any number of jobs.
A run holds a contract once. Choose at most one contract per square so that, in priority order:

    0. the most PINS are honoured (a contract the hunter asked for, or a contract fixed to one square)
    1. the most squares are filled
    2. the fewest distinct games are used (a game that takes its letter AND a job counts once)
    3. (swap mode, and the planner's Replan) unfinished squares keep their current game, so one is replaced only
       when that serves a higher tier: a pin, a filled square, or a saved game
    4. games the hunter asked to see less of ("Show me different games") are avoided
    5. the most squares are filled by BADGE GAMES (a game that advances at least one uncleared stage)
    6. the most badge stages are advanced, as a sum of each chosen game's count (a tie-break between badge games)
    With `stages_first` (the hunter's "Priority" badge weight) the order after 1 becomes 4, 5, 2, 3, 6 (owner,
    2026-10-17): badge games fill as many squares as they can, and only then do the fewest games decide. So badge
    doubles are used first, and where they run out a badge game that takes one square beats a double without a badge.
    Under Bonus the fewest games decide first, so badge games win only where they cost no extra game.
    7. (backlog preference) games the hunter has progress in are preferred
    8. earlier candidates are preferred (the caller orders them)

WHY BADGE SQUARES, NOT STAGE COUNTS, ABOVE GAMES. Priority used to rank the stage COUNT above fewer games, so any
new stage was worth an extra game: on "All badges", where nearly every game clears something, it split every double
and used one game per square. A yes/no per game keeps the doubles (a badge double fills two squares with one game),
and it keeps every tier linear in the assignment, so it costs one more quick tier, not a harder solve. Counting each stage once per
plan instead was tried and measured: it made a coverage problem HiGHS could not settle inside the time budget.

LEXICOGRAPHIC, SOLVED IN SEQUENCE. Each tier is one small exact MILP, and its optimum is then fixed as a
constraint for the next. The alternative, one solve with big-M weights per tier, needs weights near 1e10 for
51 squares, and HiGHS's default relative gap (1e-4) would then silently ignore every tier below the first
two. Every tier also runs with `mip_rel_gap=0` for the same reason: the tie tier's objective reaches five
figures at full size, where the default gap could stop short of the optimum. Tiers whose objective is all zero
are skipped.

TIER 8 IS A PREFERENCE, NOT A STRICT ORDER. It minimises the sum of the chosen games' positions, so {1st, 4th}
and {2nd, 3rd} tie, and it does not decide which of two squares one game lands on. HiGHS settles whatever is
left, and it is deterministic for identical input.

PINS ARE TIER 0, NOT HARD CONSTRAINTS, so two pins that want the same letter cannot make the model
infeasible: the solver places as many as it can and reports the rest in `unplaced_pins`, which the planner
turns into a reason the hunter can read.

TIMING, measured with a scratch script on the dev laptop (2026-10-16), not by a test: 26 letters + 25 jobs with
1,040 candidates (~3,070 pairs) took 0.1-0.3s per tier, ~1s for five tiers (a badge plan adds one or two). Pool size is the lever, and
keeping it small is the planner's job (`planner._prune`).

WHAT THIS MODULE ENFORCES vs WHAT IT TRUSTS. It enforces the structural rules: one game per square, one
square per game per run, `in_run` (a game already in a run is not placed there again), and an occupant stays
on its own square or leaves the run's plan. Rules about which contracts may go WHERE (shape, platform,
completion) are the caller's, via `eligibility`, and arrive pre-applied in `Candidate.fits`.
"""
import time
from dataclasses import dataclass, field

Square = tuple[str, str]          # (run type, slot key)

#: Wall-clock budget in seconds for one whole solve, shared by its tiers. A full-size solve measures ~1s;
#: the budget exists so a pathological input returns the best plan found so far instead of hanging a request.
SOLVE_TIME_BUDGET = 4.0

#: Every tier's coefficients are integers, so its optimum is an integer. Fixing it as `>= optimum - half`
#: keeps float noise in HiGHS's solution from making the next tier infeasible.
INTEGRALITY_SLACK = 0.5


@dataclass(frozen=True)
class Candidate:
    """One contract the solver may place. `fits` maps a run type to the square keys the contract fits there
    (keys that are not open squares are ignored)."""
    id: int
    fits: dict[str, frozenset[str]]
    stages: int = 0
    progress: int = 0


@dataclass(frozen=True)
class Problem:
    #: Squares to plan. In swap mode this includes the unfinished assigned squares.
    squares: tuple[Square, ...]
    #: Candidates in PREFERENCE order, unique ids.
    candidates: tuple[Candidate, ...]
    #: Contract ids the hunter asked for, placed anywhere they fit.
    pins: frozenset[int] = frozenset()
    #: {square: contract id} the hunter fixed to one square.
    square_pins: dict[Square, int] = field(default_factory=dict)
    #: {run type: contract ids} already in that run, never placed there again.
    in_run: dict[str, frozenset[int]] = field(default_factory=dict)
    #: {square: contract id} current occupants of unfinished squares (swap mode). Each must be a candidate.
    occupants: dict[Square, int] = field(default_factory=dict)
    #: Contract ids to avoid when an equally good plan exists without them.
    penalized: frozenset[int] = frozenset()
    prefer_progress: bool = False
    #: The hunter's "Priority" badge weight: see the module docstring for the reordered tiers. Never above filling.
    stages_first: bool = False


@dataclass(frozen=True)
class Solution:
    assignments: dict[Square, int]
    #: Pinned contract ids (from `pins` or `square_pins`) the solver could not place.
    unplaced_pins: frozenset[int]


def solve(problem: Problem) -> Solution:
    # IMPORTED HERE, not at module level: `urls.py` imports the planner's views at startup, and a module-level
    # scipy import would load it into every web and worker process whether or not anybody plans.
    import numpy as np
    from scipy.optimize import Bounds, LinearConstraint, milp

    _validate(problem)
    pairs = _allowed_pairs(problem)
    all_pins = frozenset(problem.pins) | frozenset(problem.square_pins.values())
    if not pairs:
        return Solution(assignments={}, unplaced_pins=all_pins)

    order = {c.id: i for i, c in enumerate(problem.candidates)}
    by_id = {c.id: c for c in problem.candidates}
    placeable_ids = sorted({cid for cid, _ in pairs}, key=order.__getitem__)
    n_x = len(pairs)
    u_index = {cid: n_x + i for i, cid in enumerate(placeable_ids)}
    n = n_x + len(placeable_ids)

    base = _structural_constraints(pairs, u_index, n)
    tiers = _tiers(problem, pairs, u_index, placeable_ids, by_id, order, n)

    deadline = time.monotonic() + SOLVE_TIME_BUDGET
    fixed = []
    best_x = None
    for objective in tiers:
        remaining = deadline - time.monotonic()
        if remaining <= 0 and best_x is not None:
            break
        result = milp(
            c=-objective,
            constraints=[*base, *fixed],
            integrality=np.ones(n),
            bounds=Bounds(0, 1),
            options={'time_limit': max(remaining, 0.1), 'mip_rel_gap': 0},
        )
        if result.x is None:
            if best_x is None:
                raise RuntimeError(f'Planner solve failed: {result.message}')
            break   # out of time on a later tier: keep the plan the earlier tiers settled
        best_x = result.x
        best = round(float(objective @ best_x))
        fixed.append(LinearConstraint(objective.reshape(1, -1), lb=best - INTEGRALITY_SLACK, ub=np.inf))

    chosen = best_x > INTEGRALITY_SLACK
    assignments = {square: cid for (cid, square), on in zip(pairs, chosen[:n_x]) if on}
    placed_ids = set(assignments.values())
    unplaced = {cid for cid in problem.pins if cid not in placed_ids}
    unplaced |= {cid for square, cid in problem.square_pins.items() if assignments.get(square) != cid}
    return Solution(assignments=assignments, unplaced_pins=frozenset(unplaced))


def _validate(problem):
    ids = [c.id for c in problem.candidates]
    if len(ids) != len(set(ids)):
        raise ValueError('candidate ids must be unique')
    missing = set(problem.occupants.values()) - set(ids)
    if missing:
        raise ValueError(f'occupants must be candidates: {sorted(missing)}')


def _allowed_pairs(problem):
    """Every (contract id, square) the model may choose, in a stable order."""
    pinned_square_of = {(run, cid): (run, key) for (run, key), cid in problem.square_pins.items()}
    occupied_run = {(run, cid) for (run, _), cid in problem.occupants.items()}
    pairs = []
    for cand in problem.candidates:
        for square in problem.squares:
            run, key = square
            if key not in cand.fits.get(run, frozenset()):
                continue
            pinned_id = problem.square_pins.get(square)
            if pinned_id is not None and pinned_id != cand.id:
                continue
            fixed_square = pinned_square_of.get((run, cand.id))
            if fixed_square is not None and fixed_square != square:
                continue
            # An occupant counts as in its run even if the caller left it out of `in_run`, so it can only
            # ever stay on its own square: Apply replaces games in place and never moves one.
            in_this_run = cand.id in problem.in_run.get(run, ()) or (run, cand.id) in occupied_run
            if in_this_run and problem.occupants.get(square) != cand.id:
                continue
            pairs.append((cand.id, square))
    return pairs


def _structural_constraints(pairs, u_index, n):
    import numpy as np
    from scipy.optimize import LinearConstraint
    from scipy.sparse import coo_array

    rows, cols, vals, ub = [], [], [], []

    def row(entries, upper):
        r = len(ub)
        for col, val in entries:
            rows.append(r)
            cols.append(col)
            vals.append(val)
        ub.append(upper)

    by_square, by_cand_run, by_cand = {}, {}, {}
    for i, (cid, square) in enumerate(pairs):
        by_square.setdefault(square, []).append(i)
        by_cand_run.setdefault((cid, square[0]), []).append(i)
        by_cand.setdefault(cid, []).append(i)

    for xs in by_square.values():                      # one game per square
        row([(i, 1) for i in xs], 1)
    for xs in by_cand_run.values():                    # one square per game per run
        row([(i, 1) for i in xs], 1)
    for cid, xs in by_cand.items():                    # u[c] is 1 exactly when c is placed somewhere
        u = u_index[cid]
        for i in xs:
            row([(i, 1), (u, -1)], 0)
        row([(u, 1), *((i, -1) for i in xs)], 0)

    matrix = coo_array((vals, (rows, cols)), shape=(len(ub), n)).tocsr()
    return [LinearConstraint(matrix, lb=-np.inf, ub=np.array(ub, dtype=float))]


def _tiers(problem, pairs, u_index, placeable_ids, by_id, order, n):
    """The objective vectors, highest priority first, with all-zero tiers dropped."""
    import numpy as np

    def zeros():
        return np.zeros(n)

    pins, fill, games, keep, avoid, badge, stages, progress, ties = (zeros() for _ in range(9))
    for cid in problem.pins:
        if cid in u_index:
            pins[u_index[cid]] = 1
    for i, (cid, square) in enumerate(pairs):
        fill[i] = 1
        if by_id[cid].stages:
            badge[i] = 1
        if problem.square_pins.get(square) == cid:
            pins[i] = 1
        if problem.occupants.get(square) == cid:
            keep[i] = 1
    for cid in placeable_ids:
        u = u_index[cid]
        games[u] = -1
        if cid in problem.penalized:
            avoid[u] = -1
        stages[u] = int(by_id[cid].stages)
        if problem.prefer_progress:
            progress[u] = int(by_id[cid].progress)
        ties[u] = len(order) - order[cid]

    # PRIORITY moves "avoid" up WITH the badge tier: otherwise "Different games" would keep returning the same
    # badge games. "keep" stays below it, so an unfinished square may be OFFERED a swap for a badge game (a swap is
    # always opt-in), or under the planner's Replan be planned with one.
    # WHEN EVERY PLACEABLE GAME IS A BADGE GAME ("Badges only", or nearly all of "All badges") the badge tier IS the
    # fill tier, already settled above it: dropped, so it does not spend the shared time budget proving nothing.
    if np.array_equal(badge, fill):
        badge = zeros()
    order = ((pins, fill, avoid, badge, games, keep, stages, progress, ties) if problem.stages_first
             else (pins, fill, games, keep, avoid, badge, stages, progress, ties))
    return [t for t in order if t.any()]
