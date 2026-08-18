"""The denormalized board entrant counts, and the nightly orchestrator that keeps them ordered.

`BadgeSeries.entrants` / `Job.entrants` let the two slow directories gate and sort on an indexed column
instead of aggregating the whole standing table on every request. `Game.played_count` is the same idea and
is why Game Boards was already fast.

The property that matters most here is that the columns are RECOMPUTED, never incremented. Every
incrementally-maintained counter in this codebase has drifted -- `earned_count` came to mean two things
because it tracked create/delete but not lapse, `required_stages` desynced award math silently, and
`earned_count` drifts down-only today. So the tests below deliberately create drift (write a wrong value,
delete rows behind the counter's back) and assert the recompute corrects it, rather than only checking
that a fresh count is right.
"""
import io

import pytest
from django.core.management import call_command

from trophies.management.commands.recalc_board_entrants import (
    recalc_badge_series_entrants, recalc_job_entrants,
)
from trophies.models import BadgeSeries, Job, ProfileJobXP, SeriesBadgeStanding
from tests.factories import BadgeSeriesFactory, ProfileFactory

pytestmark = pytest.mark.django_db


def _standing(slug, profile=None):
    return SeriesBadgeStanding.objects.create(
        profile=profile or ProfileFactory(), series_slug=slug, xp=100,
        progress_bp=5000, stages_cleared=1, stages_total=2,
    )


def _job(slug='hunter', name='Hunter'):
    return Job.objects.create(slug=slug, name=name, discipline='combat')


# --- the counts themselves ----------------------------------------------------


def test_series_entrants_counts_its_standings():
    series = BadgeSeriesFactory(series_slug='souls')
    for _ in range(3):
        _standing('souls')

    recalc_badge_series_entrants()

    series.refresh_from_db()
    assert series.entrants == 3


def test_a_series_with_no_standings_counts_zero():
    """Not left at whatever it was: a series everyone abandoned must fall back out of the gate."""
    series = BadgeSeriesFactory(series_slug='empty', entrants=99)

    recalc_badge_series_entrants()

    series.refresh_from_db()
    assert series.entrants == 0


def test_the_recompute_corrects_drift_in_both_directions():
    """The whole reason this is a recompute and not an increment.

    A counter nudged on write drifts the moment anything bypasses the write path -- a cascade delete, a
    bulk operation, a migration. Rebuilding from a GROUP BY has no state to get wrong.
    """
    over = BadgeSeriesFactory(series_slug='over', entrants=500)
    under = BadgeSeriesFactory(series_slug='under', entrants=0)
    _standing('over')
    for _ in range(4):
        _standing('under')

    recalc_badge_series_entrants()

    over.refresh_from_db()
    under.refresh_from_db()
    assert over.entrants == 1, 'an inflated counter was not corrected downward'
    assert under.entrants == 4, 'a stale-low counter was not corrected upward'


def test_job_entrants_ignores_zero_xp_rows():
    """`job_rows` filters `total_xp__gt=0`, so a zero-XP row is a real state the board does not show.
    Counting it would gate a board open on hunters who do not appear on it."""
    job = _job()
    ProfileJobXP.objects.create(profile=ProfileFactory(), job=job, total_xp=100, level=1)
    ProfileJobXP.objects.create(profile=ProfileFactory(), job=job, total_xp=0, level=0)

    recalc_job_entrants()

    job.refresh_from_db()
    assert job.entrants == 1


def test_the_command_reports_and_is_idempotent():
    BadgeSeriesFactory(series_slug='idem')
    _standing('idem')

    out = io.StringIO()
    call_command('recalc_board_entrants', stdout=out)
    assert 'Entrants recomputed' in out.getvalue()

    first = BadgeSeries.objects.get(series_slug='idem').entrants
    call_command('recalc_board_entrants', stdout=io.StringIO())
    assert BadgeSeries.objects.get(series_slug='idem').entrants == first


def test_dry_run_writes_nothing():
    series = BadgeSeriesFactory(series_slug='dry', entrants=42)
    _standing('dry')

    out = io.StringIO()
    call_command('recalc_board_entrants', '--dry-run', stdout=out)

    assert 'DRY RUN' in out.getvalue()
    series.refresh_from_db()
    assert series.entrants == 42, 'a dry run wrote to the database'


# --- the nightly orchestrator -------------------------------------------------


def test_the_entrants_recompute_runs_AFTER_the_badge_evaluation():
    """The ordering this command exists to make explicit.

    Badge entrants count `SeriesBadgeStanding`, which `evaluate_badges` writes. Ordering used to be
    implied by wall-clock spacing between two separate cron entries (04:00 and 04:30) -- a guess that
    breaks the first night the evaluation outgrows the gap, and then the count describes a half-rewritten
    table.
    """
    from core.management.commands.nightly import STEPS

    labels = [label for label, _cmd, _kw in STEPS]
    assert labels.index('board entrants') > labels.index('badge evaluation')
    assert labels.index('board entrants') > labels.index('DLC detection'), (
        'DLC detection also rewrites standings, so the count must follow it too'
    )


def test_one_failing_step_does_not_cancel_the_others(monkeypatch):
    """Isolated failures. Losing the whole night's maintenance because one step raised is the worse
    outcome -- but the run must still FAIL, or a broken step hides behind a green cron."""
    from django.core.management import call_command as real_call

    ran = []

    def fake(command, *args, **kwargs):
        ran.append(command)
        if command == 'detect_dlc_and_refresh':
            raise RuntimeError('boom')

    monkeypatch.setattr('core.management.commands.nightly.call_command', fake)

    with pytest.raises(SystemExit) as exc:
        real_call('nightly', stdout=io.StringIO(), stderr=io.StringIO())

    assert 'DLC detection' in str(exc.value), 'the failing step must be named'
    assert 'recalc_board_entrants' in ran, 'a later step was skipped because an earlier one failed'
    assert 'audit_badge_coverage' in ran


def test_a_clean_run_does_not_raise(monkeypatch):
    monkeypatch.setattr('core.management.commands.nightly.call_command', lambda *a, **k: None)
    out = io.StringIO()
    call_command('nightly', stdout=out)
    assert 'all 4 steps ok' in out.getvalue()


def test_dry_run_lists_the_order_without_running_anything(monkeypatch):
    ran = []
    monkeypatch.setattr('core.management.commands.nightly.call_command',
                        lambda cmd, *a, **k: ran.append(cmd))
    out = io.StringIO()
    call_command('nightly', '--dry-run', stdout=out)

    assert ran == [], 'a dry run executed a step'
    body = out.getvalue()
    assert body.index('badge evaluation') < body.index('board entrants')


def test_steps_can_be_skipped_and_isolated(monkeypatch):
    """`--only` and `--skip` exist so an operator re-running after a failure does not repeat the
    expensive full evaluation."""
    ran = []
    monkeypatch.setattr('core.management.commands.nightly.call_command',
                        lambda cmd, *a, **k: ran.append(cmd))

    call_command('nightly', '--only', 'board entrants', stdout=io.StringIO())
    assert ran == ['recalc_board_entrants']

    ran.clear()
    call_command('nightly', '--skip', 'badge evaluation', stdout=io.StringIO())
    assert 'evaluate_badges' not in ran and 'recalc_board_entrants' in ran


def test_every_step_names_a_real_command():
    """A typo in STEPS would surface as a nightly failure at 04:00 rather than here."""
    from django.core.management import get_commands
    from core.management.commands.nightly import STEPS

    known = get_commands()
    missing = [cmd for _label, cmd, _kw in STEPS if cmd not in known]
    assert not missing, f'nightly references commands that do not exist: {missing}'
