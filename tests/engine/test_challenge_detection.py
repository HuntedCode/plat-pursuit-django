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
    """Both schedules run over the same squares, and the sync hook fires on every sync.

    Asserts the QUERY as well as the count, because the count alone pins nothing: deleting
    `is_completed=False` from `pending_slots` leaves this green, masked by `mark_slot_completed`'s own
    guard returning False. The regression that hides behind it is real -- the nightly sweep would
    re-read every completed square on the site, forever, which is precisely the cost
    `completable_slots` claims not to pay.
    """
    profile = _member()
    challenge, contract = _run_with_one_square(profile)
    _finished(profile, contract)
    svc.detect_for_profile(profile)

    assert svc.detect_for_profile(profile) == 0
    assert svc.pending_slots(profile).count() == 0, (
        'a completed square is still pending; the sweep would re-read it every night'
    )
    assert svc.completable_slots().count() == 0


def test_an_empty_square_is_never_completed():
    """An unassigned letter cannot be finished by a contract the hunter happens to own.

    THREE nets stop this and the count test pinned none of them: `pending_slots`' `exclude`, SQL NULL
    semantics in the `EXISTS` (an empty square has `contract_id IS NULL`, so the correlation is never
    true), and `mark_slot_completed`'s `is_filled` guard. An earlier version credited the first and
    was actually passing on the second. Asserting the query pins the one it names.
    """
    profile = _member()
    svc.start(profile, CHALLENGE_TYPE_AZ)
    contract = _contract('Astro Bot')
    _finished(profile, contract)

    assert svc.pending_slots(profile).count() == 0, 'an empty square is pending'
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

def test_the_sweep_finds_every_hunter_in_one_query(django_assert_num_queries):
    """Site-wide in ONE query, and NOT a loop over profiles: the cost should scale with runs in
    flight, not with how many accounts exist. `process_contracts` has to walk candidate profiles
    because contract membership is derived; this does not, because a square names its own contract.

    The query count is asserted rather than described. An earlier version named it in the test's own
    title and checked only the row total, which a per-profile loop would satisfy just as well.
    """
    for _ in range(3):
        profile = _member()
        _, contract = _run_with_one_square(profile)
        _finished(profile, contract)
    _member()   # an account with no run at all, which must not be visited

    with django_assert_num_queries(1):
        found = list(svc.completable_slots())

    assert len(found) == 3


def test_the_sweep_completes_and_reports(capsys):
    """AND REPORTS, which the name claimed and the body did not check. The finished-run list names the
    RUN rather than the square that finished it -- an earlier version printed square labels under a
    heading counting runs."""
    profile = _member()
    challenge, contract = _run_with_one_square(profile)
    Challenge.objects.filter(pk=challenge.pk).update(total_slots=1)
    _finished(profile, contract)

    call_command('process_challenges')
    out = capsys.readouterr().out

    challenge.refresh_from_db()
    assert challenge.completed_count == 1
    assert '1 square(s) completed.' in out
    assert '1 run(s) FINISHED' in out
    assert f'{profile.psn_username} / {challenge.name}' in out


def test_a_dry_run_writes_nothing(capsys):
    profile = _member()
    challenge, contract = _run_with_one_square(profile)
    _finished(profile, contract)

    call_command('process_challenges', '--dry-run')
    out = capsys.readouterr().out

    challenge.refresh_from_db()
    assert challenge.completed_count == 0
    assert challenge.slots.get(key='A').is_completed is False
    # The SUMMARY counts what was offered, not what was written -- mutating it to `completed` would
    # print zero after listing the squares it would have done.
    assert '1 square(s) would be completed.' in out


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


def test_the_sweep_is_quiet_when_there_is_nothing_to_do(capsys):
    """Says so, rather than only not crashing. The empty branch has to be settled AFTER the loop now,
    because `.iterator()` is always truthy and the `if not rows:` it replaced was dead code."""
    _member()

    call_command('process_challenges')

    assert 'Nothing to complete.' in capsys.readouterr().out


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


def test_the_count_matches_how_many_squares_actually_became_completed():
    """The count's real invariant: it equals the number of squares that went from pending to completed.

    HONEST ABOUT ITS LIMIT: replacing the sum with `len(rows)` passes every test in this file and I could
    not construct one that kills it.

    The reason is NOT the one an earlier draft gave ("every guard the writer applies is already in the
    query's filter") -- the writer re-reads its guards per square while the filter ran once, so a run
    flipping to complete mid-pass would be a single-threaded divergence. What rules that out is the
    counter invariant: `completed <= filled <= total` is enforced by check constraints, so every row
    still in hand is a filled, uncompleted square and the run can only complete on the LAST one. Faking
    it out of band raises rather than diverging.

    What this does pin is that the number means something: two pending squares, one of them not yet
    finished by its owner, so the answer is 1 and not 2, not 0 and not the slot count.
    """
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_JOBS)
    first, second = list(Job.objects.all()[:2])
    done_contract = _contract(f'Game for {first.slug}', jobs=[first])
    svc.assign(challenge, profile, first.slug, done_contract)
    svc.assign(challenge, profile, second.slug,
               _contract(f'Game for {second.slug}', jobs=[second]))
    _finished(profile, done_contract)     # only ONE of the two is finished

    before = challenge.slots.filter(is_completed=True).count()
    reported = svc.detect_for_profile(profile)
    after = challenge.slots.filter(is_completed=True).count()

    assert reported == after - before
    assert reported == 1, 'the unfinished square must not be counted'


def test_challenge_detection_is_isolated_rather_than_dependent():
    """It reads what `process_contracts` writes, and still must NOT be a `DEPENDS_ON` entry.

    That map makes a step SKIP, and a half-written `EarnedContract` set is not a poisoned input here:
    completion is additive and sticky, so the sweep takes what it can see and picks the rest up tomorrow.
    Most of what it completes was stamped on earlier nights anyway, so skipping would forfeit work
    unrelated to tonight's failure.

    Pinned in the shape `test_nightly.test_the_rarity_recompute_does_not_declare_a_dependency` uses, and
    for the same reason: this step resembles a dependent one closely enough that somebody will add the
    entry for symmetry.

    The label is DERIVED from `STEPS`, which is the half that makes it the same shape. Hard-coded, a
    rename plus a matching `DEPENDS_ON` entry would leave this vacuously green.
    """
    from core.management.commands.nightly import DEPENDS_ON, STEPS

    label = next(lbl for lbl, cmd, _kw in STEPS if cmd == 'process_challenges')

    assert label not in DEPENDS_ON


def test_a_square_on_a_finished_run_is_refused_when_handed_over_directly():
    """The RUN's precondition, re-asserted under the lock rather than trusted from the query.

    Both detectors filter `challenge__is_complete=False`, so neither can reach this state -- which is
    exactly why the guard needed a test that BYPASSES them and calls `mark_slot_completed` with the slot
    in hand. Without one, the guard was pinned by nothing: removing it left the whole suite green, and the
    existing finished-run test never gets that far because the query stops it first.

    Reachable for real the moment anything writes `total_slots` or `is_complete` out of band -- a repair
    command, a shell, a data migration -- and a finished run has a badge and a title granted against it.
    """
    profile = _member()
    challenge, _contract_obj = _run_with_one_square(profile)
    Challenge.objects.filter(pk=challenge.pk).update(
        is_complete=True, completed_at=timezone.now())
    slot = challenge.slots.get(key='A')
    assert slot.is_filled and not slot.is_completed, 'the fixture must leave a pending square behind'

    assert svc.mark_slot_completed(slot) is False

    challenge.refresh_from_db()
    assert challenge.completed_count == 0
    assert challenge.slots.get(key='A').is_completed is False
