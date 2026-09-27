"""Challenge squares completing themselves: the sync hook, the nightly sweep, and the rule they share.

A square completes when its owner has finished its contract, which the contract engine already records as
an `EarnedContract` row. So detection is not a second completion rule -- it is two schedules reading the
first one. What is worth pinning is therefore mostly about WHEN and about what must survive:

- **The sweep must follow `process_contracts` in `nightly`.** It reads the rows that command writes, so
  running first makes it report nothing to do on exactly the night a contract went live.
- **A completed square must survive `reconcile_contracts` deleting its `EarnedContract`.** That is the
  finding the whole snapshot design exists for: staff correcting the catalogue must not silently
  un-complete a hunter's finished run. The test for it is a TRIPWIRE on a future redesign rather than a
  mutation-verified pin, and says so -- given completion is a stored column, nothing small can break it.
- **Hidden runs are still swept.** Hiding is a visibility act, not a pause, and progress earned while a
  run is out of sight is still the hunter's when they bring it back.
- **Finished runs are not.** A run that is already complete must never be written to again, because a
  badge and a title have been granted against it.
"""
from pathlib import Path

import pytest
from django.core.management import call_command
from django.utils import timezone

from challenges.models import CHALLENGE_TYPE_AZ, CHALLENGE_TYPE_JOBS, Challenge
from challenges.services import challenge_service as svc
from tests.factories import ConceptFactory, GameFactory, IGDBMatchFactory, ProfileFactory
from trophies.models import Contract, EarnedContract, Job

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[2]

_SEQ = {'n': 0}


def _member(*, premium=True):
    return ProfileFactory(user_is_premium=premium)


def _contract(name, *, jobs=(), live=True):
    _SEQ['n'] += 1
    c = Contract.objects.create(name=name, slug=f"{name.lower().replace(' ', '-')}-{_SEQ['n']}",
                                is_live=live, igdb_id=800_000 + _SEQ['n'])
    if jobs:
        c.jobs.set(jobs)
    # A real member game, so anything that resolves membership finds one.
    concept = ConceptFactory(anchor_migration_completed_at=timezone.now())
    IGDBMatchFactory(concept=concept, igdb_id=c.igdb_id, status='accepted')
    GameFactory(concept=concept)
    return c


def _finished(profile, contract):
    """Completion as the engine records it, which is exactly what detection reads."""
    return EarnedContract.objects.create(
        profile=profile, contract=contract, has_platinum=True,
        platinum_reached_at=timezone.now(),
    )


def _run_with_one_square(profile, letter='A', name='Astro Bot'):
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    contract = _contract(name)
    svc.assign(challenge, profile, letter, contract)
    return challenge, contract


# ── the shared rule ──────────────────────────────────────────────────────────────────────────────

def test_a_square_completes_once_its_contract_is_finished():
    profile = _member()
    challenge, contract = _run_with_one_square(profile)
    assert svc.detect_for_profile(profile) == 0, 'nothing is finished yet'

    _finished(profile, contract)

    assert svc.detect_for_profile(profile) == 1
    challenge.refresh_from_db()
    assert challenge.completed_count == 1
    assert challenge.slots.get(key='A').is_completed


def test_detection_is_idempotent():
    """Both schedules run over the same squares, and the sync hook fires on every sync."""
    profile = _member()
    challenge, contract = _run_with_one_square(profile)
    _finished(profile, contract)
    svc.detect_for_profile(profile)

    assert svc.detect_for_profile(profile) == 0


def test_an_empty_square_is_never_completed():
    """`pending_slots` excludes them, so an unassigned letter cannot be finished by somebody else's
    contract landing."""
    profile = _member()
    svc.start(profile, CHALLENGE_TYPE_AZ)
    contract = _contract('Astro Bot')
    _finished(profile, contract)

    assert svc.detect_for_profile(profile) == 0


def test_another_hunters_completion_does_not_finish_your_square():
    """The `EarnedContract` subquery is correlated on BOTH profile and contract. Dropping the profile half
    would finish a square the moment anybody, anywhere, completed that game."""
    profile, other = _member(), _member()
    challenge, contract = _run_with_one_square(profile)
    _finished(other, contract)

    assert svc.detect_for_profile(profile) == 0
    assert svc.completable_slots().count() == 0


def test_the_last_square_finishes_the_run_through_detection():
    """Detection goes through `mark_slot_completed` rather than writing rows itself, which is what makes
    this true -- the completion stamp rides the same recount."""
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_JOBS)
    for job in Job.objects.all():
        contract = _contract(f'Game for {job.slug}', jobs=[job])
        svc.assign(challenge, profile, job.slug, contract)
        _finished(profile, contract)

    assert svc.detect_for_profile(profile) == challenge.total_slots

    challenge.refresh_from_db()
    assert challenge.is_complete is True
    assert challenge.completed_at is not None


# ── what must survive, and what must not be touched ──────────────────────────────────────────────

def test_a_finished_square_survives_its_earned_contract_being_deleted():
    """THE REGRESSION PIN THE SNAPSHOT DESIGN EXISTS FOR.

    `reconcile_contracts` DELETES an `EarnedContract` row when a contract's derived membership stops
    qualifying -- a Concept split, a re-anchor, a match falling out of the trusted statuses. If a square
    read its completion live from that row, staff correcting the catalogue would silently un-complete a
    hunter's finished run, and a finished RUN would lose the badge and title granted against it.

    So the square owns `completed_at`, and `EarnedContract` is only ever the detector.

    HONEST ABOUT WHAT THIS IS: a DESIGN guard, not a mutation-verified pin. Given completion is a stored
    column, no small change to the current code makes this fail -- deleting an unrelated row cannot unset a
    boolean. It is here to fail LOUDLY if somebody later makes completion derived (a property over
    `EarnedContract`, an annotation, a signal), which would look like a tidy simplification and would
    quietly re-open the exact hole. Read it as a tripwire on a future redesign.
    """
    profile = _member()
    challenge, contract = _run_with_one_square(profile)
    _finished(profile, contract)
    svc.detect_for_profile(profile)

    EarnedContract.objects.filter(profile=profile, contract=contract).delete()

    slot = challenge.slots.get(key='A')
    assert slot.is_completed, 'a catalogue correction un-completed a finished square'
    assert slot.completed_at is not None
    challenge.refresh_from_db()
    assert challenge.completed_count == 1


def test_a_hidden_run_is_still_swept():
    """Hiding is a visibility act, not a pause. Progress earned while a run is out of sight is still the
    hunter's when they bring it back -- and `start` brings back that same run."""
    profile = _member()
    challenge, contract = _run_with_one_square(profile)
    svc.hide(challenge, profile)
    _finished(profile, contract)

    assert svc.detect_for_profile(profile) == 1

    resumed = svc.start(profile, CHALLENGE_TYPE_AZ)
    assert resumed.pk == challenge.pk
    assert resumed.completed_count == 1


def test_a_hidden_run_that_finishes_does_not_reach_the_hall_of_fame():
    """It is recorded, and it is not on display. `completed()` is built on `visible()`, which is what makes
    sweeping hidden runs safe rather than a way to publish something somebody hid."""
    profile = _member()
    challenge, contract = _run_with_one_square(profile)
    Challenge.objects.filter(pk=challenge.pk).update(total_slots=1)
    svc.hide(challenge, profile)
    _finished(profile, contract)
    svc.detect_for_profile(profile)

    challenge.refresh_from_db()
    assert challenge.is_complete is True
    assert not Challenge.objects.completed().filter(pk=challenge.pk).exists()


def test_a_finished_run_is_never_swept_again():
    """A badge and a title are granted against a completed run, so nothing may write to it afterwards."""
    profile = _member()
    challenge, contract = _run_with_one_square(profile)
    Challenge.objects.filter(pk=challenge.pk).update(is_complete=True, completed_at=timezone.now())
    _finished(profile, contract)

    assert svc.detect_for_profile(profile) == 0
    assert svc.completable_slots().count() == 0


def test_a_draft_contract_still_completes_a_square_it_already_occupies():
    """Un-publishing a contract must not strand a square that already holds it. `is_live` gates what can
    be ASSIGNED (`eligibility._shape`); detection asks only whether the hunter finished it, and the
    contract engine's own note says un-publishing does not take a reached stamp back."""
    profile = _member()
    challenge, contract = _run_with_one_square(profile)
    Contract.objects.filter(pk=contract.pk).update(is_live=False)
    _finished(profile, contract)

    assert svc.detect_for_profile(profile) == 1


# ── the sweep's shape ────────────────────────────────────────────────────────────────────────────

def test_the_sweep_finds_every_hunter_in_one_query():
    """Site-wide, and NOT a loop over profiles: the cost should scale with runs in flight, not with how
    many accounts exist. `process_contracts` has to walk candidates because contract membership is
    derived; this does not, because a square names its own contract."""
    for _ in range(3):
        profile = _member()
        _, contract = _run_with_one_square(profile)
        _finished(profile, contract)
    _member()   # an account with no run at all, which must cost nothing

    assert svc.completable_slots().count() == 3


def test_the_sweep_completes_and_reports():
    profile = _member()
    challenge, contract = _run_with_one_square(profile)
    _finished(profile, contract)

    call_command('process_challenges')

    challenge.refresh_from_db()
    assert challenge.completed_count == 1


def test_a_dry_run_writes_nothing():
    profile = _member()
    challenge, contract = _run_with_one_square(profile)
    _finished(profile, contract)

    call_command('process_challenges', '--dry-run')

    challenge.refresh_from_db()
    assert challenge.completed_count == 0
    assert challenge.slots.get(key='A').is_completed is False


def test_the_sweep_can_be_narrowed_to_one_hunter():
    mine, theirs = _member(), _member()
    my_run, my_contract = _run_with_one_square(mine)
    their_run, their_contract = _run_with_one_square(theirs)
    _finished(mine, my_contract)
    _finished(theirs, their_contract)

    call_command('process_challenges', '--user', mine.psn_username)

    my_run.refresh_from_db()
    their_run.refresh_from_db()
    assert my_run.completed_count == 1
    assert their_run.completed_count == 0


def test_an_unknown_username_is_an_error_rather_than_a_silent_no_op():
    from django.core.management.base import CommandError

    with pytest.raises(CommandError, match='No profile'):
        call_command('process_challenges', '--user', 'nobody-here')


def test_the_sweep_is_quiet_when_there_is_nothing_to_do():
    _member()

    call_command('process_challenges')   # must not raise


# ── scheduling ───────────────────────────────────────────────────────────────────────────────────

def test_the_sweep_is_scheduled_nightly():
    """Without this it is a whale-safe command scheduled nowhere -- the exact state `process_contracts`
    was found in, which is why `test_nightly` pins the drift nets at all."""
    from core.management.commands.nightly import STEPS

    assert 'process_challenges' in [cmd for _label, cmd, _kw in STEPS]


def test_the_sweep_runs_after_contract_detection():
    """LOAD-BEARING. A square completes when an `EarnedContract` row exists, and `process_contracts` is
    what creates one. Run first, this sweeps yesterday's rows and reports nothing to do on exactly the
    night a contract went live."""
    from core.management.commands.nightly import STEPS

    commands = [cmd for _label, cmd, _kw in STEPS]
    assert commands.index('process_challenges') > commands.index('process_contracts')


def test_the_sync_hook_runs_after_the_contract_hook():
    """Source-text, because the ordering inside one method is the whole property and there is no seam to
    assert it through. Same reason and same shape as the repo's other ordering guards.

    If challenge detection ran first it would miss every square finished on this very sync, and a hunter
    watching their own sync land would see the trophy arrive and the square stay empty until the nightly.
    """
    src = (ROOT / 'trophies' / 'token_keeper.py').read_text(encoding='utf-8')

    assert 'detect_for_profile' in src, 'the sync hook is gone'
    assert src.index('check_profile_contracts(profile, touched_concept_ids)') < src.index(
        'detect_for_profile(profile)'
    ), 'challenge detection must follow contract detection on the sync path'


def test_the_sync_hook_imports_outside_its_guard():
    """The shape `test_no_dangling_imports` exists for: an import inside a broad `try` turns a missing
    module into one log line and silently skips the rest of the job forever."""
    src = (ROOT / 'trophies' / 'token_keeper.py').read_text(encoding='utf-8')
    i = src.index('from challenges.services.challenge_service import detect_for_profile')

    assert src.index('detect_for_profile(profile)', i) > i
    assert src[i:src.index('detect_for_profile(profile)', i)].count('try:') == 1, (
        'the import must sit above the try, not inside it'
    )
