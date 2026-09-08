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
    assert contracts.get('all_profiles') is True, 'a per-user sweep is not a net'
    # Incremental keeps the nightly cost near zero AND still forces a full pass weekly. Dropping the
    # flag restores an O(contracts x candidates) sweep on top of step 1's pass over every profile,
    # which is what put this chain past its window.
    assert contracts.get('incremental') is True, 'a full sweep every night is not affordable'


def test_contract_detection_runs_AFTER_the_dlc_sweep():
    """`detect_dlc_and_refresh` rewrites `ProfileGame.progress` for games that gained DLC, dropping
    owners back below 100%. `process_contracts` reads that same progress to decide a contract is
    reached, so running it first would stamp reaches the DLC sweep immediately invalidates."""
    from core.management.commands.nightly import STEPS

    labels = [label for label, _cmd, _kw in STEPS]
    assert labels.index('contract detection') > labels.index('DLC detection')


def test_shovelware_detection_leads_the_chain():
    """Its START time is load-bearing to a job OUTSIDE this chain. `evaluate_contract_candidates` runs on
    its own 04:45 Render entry and reads these flags, which is safe today only because
    `update_shovelware` starts at 04:00.

    Folding it into `nightly` preserves that only while it runs FIRST. Anywhere else in this list and its
    start slides behind `evaluate_badges --all`'s pass over every profile, and the 45-minute assumption
    breaks silently -- the candidate pipeline would read yesterday's flags with nothing failing.
    """
    from core.management.commands.nightly import STEPS

    assert STEPS[0][1] == 'update_shovelware', (
        'shovelware detection no longer starts the chain, so evaluate_contract_candidates (04:45) may '
        'now read flags that have not been refreshed'
    )


def test_the_clean_standings_SKIP_when_the_shovelware_detection_failed(monkeypatch):
    """Step order alone does not make the dependency real -- failures here are isolated, so step 2 would
    happily rebuild the whole board after step 1 raised.

    Rebuilding over YESTERDAY's flags would be harmless and self-healing. The danger is the PARTIAL case:
    `update_shovelware` applies flags per game, so a mid-sweep failure leaves a half-applied catalogue and
    the recompute would materialize a board from a flag set that never existed as a consistent snapshot.
    Skipping leaves last night's board standing, which is a state that did exist.
    """
    from core.management.commands import nightly

    ran = []

    def fake(command, **kwargs):
        ran.append(command)
        if command == 'update_shovelware':
            raise RuntimeError('detector fell over')

    monkeypatch.setattr(nightly, 'call_command', fake)

    with pytest.raises(SystemExit) as exc:
        call_command('nightly')

    assert 'update_shovelware' in ran, 'the detector step never ran'
    assert 'recompute_clean_standings' not in ran, (
        'the board was rebuilt from a half-applied catalogue'
    )
    # ...and the rest of the night is unaffected: only the dependent skips.
    assert 'evaluate_badges' in ran, 'an unrelated later step was lost too'
    assert 'skipped' in str(exc.value), 'the skip is not reported in the failure'


def test_a_healthy_run_does_not_skip_the_dependent(monkeypatch):
    """The other direction, so the guard above cannot pass by never running step 2 at all."""
    from core.management.commands import nightly

    ran = []
    monkeypatch.setattr(nightly, 'call_command', lambda command, **kw: ran.append(command))
    call_command('nightly')

    assert 'recompute_clean_standings' in ran


def test_the_clean_standings_run_AFTER_the_shovelware_detection():
    """The Shovelware Free board's store is a PROJECTION of the flags `update_shovelware` writes, so the
    two have a real dependency and it is expressed as sequence.

    It has to be. Both `update_shovelware` and `nightly` were scheduled at 04:00 on separate Render
    entries, so the order between them was whatever the scheduler felt like -- and a recompute that wins
    that race rebuilds the board from YESTERDAY's catalogue, silently and plausibly. That failure is the
    reason this command exists (see its module docstring); this is the first step pair with a dependency
    strong enough to notice it.
    """
    from core.management.commands.nightly import STEPS

    labels = [label for label, _cmd, _kw in STEPS]
    assert labels.index('clean standings') > labels.index('shovelware detection'), (
        'the board is rebuilt from flags that have not been refreshed yet'
    )


def test_shovelware_detection_is_not_also_a_separate_cron_entry():
    """It MOVED into this chain rather than being duplicated into it. Two schedulers running the same
    catalogue-wide re-evaluation would have it racing itself, and `nightly`'s own docstring is explicit
    that a step here replaces a cron entry rather than joining it.

    Asserted against the cron doc, which is the register of what Render actually runs.
    """
    from pathlib import Path

    doc = (Path(__file__).resolve().parents[2] / 'docs' / 'guides' / 'cron-jobs.md').read_text(
        encoding='utf-8')
    # Not any MENTION: `evaluate_contract_candidates`'s notes reference `update_shovelware` legitimately,
    # and matching the whole row flagged that as a duplicate entry.
    #
    # `startswith`, not equality: a re-added row carrying an argument (`update_shovelware --force`) is the
    # same duplicate entry and an exact match would wave it through. The struck-out row survives either
    # way, since its cell opens with `~~`.
    #
    # BOTH registers, and NOT a fixed column index -- which is how half of this guard came to be vacuous.
    # The command sits in cell 2 of cron-jobs.md (`| time | command | frequency | notes |`) but cell 1 of
    # management-commands.md (`| command | schedule | notes |`), so pinning index 2 read the SCHEDULE
    # column of the second file and a live cron row re-added there passed straight through.
    #
    # A row counts as a live cron entry only if it ALSO carries a schedule. Both files list this command
    # in a reference table that describes what it does, which is correct and must stay -- the thing that
    # must not come back is a row that tells an operator to SCHEDULE it. `~~` excludes the struck-out
    # rows recording that it was folded in.
    import re
    from pathlib import Path as _P

    root = _P(__file__).resolve().parents[2] / 'docs' / 'guides'
    schedule = re.compile(r'\b(daily|weekly|hourly|monthly|UTC)\b', re.I)
    rows = []
    for name in ('cron-jobs.md', 'management-commands.md'):
        for ln in (root / name).read_text(encoding='utf-8').splitlines():
            cells = [c.strip() for c in ln.split('|')]
            names_it = any(c.startswith('`update_shovelware') for c in cells[1:3])
            if names_it and schedule.search(ln) and '~~' not in ln:
                rows.append(f'{name}: {ln[:120]}')
    assert not rows, (
        f'update_shovelware still has a live cron row while also being a nightly step: {rows}'
    )


def test_milestones_recompute_last_among_the_writers():
    """Milestone metrics read badge standings and ProfileJobXP, both written earlier in this chain."""
    from core.management.commands.nightly import STEPS

    labels = [label for label, _cmd, _kw in STEPS]
    assert labels.index('milestone recompute') > labels.index('badge evaluation')
    assert labels.index('milestone recompute') > labels.index('contract detection')
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


def test_reconcile_contracts_is_NOT_scheduled():
    """The inverse of the drift-net test above, and every bit as load-bearing.

    `reconcile_contracts` is the only subtractive path in the Contract engine: it DELETES banked XP.
    "Staff-triggered, never on cron" is asserted in its module docstring, in
    `contract_service.revoke_contract`, in job-board-contracts.md and in management-commands.md --
    and until this test, enforced nowhere. Scheduling it would strip real XP the moment a match went
    `pending_review` mid-rematch or PSN flux dropped a title out of a hunter's library, on a nightly
    cadence, with nobody reading the output.

    If you are here because you added it to STEPS: don't. Run it by hand against the one Contract you
    just re-keyed, read the preview, then `--apply`.
    """
    from core.management.commands.nightly import STEPS

    commands = [cmd for _label, cmd, _kw in STEPS]
    assert 'reconcile_contracts' not in commands, (
        'reconcile_contracts deletes banked XP and must never run unattended'
    )
