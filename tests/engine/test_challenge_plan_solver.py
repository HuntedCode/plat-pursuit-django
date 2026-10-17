"""The Challenge Optimizer's pure solver: one test per priority tier, plus the edges. No DB."""
import pytest

from challenges.services.plan_solver import Candidate, Problem, solve

AZ, JOBS = 'az', 'jobs'


def cand(cid, letter=None, jobs=(), stages=0, progress=0):
    fits = {}
    if letter:
        fits[AZ] = frozenset({letter})
    if jobs:
        fits[JOBS] = frozenset(jobs)
    return Candidate(id=cid, fits=fits, stages=stages, progress=progress)


def az(*letters):
    return tuple((AZ, k) for k in letters)


def jobs(*keys):
    return tuple((JOBS, k) for k in keys)


def test_empty_problem_places_nothing():
    result = solve(Problem(squares=(), candidates=()))
    assert result.assignments == {}
    assert result.unplaced_pins == frozenset()


def test_a_game_that_fits_a_letter_and_a_job_is_used_for_both():
    problem = Problem(
        squares=az('A', 'B') + jobs('brawler'),
        candidates=(cand(2, letter='B'), cand(3, jobs=['brawler']), cand(1, letter='A', jobs=['brawler'])),
    )
    result = solve(problem)
    assert result.assignments == {(AZ, 'A'): 1, (AZ, 'B'): 2, (JOBS, 'brawler'): 1}


def test_every_square_that_can_be_filled_is_filled():
    # Fails if the fill tier goes: "fewest games" alone would prefer the empty plan.
    problem = Problem(
        squares=az('A') + jobs('brawler', 'scout'),
        candidates=(
            cand(1, letter='A', jobs=['brawler']),
            cand(2, letter='A'),
            cand(3, jobs=['scout']),
        ),
    )
    result = solve(problem)
    assert len(result.assignments) == 3
    assert result.assignments[(JOBS, 'scout')] == 3


def test_a_game_fills_at_most_one_job_square():
    problem = Problem(squares=jobs('brawler', 'scout'), candidates=(cand(1, jobs=['brawler', 'scout']),))
    result = solve(problem)
    assert len(result.assignments) == 1


def test_a_game_already_in_a_run_is_not_placed_there_again():
    problem = Problem(
        squares=az('A') + jobs('brawler'),
        candidates=(cand(1, letter='A', jobs=['brawler']),),
        in_run={JOBS: frozenset({1})},
    )
    assert solve(problem).assignments == {(AZ, 'A'): 1}


def test_keys_that_are_not_open_squares_are_ignored():
    problem = Problem(squares=az('A'), candidates=(cand(1, letter='Z'),))
    assert solve(problem).assignments == {}


def test_a_pinned_game_is_placed_even_when_a_better_plan_exists():
    # Without the pin, 1 is the double. The pin forces 2 into A, so 1 must go elsewhere or not at all.
    problem = Problem(
        squares=az('A') + jobs('brawler'),
        candidates=(cand(1, letter='A', jobs=['brawler']), cand(2, letter='A')),
        pins=frozenset({2}),
    )
    result = solve(problem)
    assert result.assignments[(AZ, 'A')] == 2
    assert result.assignments[(JOBS, 'brawler')] == 1
    assert result.unplaced_pins == frozenset()


def test_a_pinned_game_is_placed_as_a_double_when_it_can_be():
    # 4 is an equally good double and comes first, so only the pin can pick 1.
    problem = Problem(
        squares=az('A') + jobs('brawler'),
        candidates=(cand(4, letter='A', jobs=['brawler']), cand(1, letter='A', jobs=['brawler'])),
        pins=frozenset({1}),
    )
    assert solve(problem).assignments == {(AZ, 'A'): 1, (JOBS, 'brawler'): 1}


def test_conflicting_pins_are_never_infeasible_and_the_unplaced_one_is_reported():
    problem = Problem(
        squares=az('A'),
        candidates=(cand(1, letter='A'), cand(2, letter='A')),
        pins=frozenset({1, 2}),
    )
    result = solve(problem)
    assert len(result.assignments) == 1
    assert len(result.unplaced_pins) == 1
    assert result.unplaced_pins | set(result.assignments.values()) == {1, 2}


def test_a_pin_that_fits_no_open_square_is_reported():
    problem = Problem(squares=az('A'), candidates=(cand(1, letter='A'), cand(2, letter='Q')), pins=frozenset({2}))
    result = solve(problem)
    assert result.assignments == {(AZ, 'A'): 1}
    assert result.unplaced_pins == frozenset({2})


def test_a_square_pin_holds_its_square_even_at_the_cost_of_a_filled_square():
    # Unpinned, the best plan is brawler=1, scout=2. Pinning 1 to scout leaves brawler with nothing.
    problem = Problem(
        squares=jobs('brawler', 'scout'),
        candidates=(cand(1, jobs=['brawler', 'scout']), cand(2, jobs=['scout'])),
        square_pins={(JOBS, 'scout'): 1},
    )
    result = solve(problem)
    assert result.assignments == {(JOBS, 'scout'): 1}
    assert result.unplaced_pins == frozenset()


def test_a_square_pin_on_a_game_that_does_not_fit_is_reported():
    problem = Problem(squares=az('A'), candidates=(cand(1, letter='B'),), square_pins={(AZ, 'A'): 1})
    result = solve(problem)
    assert result.assignments == {}
    assert result.unplaced_pins == frozenset({1})


def test_an_occupant_stays_unless_replacing_it_saves_a_game():
    # Swap mode: A holds 9 (unfinished). 1 would take A AND the open job, saving a game, so it is suggested.
    problem = Problem(
        squares=az('A') + jobs('brawler'),
        candidates=(cand(9, letter='A'), cand(1, letter='A', jobs=['brawler'])),
        in_run={AZ: frozenset({9})},
        occupants={(AZ, 'A'): 9},
    )
    assert solve(problem).assignments == {(AZ, 'A'): 1, (JOBS, 'brawler'): 1}


def test_an_occupant_is_kept_when_a_swap_gains_nothing():
    problem = Problem(
        squares=az('A'),
        candidates=(cand(1, letter='A', stages=3), cand(9, letter='A')),
        in_run={AZ: frozenset({9})},
        occupants={(AZ, 'A'): 9},
    )
    assert solve(problem).assignments == {(AZ, 'A'): 9}


def test_penalized_games_are_avoided_without_losing_squares_or_doubles():
    problem = Problem(
        squares=az('A') + jobs('brawler'),
        candidates=(
            cand(1, letter='A', jobs=['brawler']),
            cand(2, letter='A', jobs=['brawler']),
            cand(3, letter='A'),
        ),
        penalized=frozenset({1}),
    )
    assert solve(problem).assignments == {(AZ, 'A'): 2, (JOBS, 'brawler'): 2}


def test_a_penalized_double_is_still_used_when_avoiding_it_would_cost_a_game():
    problem = Problem(
        squares=az('A') + jobs('brawler'),
        candidates=(cand(3, letter='A'), cand(4, jobs=['brawler']), cand(1, letter='A', jobs=['brawler'])),
        penalized=frozenset({1}),
    )
    assert solve(problem).assignments == {(AZ, 'A'): 1, (JOBS, 'brawler'): 1}


def test_a_penalized_game_is_still_used_when_nothing_else_fills_the_square():
    problem = Problem(squares=az('A'), candidates=(cand(1, letter='A'),), penalized=frozenset({1}))
    assert solve(problem).assignments == {(AZ, 'A'): 1}


def test_badge_stages_break_a_tie_between_equal_plans():
    problem = Problem(squares=az('A'), candidates=(cand(1, letter='A'), cand(2, letter='A', stages=2)))
    assert solve(problem).assignments == {(AZ, 'A'): 2}


def test_badge_stages_never_cost_a_double():
    problem = Problem(
        squares=az('A') + jobs('brawler'),
        candidates=(
            cand(2, letter='A', stages=5),
            cand(3, jobs=['brawler'], stages=5),
            cand(1, letter='A', jobs=['brawler']),
        ),
    )
    assert solve(problem).assignments == {(AZ, 'A'): 1, (JOBS, 'brawler'): 1}


@pytest.mark.parametrize('prefer, expected', [(True, 2), (False, 1)])
def test_progress_only_counts_when_the_hunter_prefers_their_backlog(prefer, expected):
    problem = Problem(
        squares=az('A'),
        candidates=(cand(1, letter='A'), cand(2, letter='A', progress=60)),
        prefer_progress=prefer,
    )
    assert solve(problem).assignments == {(AZ, 'A'): expected}


def test_earlier_candidates_are_preferred_on_a_tie():
    problem = Problem(
        squares=az('A', 'B'),
        candidates=(cand(5, letter='B'), cand(4, letter='A'), cand(3, letter='B'), cand(2, letter='A')),
    )
    assert solve(problem).assignments == {(AZ, 'A'): 4, (AZ, 'B'): 5}


def test_an_occupant_left_out_of_in_run_still_never_moves_to_another_square():
    problem = Problem(
        squares=jobs('brawler', 'scout'),
        candidates=(cand(9, jobs=['brawler', 'scout']), cand(2, jobs=['brawler'])),
        occupants={(JOBS, 'brawler'): 9},
    )
    assert solve(problem).assignments == {(JOBS, 'brawler'): 9}


def test_duplicate_candidate_ids_are_refused():
    with pytest.raises(ValueError):
        solve(Problem(squares=az('A'), candidates=(cand(1, letter='A'), cand(1, letter='A'))))


def test_an_occupant_that_is_not_a_candidate_is_refused():
    # Otherwise its square reads as open with no keep bonus, and the plan proposes a pointless swap.
    with pytest.raises(ValueError):
        solve(Problem(squares=az('A'), candidates=(cand(1, letter='A'),), occupants={(AZ, 'A'): 9}))


def test_a_full_size_plan_fills_every_square_it_can():
    # 26 letters + 25 jobs, 40 candidates per letter, each fitting two jobs: the shape of a real plan.
    letters = [chr(ord('A') + i) for i in range(26)]
    job_keys = [f'job{i}' for i in range(25)]
    candidates = []
    for li, letter in enumerate(letters):
        for k in range(40):
            cid = li * 100 + k
            candidates.append(cand(cid, letter=letter, jobs=[job_keys[(li + k) % 25], job_keys[(li + 2 * k) % 25]]))
    result = solve(Problem(squares=az(*letters) + jobs(*job_keys), candidates=tuple(candidates)))
    assert len(result.assignments) == 51
    # 25 of the 26 letter games can double as a job, so 26 games cover all 51 squares.
    assert len(set(result.assignments.values())) == 26



def test_the_priority_weight_puts_badge_stages_above_using_fewer_games():
    # Bonus keeps the double (one game); Priority takes the stage game for A even though it needs a second game
    # for the job (either 1 or 3 may then take it: both plans fill both squares with two games).
    def run(stages_first):
        return solve(Problem(
            squares=az('A') + jobs('brawler'),
            candidates=(cand(1, letter='A', jobs=['brawler']), cand(2, letter='A', stages=2), cand(3, jobs=['brawler'])),
            stages_first=stages_first,
        )).assignments
    assert run(False) == {(AZ, 'A'): 1, (JOBS, 'brawler'): 1}
    priority = run(True)
    assert priority[(AZ, 'A')] == 2 and priority[(JOBS, 'brawler')] in (1, 3)


def test_the_priority_weight_never_fills_fewer_squares_than_bonus():
    # Filling is its own tier above stages in both orders; a full-size problem with stages scattered across
    # singles and doubles must fill exactly as many squares either way.
    letters = [chr(ord('A') + i) for i in range(26)]
    job_keys = [f'job{i}' for i in range(25)]
    candidates = []
    for li, letter in enumerate(letters):
        for k in range(6):
            cid = li * 100 + k
            fits_jobs = [job_keys[(li + k) % 25]] if k % 2 else []
            candidates.append(cand(cid, letter=letter, jobs=fits_jobs, stages=(k * 7 + li) % 4))
    for j, key in enumerate(job_keys):
        candidates.append(cand(9000 + j, jobs=[key], stages=j % 3))
    squares = az(*letters) + jobs(*job_keys)
    bonus = solve(Problem(squares=squares, candidates=tuple(candidates)))
    priority = solve(Problem(squares=squares, candidates=tuple(candidates), stages_first=True))
    assert len(bonus.assignments) == len(priority.assignments) == 51
    assert len(set(priority.assignments.values())) >= len(set(bonus.assignments.values()))



def test_under_priority_different_games_still_finds_different_games():
    # The stage-heavy game is penalized ("Different games"); a stage-free alternative fills the square just as
    # well, so Priority must not keep handing the same game back.
    problem = Problem(squares=az('A'), candidates=(cand(1, letter='A', stages=4), cand(2, letter='A')),
                      penalized=frozenset({1}), stages_first=True)
    assert solve(problem).assignments == {(AZ, 'A'): 2}


def test_under_priority_an_unfinished_square_is_offered_a_stage_clearing_swap():
    # Bonus keeps the occupant (a swap would save no game); Priority ranks stages above keeping it, so the swap
    # solve proposes the stage game. Swaps are opt-in, so this is an offer, never a change.
    def run(stages_first):
        return solve(Problem(
            squares=az('A'), candidates=(cand(9, letter='A'), cand(1, letter='A', stages=3)),
            in_run={AZ: frozenset({9})}, occupants={(AZ, 'A'): 9}, stages_first=stages_first,
        )).assignments
    assert run(False) == {(AZ, 'A'): 9}
    assert run(True) == {(AZ, 'A'): 1}
