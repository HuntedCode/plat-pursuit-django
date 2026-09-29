"""`report_xp_economy`'s challenge-bonus section.

WHY THIS FILE EXISTS AT ALL, given the command is a read-only calibration tool nobody runs in CI: it is
the thing the owner is told to run BEFORE changing an XP number, so its arithmetic is load-bearing for a
decision rather than for a request. `CHALLENGE_SLOT_JOB_XP`'s docstring says to model a change here first.
A report that quietly understates the bonus would license a change nobody would otherwise make.

NO DATABASE. `simulate_stage_jobs` is the only DB reader in the command, so patching it lets the whole
report run against a known supply -- which is also the only way to assert exact figures, since real
catalogue numbers move every day.
"""
import pytest
from django.core.management import call_command

from trophies.util_modules.constants import CHALLENGE_SLOT_JOB_XP, JOB_XP_PER_LEVEL


@pytest.fixture
def one_stage_per_job(monkeypatch):
    """Exactly one contract feeding each job, so every job's contract supply is identical and any spread
    in the output must have come from the bonus rather than from the fixture."""
    from trophies.services import job_detection
    from trophies.management.commands import report_xp_economy

    stages = [[slug] for slug in job_detection.CATALOG_ORDER]
    monkeypatch.setattr(report_xp_economy, 'simulate_stage_jobs', lambda: stages)
    return stages


def _run(**kwargs):
    from io import StringIO

    out = StringIO()
    call_command('report_xp_economy', stdout=out, **kwargs)
    return out.getvalue()


def test_the_bonus_section_reports_one_square_per_job_per_run(one_stage_per_job):
    output = _run(runs=1)

    assert 'Job Coverage Challenge bonus' in output
    assert f'{CHALLENGE_SLOT_JOB_XP:,}' in output
    # A square is two job levels under the flat curve, which is the legibility claim the constant makes.
    assert CHALLENGE_SLOT_JOB_XP // JOB_XP_PER_LEVEL == 2
    assert f'Job levels bought per job:             {2:>6,}' in output


def test_two_runs_pay_twice_as_much_as_one(one_stage_per_job):
    """The bonus is linear in runs, and it is the one XP source that cannot skew the per-job spread --
    every run pays every job exactly once."""
    one, two = _run(runs=1), _run(runs=2)

    assert f'Bonus XP per job:                      {CHALLENGE_SLOT_JOB_XP:>6,}' in one
    assert f'Bonus XP per job:                      {2 * CHALLENGE_SLOT_JOB_XP:>6,}' in two


def test_the_bonus_raises_the_reported_pursuer_level(one_stage_per_job):
    """The figure the owner actually wants: what a completed run does to Pursuer Level. 25 jobs x 2 levels
    is the +50 the constant's own docstring warns about."""
    output = _run(runs=1)

    contracts_only = [ln for ln in output.splitlines() if 'contracts only' in ln][0]
    with_bonus = [ln for ln in output.splitlines() if 'with the bonus' in ln][0]
    base = int(contracts_only.split(':')[1].strip().replace(',', ''))
    lifted = int(with_bonus.split(':')[1].strip().split()[0].replace(',', ''))

    jobs = len(one_stage_per_job)
    assert lifted - base == jobs * (CHALLENGE_SLOT_JOB_XP // JOB_XP_PER_LEVEL)
    assert f'(+{lifted - base:,})' in with_bonus


def test_zero_runs_prints_no_bonus_section(one_stage_per_job):
    """`--runs 0` is how somebody asks the pre-challenge question, so the section must disappear rather
    than print a row of zeroes that reads like a bug."""
    assert 'Job Coverage Challenge bonus' not in _run(runs=0)


def test_a_negative_run_count_is_floored_rather_than_paying_negative_xp(one_stage_per_job):
    """Pinned because the alternative is a report that shows XP being taken away, which is not a state the
    economy has -- the ledger is append-only."""
    assert 'Job Coverage Challenge bonus' not in _run(runs=-5)


def test_the_slot_xp_override_is_what_models_a_change(one_stage_per_job):
    """The whole point of the flag: ask what a different figure would do before shipping it."""
    output = _run(runs=1, slot_xp=3000)

    assert 'Job Coverage Challenge bonus' in output
    assert f'Bonus XP per job:                      {3000:>6,}' in output
    assert f'Job levels bought per job:             {1:>6,}' in output
