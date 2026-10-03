"""The Challenge models' DATABASE constraints, which are the whole deliverable of the first chunk.

Every rule pinned here is enforced in Postgres rather than in the service, and each one is here
because the service is not the only writer: the admin, a shell, a data migration and a future repair
command all write around it. What the service adds is a good error message.

ONE of these would fail SILENTLY if the constraint were written the obvious way instead of the right way
(item 3). Items 1 and 2 are here for the opposite reason -- to record that they pin LESS than they look
like they do. Item 1 used to belong in the first group and moved when its key moved; the header said so
for a while after its own paragraph had stopped agreeing:

1. **One contract per run, keyed on the FK.** `challengeslot_unique_contract` stops one contract
   filling six Job Coverage slots (six payouts for one completion). It is keyed on `contract`, the same
   identity the service's duplicate check uses -- it used to be keyed on the frozen `contract_slug`, and
   that disagreement was a reachable 500: a staff rename frees a slug, a different contract takes it, and
   a placement the service allows (different FK) collided on two identical frozen slugs.

   THE PARTIAL CONDITION NO LONGER PROTECTS ANYTHING, and the old version of this paragraph said it did.
   When the key was the blank-not-null slug it was correctness: without it every empty slot collided on
   `''`, and a run is 26 empty slots. `contract` is NULLABLE and Postgres treats NULLs as distinct, so a
   plain unique would already permit them. `test_a_run_holds_a_full_set_of_empty_slots` therefore passes
   either way now; it is kept because "a new run can be created" is worth holding on its own.

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
from django.db import IntegrityError, connection, transaction
from django.db.models import Max
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
    if challenge_type == CHALLENGE_TYPE_JOBS:
        # `total_slots` comes from the migration-seeded catalogue, and `challenge_total_slots_positive`
        # refuses 0 -- so an empty Job table would fail every jobs test with a constraint error about
        # slot counts rather than naming the real cause.
        assert Job.objects.exists(), 'the seeded Job catalogue is empty; see conftest.py'
    now = timezone.now()
    return Challenge.objects.create(
        profile=profile, challenge_type=challenge_type, name=name,
        total_slots=len(AZ_LETTERS) if challenge_type == CHALLENGE_TYPE_AZ else Job.objects.count(),
        is_complete=complete, completed_at=now if complete else None,
        is_deleted=deleted, deleted_at=now if deleted else None,
    )


def _slot(challenge, key, *, position=None, contract_slug='', completed=False, contract=None):
    """A slot on `challenge`.

    `position` defaults to one past the highest in use, rather than to 0, because
    `challengeslot_unique_position` means a fixed default would collide the moment a test wants two
    slots -- and it would fail as an IntegrityError about positions in a test that is about something
    else entirely. `count()` was the first version and was not the same thing: after an explicit
    `position=18`, a count of 1 hands back 1 and then collides on the next call.
    """
    if position is None:
        highest = challenge.slots.aggregate(top=Max('position'))['top']
        position = 0 if highest is None else highest + 1
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
def _refuses(constraint):
    """Assert the block raises `IntegrityError` FROM `constraint`, in its own atomic block.

    Naming the constraint is the point. Twenty refusal tests asserting only the exception TYPE all
    passed while telling us nothing about which rule fired -- and several of these states violate more
    than one rule, so a test can go on passing after the constraint it was written for is dropped. The
    `match` turns each one into a pin on a specific rule.

    The atomic is folded in rather than left to each caller on purpose. Without it the broken
    transaction poisons the rest of the test, so any later assertion fails with
    `TransactionManagementError` and reads like a bug in the code under test rather than in the test.
    An earlier draft described that reasoning while leaving callers to supply their own `atomic()`,
    which invited exactly the omission it was warning about.
    """
    with pytest.raises(IntegrityError, match=constraint):
        with transaction.atomic():
            yield


# ── Sequential runs: one active per type ──────────────────────────────────────────────────────

def test_a_second_active_run_of_the_same_type_is_refused():
    profile = ProfileFactory()
    _challenge(profile)

    with _refuses('challenge_one_active_per_type'):
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

    with _refuses('challenge_completed_at_matches_flag'):
        Challenge.objects.create(profile=profile, challenge_type=CHALLENGE_TYPE_AZ,
                                 name='Finished, supposedly', total_slots=26,
                                 is_complete=True, completed_at=None)


def test_an_unfinished_run_must_not_carry_one():
    """The other half, and the one a reopen path would trip: a stale `completed_at` on an incomplete
    run sorts it into the Hall of Fame's ordering at a position it has not earned."""
    profile = ProfileFactory()

    with _refuses('challenge_completed_at_matches_flag'):
        Challenge.objects.create(profile=profile, challenge_type=CHALLENGE_TYPE_AZ,
                                 name='Not done', total_slots=26,
                                 is_complete=False, completed_at=timezone.now())


def test_a_run_needs_a_name():
    profile = ProfileFactory()

    with _refuses('challenge_name_not_blank'):
        Challenge.objects.create(profile=profile, challenge_type=CHALLENGE_TYPE_AZ, name='',
                                 total_slots=26)


def test_an_unknown_type_is_refused_by_the_database_not_only_by_choices():
    """`choices` is a form and admin concern; Postgres does not enforce it. A challenge with an
    unknown type has no slot keys that make sense, so there is no UI path back from one.

    `genre`, NOT `calendar`: this used `'calendar'` while the plan said the Platinum Calendar would not
    return, and when it DID return (2026-10-02) the test began asserting that a valid type is rejected.
    It failed loudly, which is the good outcome, but the example should be one that cannot be promoted
    later. `genre` is the retired challenge type -- gone, and the likeliest thing an old caller sends.
    """
    profile = ProfileFactory()

    with _refuses('challenge_type_valid'):
        Challenge.objects.create(profile=profile, challenge_type='genre',
                                 name='The one that does not return', total_slots=26)


# ── Slots: one contract per run ──────────────────────────────────────

def test_a_run_holds_a_full_set_of_empty_slots():
    """A new run is 26 empty slots and creating one must work.

    THIS WAS THE PARTIAL-UNIQUE PIN and it no longer is, which is worth saying rather than leaving the old
    docstring to imply coverage that moved. While `challengeslot_unique_contract` keyed on the blank-not-null
    `contract_slug`, dropping its condition collided every empty slot on `''` and this test caught it. The key
    is the nullable `contract` FK now, and Postgres treats NULLs as distinct, so the condition is scoping
    rather than protection and nothing here can fail on it."""
    challenge = _challenge(ProfileFactory())

    ChallengeSlot.objects.bulk_create([
        ChallengeSlot(challenge=challenge, key=letter, position=i)
        for i, letter in enumerate(AZ_LETTERS)
    ])

    assert challenge.slots.count() == len(AZ_LETTERS)
    assert not any(slot.is_filled for slot in challenge.slots.all())


def test_one_contract_cannot_fill_two_slots_in_the_same_run():
    """A contract carries up to six jobs. Without this, one completion fills six Job Coverage slots
    and pays six times.

    ON THE FK, because that is what the constraint keys on. This passed the same `contract_slug` twice and no
    FK at all, which pinned the OLD identity -- and once the key moved it pinned nothing, because two slots
    with a null `contract` do not collide (Postgres treats NULLs as distinct)."""
    challenge = _challenge(ProfileFactory(), challenge_type=CHALLENGE_TYPE_JOBS)
    contract = _contract('Some RPG')
    _slot(challenge, 'mage', position=0, contract_slug=contract.slug, contract=contract)

    with _refuses('challengeslot_unique_contract'):
        _slot(challenge, 'champion', position=1, contract_slug=contract.slug, contract=contract)


def test_the_same_contract_may_appear_in_two_different_runs():
    """Scoped per run, not globally. One platinum legitimately advances an A-Z slot and a Job
    Coverage slot at once, and a later run is allowed to reuse a game the hunter never completed."""
    profile = ProfileFactory()
    az = _challenge(profile, challenge_type=CHALLENGE_TYPE_AZ)
    jobs = _challenge(profile, challenge_type=CHALLENGE_TYPE_JOBS, name='Jobs run')

    contract = _contract('Some RPG')
    _slot(az, 'S', position=18, contract_slug=contract.slug, contract=contract)

    # ON THE FK, or this asserts nothing: two null-contract slots never collide, so the old slug-only form
    # would have passed even if the constraint were global rather than per-run.
    assert _slot(jobs, 'mage', position=0, contract_slug=contract.slug, contract=contract).pk is not None


def test_a_slot_key_appears_once_per_run():
    challenge = _challenge(ProfileFactory())
    _slot(challenge, 'A', position=0)

    with _refuses('challengeslot_unique_key'):
        _slot(challenge, 'A', position=1)


def test_an_empty_slot_cannot_be_completed():
    """Completion without a contract is a slot claiming credit for nothing, and it would render as a
    filled tile with no game on it."""
    challenge = _challenge(ProfileFactory())

    with _refuses('challengeslot_completed_is_filled_dated_and_explained'):
        ChallengeSlot.objects.create(challenge=challenge, key='A', position=0,
                                     is_completed=True, completed_at=timezone.now(),
                                     completed_via=COMPLETED_VIA_LIVE)


def test_a_completed_slot_must_be_dated():
    challenge = _challenge(ProfileFactory())

    with _refuses('challengeslot_completed_is_filled_dated_and_explained'):
        ChallengeSlot.objects.create(challenge=challenge, key='A', position=0,
                                     contract_slug='a-game', contract_name='A Game',
                                     is_completed=True, completed_at=None,
                                     completed_via=COMPLETED_VIA_LIVE)


def test_xp_cannot_be_redeemed_for_an_incomplete_slot():
    """The guard one layer above the ledger. An unfinished slot paying out is the double-spend's
    cousin, and the ledger it writes to is append-only."""
    challenge = _challenge(ProfileFactory(), challenge_type=CHALLENGE_TYPE_JOBS)

    with _refuses('challengeslot_xp_needs_completion'):
        ChallengeSlot.objects.create(challenge=challenge, key='mage', position=0,
                                     contract_slug='a-game', contract_name='A Game',
                                     is_completed=False, xp_redeemed_at=timezone.now())


def test_is_filled_reads_the_snapshot_and_not_the_live_fk():
    """`is_filled` reads `contract_slug`, not `contract_id`. Flip the property to the FK and this fails.

    Scoped deliberately narrowly: this test never creates a Contract, so it says nothing about what
    happens when one is deleted. That claim belongs to
    `test_a_finished_slot_survives_its_contract_being_deleted`, which does the real thing."""
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

    with _refuses('xpgrant_challenge_once_per_slot'):
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

    with _refuses('xpgrant_challenge_needs_source_id'):
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

    with _refuses('challengeslot_unique_position'):
        _slot(challenge, 'B', position=0)


def test_a_completed_slot_must_say_how_it_was_completed():
    """`completed_via` is not decoration: two of its three values are catch-up mechanisms with
    different justifications, and a hunter reading their own finished run is entitled to see which
    applied to which slot. The constraint's comment claimed this clause before the clause existed."""
    challenge = _challenge(ProfileFactory())

    with _refuses('challengeslot_completed_is_filled_dated_and_explained'):
        ChallengeSlot.objects.create(challenge=challenge, key='A', position=0,
                                     contract_slug='a-game', contract_name='A Game',
                                     is_completed=True, completed_at=timezone.now(),
                                     completed_via='')


def test_an_unknown_completed_via_is_refused_by_the_database():
    challenge = _challenge(ProfileFactory())

    with _refuses('challengeslot_completed_via_valid'):
        ChallengeSlot.objects.create(challenge=challenge, key='A', position=0,
                                     contract_slug='a-game', contract_name='A Game',
                                     is_completed=True, completed_at=timezone.now(),
                                     completed_via='somehow')


def test_a_run_cannot_have_completed_more_slots_than_it_has_filled():
    profile = ProfileFactory()

    with _refuses('challenge_completed_within_filled'):
        Challenge.objects.create(profile=profile, challenge_type=CHALLENGE_TYPE_AZ, name='Fibbing',
                                 total_slots=26, filled_count=2, completed_count=3)


def test_a_run_cannot_have_filled_more_slots_than_it_has():
    profile = ProfileFactory()

    with _refuses('challenge_filled_within_total'):
        Challenge.objects.create(profile=profile, challenge_type=CHALLENGE_TYPE_AZ, name='Fibbing',
                                 total_slots=26, filled_count=27)


def test_a_run_with_no_slots_is_refused():
    """A zero-slot run is complete before it starts, and would render as "0 / 0"."""
    profile = ProfileFactory()

    with _refuses('challenge_total_slots_positive'):
        Challenge.objects.create(profile=profile, challenge_type=CHALLENGE_TYPE_AZ, name='Empty',
                                 total_slots=0)


def test_a_soft_deleted_run_must_carry_a_deletion_date():
    profile = ProfileFactory()

    with _refuses('challenge_deleted_at_matches_flag'):
        Challenge.objects.create(profile=profile, challenge_type=CHALLENGE_TYPE_AZ, name='Gone',
                                 total_slots=26, is_deleted=True, deleted_at=None)


def test_a_live_run_must_not_carry_one():
    profile = ProfileFactory()

    with _refuses('challenge_deleted_at_matches_flag'):
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


# ── The index inventory, pinned ───────────────────────────────────────────────────────────────

def test_the_slot_table_carries_no_redundant_indexes():
    """A slot table holding 26 rows per run does not need nine indexes, and it had them.

    Two arrived by accident and both were removed: an explicit `(challenge, position)` index that
    duplicated `challengeslot_unique_position`'s own btree exactly, and the pair `SlugField` builds by
    default on `contract_slug` (a plain btree plus a `varchar_pattern_ops` one for LIKE) -- neither of
    which serves any read worth indexing at 25-26 rows per run. That used to say "because the only access
    pattern on that column is the per-challenge partial unique", which stopped being true when the unique moved
    onto the FK: `contract_slug` now has NO index, and `pending_slots()` plus `assign`'s dead-snapshot clause
    read it unindexed. Still the right call, on the table's size rather than on a covering index.

    Pinned by introspection rather than by reading the migration, because the migration is what we were
    already reading when the duplicates went in. Asserting on real `pg_indexes` output is the only
    version of this test that could have caught them.
    """
    with connection.cursor() as cur:
        cur.execute("SELECT indexname FROM pg_indexes WHERE tablename = %s",
                    [ChallengeSlot._meta.db_table])
        names = {row[0] for row in cur.fetchall()}

    assert not {n for n in names if 'contract_slug' in n}, (
        f'SlugField rebuilt its default indexes on contract_slug: {sorted(names)}'
    )
    assert 'chalslot_position_idx' not in names, (
        'the redundant (challenge, position) index is back; challengeslot_unique_position already '
        'builds that btree'
    )
    # The ones that must exist: pkey, the three constraint-backed uniques, and the two FK indexes
    # Postgres does NOT create automatically -- Django adds them for `challenge_id`.
    assert 'challengeslot_unique_position' in names
    assert 'challengeslot_unique_key' in names
    assert 'challengeslot_unique_contract' in names


def test_the_challenge_table_does_not_index_a_two_value_column():
    """`db_index=True` was dropped from `challenge_type` because `chal_type_completed_idx` already
    leads with it, and the same commit was removing another index for being redundant."""
    with connection.cursor() as cur:
        cur.execute("SELECT indexname FROM pg_indexes WHERE tablename = %s",
                    [Challenge._meta.db_table])
        names = {row[0] for row in cur.fetchall()}

    assert not {n for n in names if n.endswith('challenge_type_a3d1c0f9') or
                ('challenge_type' in n and n != 'chal_type_completed_idx')}, (
        f'a bare index on challenge_type is back: {sorted(names)}'
    )
    assert 'chal_type_completed_idx' in names


# ── readable_by: the visibility vocabulary, tested directly ───────────────────────────────────────

def test_readable_by_gives_an_anonymous_reader_only_visible_runs():
    """`profile is None` is its own branch, so it gets its own test -- the gamelists twin is pinned the
    same way."""
    mine = ProfileFactory()
    visible = Challenge.objects.create(profile=mine, challenge_type=CHALLENGE_TYPE_AZ,
                                       name='Visible', total_slots=26)
    hidden = Challenge.objects.create(profile=mine, challenge_type=CHALLENGE_TYPE_JOBS,
                                      name='Hidden', total_slots=25, is_deleted=True,
                                      deleted_at=timezone.now())

    readable = set(Challenge.objects.readable_by(None).values_list('pk', flat=True))

    assert visible.pk in readable
    assert hidden.pk not in readable


def test_readable_by_adds_your_own_hidden_runs_and_nobody_elses():
    mine, theirs = ProfileFactory(), ProfileFactory()
    my_hidden = Challenge.objects.create(profile=mine, challenge_type=CHALLENGE_TYPE_AZ, name='Mine',
                                         total_slots=26, is_deleted=True, deleted_at=timezone.now())
    their_hidden = Challenge.objects.create(profile=theirs, challenge_type=CHALLENGE_TYPE_AZ,
                                            name='Theirs', total_slots=26, is_deleted=True,
                                            deleted_at=timezone.now())
    their_visible = Challenge.objects.create(profile=theirs, challenge_type=CHALLENGE_TYPE_JOBS,
                                             name='Open', total_slots=25)

    readable = set(Challenge.objects.readable_by(mine).values_list('pk', flat=True))

    assert my_hidden.pk in readable
    assert their_visible.pk in readable
    assert their_hidden.pk not in readable


def test_readable_by_cannot_duplicate_a_row():
    """An OR across two conditions that both match is the classic way to get a row twice. Both sit on
    `challenges_challenge` columns with no join, so it cannot fan out -- pinned because the day one of
    them grows a join is the day it can."""
    mine = ProfileFactory()
    # Visible AND owned: both sides of the OR match this row.
    Challenge.objects.create(profile=mine, challenge_type=CHALLENGE_TYPE_AZ, name='Both',
                             total_slots=26)

    assert Challenge.objects.readable_by(mine).count() == 1


def test_visible_and_readable_by_share_one_definition_of_the_flag():
    """The duplicate this replaced was two spellings of `is_deleted=False`, one per method, under a
    docstring arguing the flag had one home. Adding a condition to `VISIBLE` must reach BOTH -- which is
    the property that was broken and is otherwise invisible until somebody adds one.
    """
    mine = ProfileFactory()
    Challenge.objects.create(profile=mine, challenge_type=CHALLENGE_TYPE_AZ, name='A', total_slots=26)

    visible_sql = str(Challenge.objects.visible().query)
    readable_sql = str(Challenge.objects.readable_by(mine).query)

    # Both must express the flag, and `readable_by` must additionally express ownership.
    assert 'is_deleted' in visible_sql
    assert 'is_deleted' in readable_sql
    assert 'profile_id' in readable_sql
    # And the Q object really is shared, not copied.
    from challenges.models import ChallengeQuerySet
    assert ChallengeQuerySet.VISIBLE.children == [('is_deleted', False)]


_CSEQ = {'n': 0}


def _contract(name):
    """A live contract. No member concepts: nothing here reads membership, only identity."""
    _CSEQ['n'] += 1
    return Contract.objects.create(name=name, slug='slot-uniq-%d' % _CSEQ['n'],
                                   is_live=True, igdb_id=930_000 + _CSEQ['n'])


def test_two_different_contracts_may_share_a_frozen_slug():
    """THE 500 THAT MOVING THE KEY CLOSED, and it needs no bad data to reach.

    `contract_slug` is a SNAPSHOT taken at assignment. `Contract.slug` is globally unique, so two contracts
    cannot hold one slug at the same time -- but a rename frees the string, and the next contract to take it
    then matches a slug some slot froze months ago. While the constraint keyed on that snapshot, the second
    placement raised `IntegrityError` even though the service had just allowed it (different `contract_id`),
    and nothing caught it: an uncaught 500 on an ordinary placement.

    Keyed on the FK, the two rows are what they always were -- two different games -- and both are legal.
    """
    challenge = _challenge(ProfileFactory(), challenge_type=CHALLENGE_TYPE_JOBS)
    first, second = _contract('Sonic Frontiers'), _contract('Sonic Superstars')

    # THE SNAPSHOTS ARE WRITTEN DIRECTLY, which is what the constraint sees. An earlier version also called
    # `Contract.objects.update(slug=...)` on both rows to dramatise the rename; those two lines were inert
    # (the helper's slugs were never `sonic-frontiers`, and nothing here reads `Contract.slug`), so they
    # implied coverage of the rename path that this test does not have. The service-level version below does.
    _slot(challenge, 'mage', position=0, contract_slug='sonic-frontiers', contract=first)
    _slot(challenge, 'champion', position=1, contract_slug='sonic-frontiers', contract=second)

    held = list(challenge.slots.exclude(contract=None).order_by('position'))
    assert [s.contract_id for s in held] == [first.pk, second.pk]
    assert {s.contract_slug for s in held} == {'sonic-frontiers'}


def test_a_deleted_contract_releases_its_slots_from_the_constraint():
    """`on_delete=SET_NULL` nulls the FK on every slot that held the contract, and a partial unique over a
    nullable column does not constrain NULLs -- so two squares that both held it end up unconstrained. That is
    acceptable and deliberate: no new assignment can duplicate a contract that is gone from the pool, and the
    squares keep their snapshots. Pinned so the consequence is a decision on record rather than a surprise."""
    challenge = _challenge(ProfileFactory(), challenge_type=CHALLENGE_TYPE_JOBS)
    contract = _contract('Astro Bot')
    frozen = contract.slug
    slot = _slot(challenge, 'mage', position=0, contract_slug=frozen, contract=contract)

    Contract.objects.filter(pk=contract.pk).delete()

    kept = ChallengeSlot.objects.get(pk=slot.pk)
    assert kept.contract_id is None
    # The SNAPSHOT survives the row it pointed at, which is the whole reason it is stored.
    assert kept.contract_slug == frozen
    # NOT `contract_name`: asserting it equals `frozen.replace('-', ' ').title()` re-derived `_slot`'s own
    # expression, so it tested the helper rather than the model.
    # And a second slot may now carry the same dead snapshot, which is what "does not constrain NULLs" means.
    assert _slot(challenge, 'champion', position=1, contract_slug=frozen).pk is not None


def test_one_contract_cannot_hold_two_squares_under_two_different_snapshots():
    """THE SECOND HOLE THE KEY MOVE CLOSED, which had no pin at all.

    While the constraint read the frozen snapshot, a contract renamed between two placements sat in two job
    squares legally -- two different strings, one game. The service guard was fixed for this first (it matches
    on the FK); the database permitted it until the key moved. Both layers refuse it now.
    """
    challenge = _challenge(ProfileFactory(), challenge_type=CHALLENGE_TYPE_JOBS)
    contract = _contract('Some RPG')
    _slot(challenge, 'mage', position=0, contract_slug='some-rpg', contract=contract)

    with _refuses('challengeslot_unique_contract'):
        _slot(challenge, 'champion', position=1, contract_slug='some-rpg-remastered', contract=contract)
