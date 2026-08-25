"""The nightly orchestrator: the badge maintenance chain as ONE cron entry, in dependency order.

The ordering used to be expressed as wall-clock spacing between separate Render entries -- `evaluate_badges
--all` at 04:00, `detect_dlc_and_refresh` at 04:30. Thirty minutes is a guess, and `evaluate_badges --all`
walks every profile with a PSN username, so when it outgrows the gap the two overlap and two processes call
`recompute_standing` for the same profiles.

(This file was `test_board_entrants_and_nightly.py`. The entrants half went with the board directories in
2026-08: `BadgeSeries.entrants` / `Job.entrants` existed only so those directories could gate and sort on an
indexed column, and with the directories gone the columns had no readers. The per-entity Ranks panels each
do ONE scoped, indexed count instead -- there is no whole-table aggregate to avoid.)
"""
import io

import pytest
from django.core.management import call_command

pytestmark = pytest.mark.django_db


def test_the_dlc_sweep_runs_AFTER_the_badge_evaluation():
    """`detect_dlc_and_refresh` re-evaluates series whose games gained DLC, writing the same standing
    tables `evaluate_badges` does. Overlapping them means two writers on one profile."""
    from core.management.commands.nightly import STEPS

    labels = [label for label, _cmd, _kw in STEPS]
    assert labels.index('DLC detection') > labels.index('badge evaluation')
    assert labels.index('badge coverage audit') == len(labels) - 1, (
        'the read-only report is last because it is the least urgent'
    )


def test_the_drift_nets_are_scheduled():
    """THE regression this pins. Sync only evaluates what a sync TOUCHED, so anything authored after a
    hunter last touched the relevant game never reaches them without a sweep. Badges have always had
    `evaluate_badges --all`; contracts and milestones had no net at all, and `process_contracts` sat
    written, whale-safe and scheduled NOWHERE. A Contract published for a game 10,000 hunters had
    already platinumed reached exactly zero of them."""
    from core.management.commands.nightly import STEPS

    commands = [cmd for _label, cmd, _kw in STEPS]
    assert 'process_contracts' in commands, 'contracts have no drift net'
    assert 'recompute_milestones' in commands, 'milestones have no drift net'

    contracts = next(kw for _l, cmd, kw in STEPS if cmd == 'process_contracts')
    assert contracts.get('all') is True, 'a per-user sweep is not a net'


def test_contract_detection_runs_AFTER_the_dlc_sweep():
    """`detect_dlc_and_refresh` rewrites `ProfileGame.progress` for games that gained DLC, dropping
    owners back below 100%. `process_contracts` reads that same progress to decide a contract is
    reached, so running it first would stamp reaches the DLC sweep immediately invalidates."""
    from core.management.commands.nightly import STEPS

    labels = [label for label, _cmd, _kw in STEPS]
    assert labels.index('contract detection') > labels.index('DLC detection')


def test_milestones_recompute_last_among_the_writers():
    """Milestone metrics read badge standings and ProfileJobXP, both written earlier in this chain."""
    from core.management.commands.nightly import STEPS

    labels = [label for label, _cmd, _kw in STEPS]
    assert labels.index('milestone recompute') > labels.index('badge evaluation')
    assert labels.index('milestone recompute') > labels.index('contract detection')


def test_every_step_names_a_real_command():
    """A step naming a deleted command fails the whole night's run at that step. Cheap to pin."""
    from django.core.management import get_commands
    from core.management.commands.nightly import STEPS

    known = get_commands()
    for _label, cmd, _kw in STEPS:
        assert cmd in known, f'{cmd} is not a registered management command'


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
    assert 'audit_badge_coverage' in ran, 'a later step was skipped because an earlier one failed'


def test_a_clean_run_does_not_raise(monkeypatch):
    # Counted from STEPS rather than hardcoded: this asserted "all 3 steps ok" and so failed the
    # moment the contract and milestone drift nets were added, which is a passing test breaking on
    # a correct change.
    from core.management.commands.nightly import STEPS

    monkeypatch.setattr('core.management.commands.nightly.call_command', lambda *a, **k: None)
    out = io.StringIO()
    call_command('nightly', stdout=out)
    assert f'all {len(STEPS)} steps ok' in out.getvalue()


def test_dry_run_lists_the_order_without_running_anything(monkeypatch):
    ran = []
    monkeypatch.setattr('core.management.commands.nightly.call_command',
                        lambda cmd, *a, **k: ran.append(cmd))
    out = io.StringIO()
    call_command('nightly', '--dry-run', stdout=out)

    assert ran == [], 'a dry run executed a step'
    body = out.getvalue()
    assert body.index('badge evaluation') < body.index('DLC detection')


def test_steps_can_be_skipped_and_isolated(monkeypatch):
    """`--only` and `--skip` exist so an operator re-running after a failure does not repeat the
    expensive full evaluation."""
    ran = []
    monkeypatch.setattr('core.management.commands.nightly.call_command',
                        lambda cmd, *a, **k: ran.append(cmd))

    call_command('nightly', '--only', 'DLC detection', stdout=io.StringIO())
    assert ran == ['detect_dlc_and_refresh']

    ran.clear()
    call_command('nightly', '--skip', 'badge evaluation', stdout=io.StringIO())
    assert 'evaluate_badges' not in ran and 'detect_dlc_and_refresh' in ran


def test_every_step_names_a_real_command():
    """A typo in STEPS would surface as a nightly failure at 04:00 rather than here."""
    from django.core.management import get_commands
    from core.management.commands.nightly import STEPS

    known = get_commands()
    missing = [cmd for _label, cmd, _kw in STEPS if cmd not in known]
    assert not missing, f'nightly references commands that do not exist: {missing}'
