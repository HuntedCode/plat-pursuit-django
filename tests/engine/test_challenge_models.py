"""The Challenge models' DATABASE constraints, which are the whole deliverable of the first chunk.

Every rule pinned here is enforced in Postgres rather than in the service, and each one is here
because the service is not the only writer: the admin, a shell, a data migration and a future repair
command all write around it. What the service adds is a good error message.

Three of these would fail SILENTLY if the constraint were written the obvious way instead of the right
way, and those are the tests worth having:

1. **The empty-slot trap.** `challengeslot_unique_contract` stops one contract filling six Job
   Coverage slots (six payouts for one completion). Written as a plain unique over a NULLABLE column
   it would also collide every EMPTY slot with every other, because a brand-new run is 26 empty
   slots -- so `contract_slug` is blank-not-null and the constraint is PARTIAL. The test that catches
   a regression here is the boring one: creating a full set of empty slots must work.

2. **The ledger's new source pays once.** `xpgrant_challenge_once_per_slot` refuses a second grant
   for the same (profile, job, slot), which is the guard `xp-economy.md` demanded of the first
   non-contract source.

   MUTATION-CHECKED AND HONESTLY LABELLED: dropping that constraint's `condition=Q(source='challenge')`
   changes NOTHING that these tests can see, and the first draft of this docstring claimed otherwise.
   The reason is the same NULL-distinctness fact as point 1, pointing the other way: `contract` and
   `manual` grants carry `source_id=None`, so a blanket unique over the four columns never collides
   for them either. The condition is therefore SCOPING, not protection -- it keeps the constraint
   saying "this is the challenge source's idempotency" so a future `quest` source is left to own its
   own, per the rule the ledger's docstring states. The two non-refusal tests below still earn their
   place (contract and manual grants must stay repeatable, and that is true and worth holding), but
   they are NOT pins on the partial condition and must not be read as such.

3. **Both halves of the active-run predicate.** One active run per type is what makes runs
   sequential, but a completed run and a deleted run each have to stop occupying the slot. Name only
   `is_complete` in the condition and finishing your A-Z locks you out of ever starting another.
"""
import contextlib

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from challenges.models import (
    AZ_LETTERS,
    CHALLENGE_TYPE_AZ,
    CHALLENGE_TYPE_JOBS,
    COMPLETED_VIA_LIVE,
    Challenge,
    ChallengeSlot,
)
from tests.factories import ProfileFactory
from trophies.models import Contract, ContractXPGrant, Job

pytestmark = pytest.mark.django_db


def _challenge(profile, *, challenge_type=CHALLENGE_TYPE_AZ, complete=False, deleted=False,
               name='A run'):
    """A challenge in one of the three states the active-run predicate cares about.

    `completed_at` tracks `is_complete` because `challenge_completed_at_matches_flag` requires it --
    so a helper that let them disagree would fail every test for the wrong reason.
    """
    now = timezone.now()
    return Challenge.objects.create(
        profile=profile, challenge_type=challenge_type, name=name,
        total_slots=len(AZ_LETTERS) if challenge_type == CHALLENGE_TYPE_AZ else Job.objects.count(),
        is_complete=complete, completed_at=now if complete else None,
        is_deleted=deleted, deleted_at=now if deleted else None,
    )


def _slot(challenge, key, *, position=None, contract_slug='', completed=False, contract=None):
    """A slot on `challenge`.

    `position` defaults to the next free one rather than to 0, because `challengeslot_unique_position`
    means a fixed default would collide the moment a test wants two slots -- and it would fail as an
    IntegrityError about positions in a test that is about something else entirely.
    """
    if position is None:
        position = challenge.slots.count()
    return ChallengeSlot.objects.create(
        challenge=challenge, key=key, position=position, contract=contract,
        contract_slug=contract_slug,
        contract_name=contract_slug.replace('-', ' ').title() if contract_slug else '',
        assigned_at=timezone.now() if contract_slug else None,
        is_completed=completed,
        completed_at=timezone.now() if completed else None,
        completed_via=COMPLETED_VIA_LIVE if completed else '',
    )


@contextlib.contextmanager
def _refuses():
    """Assert the block raises `IntegrityError`, AND give it its own atomic block.

    The atomic is folded in rather than left to each caller on purpose. Without it the broken
    transaction poisons the rest of the test, so any later assertion fails with
    `TransactionManagementError` and reads like a bug in the code under test rather than in the test.
    An earlier draft described that reasoning while leaving callers to supply their own `atomic()`,
    which invited exactly the omission it was warning about.
    """
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            yield


# ── Sequential runs: one active per type ──────────────────────────────────────────────────────

def test_a_second_active_run_of_the_same_type_is_refused():
    profile = ProfileFactory()
    _challenge(profile)

    with _refuses():
        _challenge(profile, name='A second run')


def test_completing_a_run_frees_the_slot_for_the_next_one():
    """The point of sequential runs: you finish one and start another. If this fails, the reward for
    completing an A-Z is never being allowed to do it again."""
    profile = ProfileFactory()
    _challenge(profile, complete=True)

    assert _challenge(profile, name='Run two').pk is not None


def test_deleting_a_run_frees_the_slot_too():
    """Abandoning a run has to be recoverable. A hunter who deletes a half-finished A-Z and cannot
    start over has bricked the feature for themselves with no way back."""
    profile = ProfileFactory()
    _challenge(profile, deleted=True)

    assert _challenge(profile, name='Take two').pk is not None


def test_the_two_types_do_not_collide():
    profile = ProfileFactory()
    _challenge(profile, challenge_type=CHALLENGE_TYPE_AZ)

    assert _challenge(profile, challenge_type=CHALLENGE_TYPE_JOBS, name='Jobs run').pk is not None


def test_two_hunters_each_get_their_own_active_run():
    _challenge(ProfileFactory())

    assert _challenge(ProfileFactory()).pk is not None


def test_a_completed_run_must_carry_a_completion_date():
    profile = ProfileFactory()

    with _refuses():
        Challenge.objects.create(profile=profile, challenge_type=CHALLENGE_TYPE_AZ,
                                 name='Finished, supposedly', total_slots=26,
                                 is_complete=True, completed_at=None)


def test_an_unfinished_run_must_not_carry_one():
    """The other half, and the one a reopen path would trip: a stale `completed_at` on an incomplete
    run sorts it into the Hall of Fame's ordering at a position it has not earned."""
    profile = ProfileFactory()

    with _refuses():
        Challenge.objects.create(profile=profile, challenge_type=CHALLENGE_TYPE_AZ,
                                 name='Not done', total_slots=26,
                                 is_complete=False, completed_at=timezone.now())


def test_a_run_needs_a_name():
    profile = ProfileFactory()

    with _refuses():
        Challenge.objects.create(profile=profile, challenge_type=CHALLENGE_TYPE_AZ, name='',
                                 total_slots=26)


def test_an_unknown_type_is_refused_by_the_database_not_only_by_choices():
    """`choices` is a form and admin concern; Postgres does not enforce it. A challenge with an
    unknown type has no slot keys that make sense, so there is no UI path back from one."""
    profile = ProfileFactory()

    with _refuses():
        Challenge.objects.create(profile=profile, challenge_type='calendar',
                                 name='The one that does not return', total_slots=365)


# ── Slots: one contract per run, and the empty-slot trap ──────────────────────────────────────

def test_a_run_holds_a_full_set_of_empty_slots():
    """THE PARTIAL-UNIQUE PIN. A new run is 26 empty slots, and if `challengeslot_unique_contract`
    were not partial they would all collide with each other on the blank value -- so this boring
    test is the one that catches the constraint being written the obvious way."""
    challenge = _challenge(ProfileFactory())

    ChallengeSlot.objects.bulk_create([
        ChallengeSlot(challenge=challenge, key=letter, position=i)
        for i, letter in enumerate(AZ_LETTERS)
    ])

    assert challenge.slots.count() == len(AZ_LETTERS)
    assert not any(slot.is_filled for slot in challenge.slots.all())


def test_one_contract_cannot_fill_two_slots_in_the_same_run():
    """A contract carries up to six jobs. Without this, one completion fills six Job Coverage slots
    and pays six times."""
    challenge = _challenge(ProfileFactory(), challenge_type=CHALLENGE_TYPE_JOBS)
    _slot(challenge, 'mage', position=0, contract_slug='some-rpg')

    with _refuses():
        _slot(challenge, 'champion', position=1, contract_slug='some-rpg')


def test_the_same_contract_may_appear_in_two_different_runs():
    """Scoped per run, not globally. One platinum legitimately advances an A-Z slot and a Job
    Coverage slot at once, and a later run is allowed to reuse a game the hunter never completed."""
    profile = ProfileFactory()
    az = _challenge(profile, challenge_type=CHALLENGE_TYPE_AZ)
    jobs = _challenge(profile, challenge_type=CHALLENGE_TYPE_JOBS, name='Jobs run')

    _slot(az, 'S', position=18, contract_slug='some-rpg')

    assert _slot(jobs, 'mage', position=0, contract_slug='some-rpg').pk is not None


def test_a_slot_key_appears_once_per_run():
    challenge = _challenge(ProfileFactory())
    _slot(challenge, 'A', position=0)

    with _refuses():
        _slot(challenge, 'A', position=1)


def test_an_empty_slot_cannot_be_completed():
    """Completion without a contract is a slot claiming credit for nothing, and it would render as a
    filled tile with no game on it."""
    challenge = _challenge(ProfileFactory())

    with _refuses():
        ChallengeSlot.objects.create(challenge=challenge, key='A', position=0,
                                     is_completed=True, completed_at=timezone.now(),
                                     completed_via=COMPLETED_VIA_LIVE)


def test_a_completed_slot_must_be_dated():
    challenge = _challenge(ProfileFactory())

    with _refuses():
        ChallengeSlot.objects.create(challenge=challenge, key='A', position=0,
                                     contract_slug='a-game', contract_name='A Game',
                                     is_completed=True, completed_at=None,
                                     completed_via=COMPLETED_VIA_LIVE)


def test_xp_cannot_be_redeemed_for_an_incomplete_slot():
    """The guard one layer above the ledger. An unfinished slot paying out is the double-spend's
    cousin, and the ledger it writes to is append-only."""
    challenge = _challenge(ProfileFactory(), challenge_type=CHALLENGE_TYPE_JOBS)

    with _refuses():
        ChallengeSlot.objects.create(challenge=challenge, key='mage', position=0,
                                     contract_slug='a-game', contract_name='A Game',
                                     is_completed=False, xp_redeemed_at=timezone.now())


def test_is_filled_reads_the_snapshot_and_not_the_live_fk():
    """A slot whose contract row went away is still filled. That is the snapshot's entire purpose --
    `reconcile_contracts` and staff re-anchoring both move the catalogue under a finished run."""
    challenge = _challenge(ProfileFactory())
    slot = _slot(challenge, 'A', position=0, contract_slug='a-game')

    assert slot.contract_id is None      # never joined one; the snapshot is what it has
    assert slot.is_filled


# ── The job-XP ledger's new source, and what it must NOT constrain ────────────────────────────

def _grant(profile, job, *, source, source_id, amount=6000):
    return ContractXPGrant.objects.create(profile=profile, job=job, amount=amount,
                                          source=source, source_id=source_id)


def test_a_challenge_slot_pays_its_job_once():
    """The ledger half of the idempotency guard. `xp-economy.md` warned that the first non-contract
    source must own this, because a double-pay into an append-only ledger can only be offset by a
    negating row, never removed."""
    profile = ProfileFactory()
    job = Job.objects.first()
    _grant(profile, job, source='challenge', source_id=1)

    with _refuses():
        _grant(profile, job, source='challenge', source_id=1)


def test_two_different_slots_may_each_pay_the_same_job():
    """Scoped to the slot, not the job. Nothing stops a hunter's A-Z and Job Coverage runs both
    touching Mage, and a second run legitimately pays it again."""
    profile = ProfileFactory()
    job = Job.objects.first()
    _grant(profile, job, source='challenge', source_id=1)

    assert _grant(profile, job, source='challenge', source_id=2).pk is not None


def test_the_constraint_does_not_touch_contract_grants():
    """Two contract grants for one job is the NORMAL case -- the platinum tier and the 100% tier of
    the same contract are two rows -- so whatever the challenge source adds must leave that alone.

    Note what this does and does not prove: it holds that the engine still works, which is the thing
    worth holding. It is NOT evidence for the constraint's `condition`, because contract grants carry
    `source_id=None` and NULLs are distinct in a unique index, so they would survive a blanket unique
    too. See the module docstring's point 2."""
    profile = ProfileFactory()
    job = Job.objects.first()
    _grant(profile, job, source='contract', source_id=None, amount=4200)

    assert _grant(profile, job, source='contract', source_id=None, amount=1800).pk is not None


def test_manual_grants_stay_repeatable():
    """A staff escape hatch that can only be used once is not an escape hatch."""
    profile = ProfileFactory()
    job = Job.objects.first()
    _grant(profile, job, source='manual', source_id=None)

    assert _grant(profile, job, source='manual', source_id=None).pk is not None



# ── Constraints added after the chunk-0 audit ─────────────────────────────────────────────────

def test_a_challenge_grant_must_identify_its_slot():
    """THE HOLE THE AUDIT FOUND, and the one with money behind it.

    `xpgrant_challenge_once_per_slot` is a unique over (profile, job, source, source_id). Because
    Postgres treats NULLs as DISTINCT, a grant written with `source_id=None` collided with nothing and
    could be inserted without limit -- an unbounded double-pay into an append-only ledger, which is
    precisely what that unique index was added to prevent. The original tests all passed a real
    `source_id`, so none of them went near it.
    """
    profile = ProfileFactory()
    job = Job.objects.first()

    with _refuses():
        _grant(profile, job, source='challenge', source_id=None)


def test_a_sibling_source_may_still_omit_its_source_id():
    """The fix must be scoped to the challenge source. Contract grants carry `source_id=None` by
    design -- the contract is identified by `earned_contract`, not by this column."""
    profile = ProfileFactory()
    job = Job.objects.first()

    assert _grant(profile, job, source='contract', source_id=None).pk is not None


def test_two_slots_cannot_share_a_position():
    """What makes `Meta.ordering` deterministic rather than usually-deterministic. Duplicate positions
    would let the slot grid reshuffle between page loads."""
    challenge = _challenge(ProfileFactory())
    _slot(challenge, 'A', position=0)

    with _refuses():
        _slot(challenge, 'B', position=0)


def test_a_completed_slot_must_say_how_it_was_completed():
    """`completed_via` is not decoration: two of its three values are catch-up mechanisms with
    different justifications, and a hunter reading their own finished run is entitled to see which
    applied to which slot. The constraint's comment claimed this clause before the clause existed."""
    challenge = _challenge(ProfileFactory())

    with _refuses():
        ChallengeSlot.objects.create(challenge=challenge, key='A', position=0,
                                     contract_slug='a-game', contract_name='A Game',
                                     is_completed=True, completed_at=timezone.now(),
                                     completed_via='')


def test_an_unknown_completed_via_is_refused_by_the_database():
    challenge = _challenge(ProfileFactory())

    with _refuses():
        ChallengeSlot.objects.create(challenge=challenge, key='A', position=0,
                                     contract_slug='a-game', contract_name='A Game',
                                     is_completed=True, completed_at=timezone.now(),
                                     completed_via='somehow')


def test_a_run_cannot_have_completed_more_slots_than_it_has_filled():
    profile = ProfileFactory()

    with _refuses():
        Challenge.objects.create(profile=profile, challenge_type=CHALLENGE_TYPE_AZ, name='Fibbing',
                                 total_slots=26, filled_count=2, completed_count=3)


def test_a_run_cannot_have_filled_more_slots_than_it_has():
    profile = ProfileFactory()

    with _refuses():
        Challenge.objects.create(profile=profile, challenge_type=CHALLENGE_TYPE_AZ, name='Fibbing',
                                 total_slots=26, filled_count=27)


def test_a_run_with_no_slots_is_refused():
    """A zero-slot run is complete before it starts, and would render as "0 / 0"."""
    profile = ProfileFactory()

    with _refuses():
        Challenge.objects.create(profile=profile, challenge_type=CHALLENGE_TYPE_AZ, name='Empty',
                                 total_slots=0)


def test_a_soft_deleted_run_must_carry_a_deletion_date():
    profile = ProfileFactory()

    with _refuses():
        Challenge.objects.create(profile=profile, challenge_type=CHALLENGE_TYPE_AZ, name='Gone',
                                 total_slots=26, is_deleted=True, deleted_at=None)


def test_a_live_run_must_not_carry_one():
    profile = ProfileFactory()

    with _refuses():
        Challenge.objects.create(profile=profile, challenge_type=CHALLENGE_TYPE_AZ, name='Here',
                                 total_slots=26, is_deleted=False, deleted_at=timezone.now())


def test_a_finished_slot_survives_its_contract_being_deleted():
    """THE SNAPSHOT'S ACTUAL DESIGN CLAIM, exercised rather than asserted.

    An earlier version of this test never created a Contract at all, so it pinned only that
    `is_filled` reads the snapshot -- while its docstring claimed to prove a finished run survives the
    contract going away. This does the real thing, which also pins that `on_delete` is SET_NULL and
    not CASCADE. A CASCADE typo there would silently destroy completed runs, and nothing else in this
    file would notice.
    """
    challenge = _challenge(ProfileFactory())
    contract = Contract.objects.create(name='A Game', slug='a-game', is_live=True, igdb_id=4242)
    slot = _slot(challenge, 'A', contract=contract, contract_slug='a-game', completed=True)

    contract.delete()
    slot.refresh_from_db()

    assert slot.contract_id is None          # the live join is gone
    assert slot.is_filled                    # the snapshot is not
    assert slot.contract_name == 'A Game'
    assert slot.is_completed
