"""`challenge_service` -- the only thing that writes a challenge.

What is worth pinning here is not that the functions run, but the RULES they encode, several of which
are the product of a decision that could have gone the other way and would look equally reasonable in
code. Those are the ones a future reader will be tempted to "simplify":

- **`start` resumes before it creates.** Hiding a run is not deleting it, so pressing Start again brings
  the same run back with its progress. A version that created a fresh run would pass any test that only
  checked "a run exists afterwards", and would silently throw away completed squares.
- **A finished square never clears.** This is load-bearing for the XP economy, not tidiness: the job-XP
  guard is keyed on the slot, so clearing a paid square and refilling it is exactly how one job gets
  paid twice in one run.
- **An already-completed contract is refused, unless the importer or the hatch lifts it** -- and the two
  are labelled differently on purpose, because `import` carries a fairness date and `hatch` carries an
  admission that our supply failed the hunter.
- **The beta gate refuses with a message rather than redirecting.** When Challenges was parked the
  decision was that a free hunter gets told so on a page; `PremiumRequiredMixin` would bounce them.

The Job catalogue comes from the migration seed (`conftest.py` repairs it per session), so `jobs` runs
depend on it being non-empty -- `slot_keys_for` reads it and the count becomes `total_slots`.
"""
import contextlib

import pytest
from django.test import override_settings
from django.utils import timezone

from challenges.models import (
    AZ_LETTERS,
    CHALLENGE_TYPE_AZ,
    CHALLENGE_TYPE_JOBS,
    COMPLETED_VIA_HATCH,
    COMPLETED_VIA_IMPORT,
    COMPLETED_VIA_LIVE,
    HATCH_THRESHOLD,
    Challenge,
)
from challenges.services import challenge_service as svc
from challenges.services import eligibility
from tests.factories import (
    ConceptFactory,
    EarnedTrophyFactory,
    GameFactory,
    IGDBMatchFactory,
    ProfileFactory,
    ProfileGameFactory,
    TrophyFactory,
)
from trophies.models import Contract, EarnedContract, Job

pytestmark = pytest.mark.django_db

#: The beta is ON by default in settings, so tests about anything OTHER than the gate run as a member
#: unless they say otherwise. `open_beta` below is the shorthand for the post-beta world.
open_beta = override_settings(CHALLENGES_BETA_MEMBERS_ONLY=False)

_SEQ = {'n': 0}


def _slug(name):
    _SEQ['n'] += 1
    return f"{name.lower().replace(' ', '-').replace(':', '')}-{_SEQ['n']}"


def _member(*, premium=True):
    """A linked profile, premium by default because the beta gates creation to members.

    `user_is_premium` is a real column on `Profile`, not a property, which is why this can be a
    factory argument at all -- `test_gamelists_my_lists` sets it the same way."""
    return ProfileFactory(user_is_premium=premium)


def _contract(name, *, jobs=(), live=True):
    _SEQ['n'] += 1
    c = Contract.objects.create(name=name, slug=_slug(name), is_live=live,
                                igdb_id=900_000 + _SEQ['n'])
    if jobs:
        c.jobs.set(jobs)
    return c


def _member_game(contract):
    """A real member game: an ANCHORED concept whose TRUSTED match carries the contract's igdb id.

    That IS the membership rule -- there is no join table -- so a fixture that skips it produces a
    contract with no members, which every completion read would silently report as unfinished.
    """
    concept = ConceptFactory(anchor_migration_completed_at=timezone.now())
    IGDBMatchFactory(concept=concept, igdb_id=contract.igdb_id, status='accepted')
    return GameFactory(concept=concept)


def _mark_completed(profile, contract):
    """Completion as the ENGINE records it: an `EarnedContract` row exists."""
    return EarnedContract.objects.create(
        profile=profile, contract=contract, has_platinum=True,
        platinum_reached_at=timezone.now(),
    )


def _platted_at(profile, contract, when):
    """Completion as the TROPHY DATA records it, which is what the importer's date reads."""
    game = _member_game(contract)
    plat = TrophyFactory(game=game, trophy_type='platinum')
    EarnedTrophyFactory(profile=profile, trophy=plat, earned_date_time=when)
    _mark_completed(profile, contract)
    return game


def _joined(profile, when):
    """Move the account's creation date. `Profile` has none; `CustomUser.date_joined` is the anchor."""
    profile.user.date_joined = when
    profile.user.save(update_fields=['date_joined'])
    profile.refresh_from_db()
    return profile


@contextlib.contextmanager
def _refuses(fragment):
    """Assert a `ChallengeError` whose message a hunter could read, and which says the right thing."""
    with pytest.raises(svc.ChallengeError, match=fragment):
        yield


# ── gates ────────────────────────────────────────────────────────────────────────────────────────

def test_an_unlinked_hunter_cannot_start_a_run():
    """A challenge is built out of the hunter's own completions, so there is nothing to read."""
    profile = ProfileFactory(is_linked=False, user_is_premium=True)

    with _refuses('Link your PSN'):
        svc.start(profile, CHALLENGE_TYPE_AZ)


def test_during_the_beta_a_free_hunter_is_refused_with_a_reason():
    """NOT a redirect. When Challenges was parked the rule was that somebody who cannot use it yet gets
    told so, in place -- and the message has to say browsing is open, because it is."""
    profile = _member(premium=False)

    with _refuses('beta for members first'):
        svc.start(profile, CHALLENGE_TYPE_AZ)


def test_during_the_beta_a_member_may_start():
    assert svc.start(_member(), CHALLENGE_TYPE_AZ).pk is not None


@open_beta
def test_when_the_beta_ends_everyone_may_start():
    """The dial's whole point: ending the beta opens creation without taking anything back."""
    assert svc.start(_member(premium=False), CHALLENGE_TYPE_AZ).pk is not None


def test_the_gate_is_read_through_one_function():
    """`creation_is_open_to` is the single enforcement point, so a view can ASK before it offers a
    button rather than discovering the refusal by catching an exception."""
    assert svc.creation_is_open_to(_member()) is True
    assert svc.creation_is_open_to(_member(premium=False)) is False


def test_an_unknown_type_is_refused_before_anything_is_written():
    profile = _member()

    with _refuses('not a challenge type'):
        svc.start(profile, 'calendar')

    assert not Challenge.objects.filter(profile=profile).exists()


# ── starting, resuming, hiding ───────────────────────────────────────────────────────────────────

def test_an_az_run_gets_twenty_six_slots_keyed_by_letter_in_order():
    challenge = svc.start(_member(), CHALLENGE_TYPE_AZ)

    keys = list(challenge.slots.values_list('key', flat=True))
    assert keys == list(AZ_LETTERS)
    assert challenge.total_slots == 26
    assert list(challenge.slots.values_list('position', flat=True)) == list(range(26))


def test_a_jobs_run_gets_one_slot_per_job_in_careers_discipline_order():
    """Not the column's order. `discipline` holds slugs, so sorting on it gives combat, exploration,
    finesse, heart, mind while the canonical radar sequence puts mind and heart before finesse -- they
    agree for two disciplines and then diverge, which is the kind of wrong that reads as right."""
    challenge = svc.start(_member(), CHALLENGE_TYPE_JOBS)

    keys = list(challenge.slots.values_list('key', flat=True))
    assert len(keys) == Job.objects.count() == challenge.total_slots
    disciplines = [Job.objects.get(slug=k).discipline for k in keys]
    first_seen = list(dict.fromkeys(disciplines))
    assert first_seen == ['combat', 'exploration', 'mind', 'heart', 'finesse']


def test_freelancer_gets_a_slot_like_every_other_job():
    """Owner's call: it is a job like the others, and excluding it was what made its thin supply look
    like a problem in the first place."""
    challenge = svc.start(_member(), CHALLENGE_TYPE_JOBS)

    fallbacks = Job.objects.filter(is_fallback=True).values_list('slug', flat=True)
    assert set(fallbacks) <= set(challenge.slots.values_list('key', flat=True))


def test_starting_twice_returns_the_same_run():
    """Idempotent, so a double-submitted Start cannot race the one-active-run unique into a 500."""
    profile = _member()
    first = svc.start(profile, CHALLENGE_TYPE_AZ)

    assert svc.start(profile, CHALLENGE_TYPE_AZ).pk == first.pk
    assert Challenge.objects.filter(profile=profile).count() == 1


def test_hiding_then_starting_resumes_the_same_run_with_its_progress():
    """THE RULE, and the one a "simplification" would break silently. Hide is not delete: pressing Start
    again brings this run back, not a fresh one. A version that created a new run would still leave "a
    run exists" true afterwards while having thrown away every completed square."""
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    contract = _contract('Astro Bot')
    svc.assign(challenge, profile, 'A', contract)

    svc.hide(challenge, profile)
    resumed = svc.start(profile, CHALLENGE_TYPE_AZ)

    assert resumed.pk == challenge.pk
    assert resumed.is_deleted is False
    assert resumed.filled_count == 1
    assert Challenge.objects.filter(profile=profile).count() == 1


def test_hiding_is_idempotent():
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)

    svc.hide(challenge, profile)
    assert svc.hide(challenge, profile).is_deleted is True


def test_a_hidden_run_leaves_the_hub_but_keeps_its_rows():
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)

    svc.hide(challenge, profile)

    assert not Challenge.objects.visible().filter(pk=challenge.pk).exists()
    assert Challenge.objects.filter(pk=challenge.pk).exists()
    assert challenge.slots.count() == 26


def test_only_the_owner_may_hide_a_run():
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)

    with _refuses('not your challenge'):
        svc.hide(challenge, _member())


def test_the_first_run_is_named_for_its_type_and_later_ones_are_numbered():
    """Generated, never typed -- which is what keeps the whole feature free of a moderation surface."""
    profile = _member()
    first = svc.start(profile, CHALLENGE_TYPE_AZ)
    assert first.name == 'A-Z Challenge'

    Challenge.objects.filter(pk=first.pk).update(is_complete=True, completed_at=timezone.now())

    assert svc.start(profile, CHALLENGE_TYPE_AZ).name == 'A-Z Challenge (Run 2)'


def test_the_two_types_are_independent_runs():
    profile = _member()
    az = svc.start(profile, CHALLENGE_TYPE_AZ)
    jobs = svc.start(profile, CHALLENGE_TYPE_JOBS)

    assert az.pk != jobs.pk


# ── assigning ────────────────────────────────────────────────────────────────────────────────────

def test_assigning_snapshots_the_contract_rather_than_only_linking_it():
    """The snapshot is the point: `reconcile_contracts` and staff re-anchoring both move the catalogue
    under a finished run, so the slot has to hold what it was told at the time."""
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    contract = _contract('Bloodborne')

    slot = svc.assign(challenge, profile, 'B', contract)

    assert slot.contract_id == contract.id
    assert slot.contract_slug == contract.slug
    assert slot.contract_name == 'Bloodborne'
    assert slot.assigned_at is not None
    assert slot.is_completed is False


def test_assigning_moves_the_filled_counter():
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)

    svc.assign(challenge, profile, 'B', _contract('Bloodborne'))
    challenge.refresh_from_db()

    assert challenge.filled_count == 1
    assert challenge.completed_count == 0


def test_an_az_slot_refuses_a_game_that_does_not_start_with_its_letter():
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)

    with _refuses('does not start with'):
        svc.assign(challenge, profile, 'B', _contract('Astro Bot'))


def test_a_job_slot_refuses_a_game_that_does_not_cover_that_job():
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_JOBS)
    mage, champion = Job.objects.all()[:2]

    with _refuses('does not cover that job'):
        svc.assign(challenge, profile, champion.slug, _contract('Some RPG', jobs=[mage]))


def test_a_job_slot_accepts_a_game_that_covers_it():
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_JOBS)
    mage = Job.objects.first()

    slot = svc.assign(challenge, profile, mage.slug, _contract('Some RPG', jobs=[mage]))

    assert slot.contract_name == 'Some RPG'


def test_one_game_cannot_fill_two_squares_of_the_same_run():
    """A contract carries up to six jobs, so without this one completion fills six squares and pays six
    times. Refused in the service with a message, not left to the database constraint's bare error."""
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_JOBS)
    jobs = list(Job.objects.all()[:2])
    contract = _contract('Some RPG', jobs=jobs)
    svc.assign(challenge, profile, jobs[0].slug, contract)

    with _refuses('already in another square'):
        svc.assign(challenge, profile, jobs[1].slug, contract)


def test_a_draft_contract_cannot_be_assigned():
    """`is_live=False` is how staff stage a contract before publishing it; it is not on the board yet."""
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)

    with _refuses('not on the Job Board'):
        svc.assign(challenge, profile, 'B', _contract('Bloodborne', live=False))


def test_reassigning_an_unfinished_square_overwrites_it_in_place():
    """DIRECTLY over a filled square, with no clear in between -- an earlier version of this test
    cleared first and so never exercised the path it was named for.

    Two things have to hold at once: the old slug is freed by the same row's UPDATE, so
    `challengeslot_unique_contract` is satisfied without a separate delete, and `filled_count` must not
    move -- one square held a game before and holds one after."""
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    svc.assign(challenge, profile, 'B', _contract('Bloodborne'))

    slot = svc.assign(challenge, profile, 'B', _contract('Balatro'))
    challenge.refresh_from_db()

    assert slot.contract_name == 'Balatro'
    assert challenge.filled_count == 1


def test_only_the_owner_may_assign():
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)

    with _refuses('not your challenge'):
        svc.assign(challenge, _member(), 'B', _contract('Bloodborne'))


def test_a_finished_run_refuses_further_writes():
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    Challenge.objects.filter(pk=challenge.pk).update(is_complete=True, completed_at=timezone.now())
    challenge.refresh_from_db()

    with _refuses('challenge is finished'):
        svc.assign(challenge, profile, 'B', _contract('Bloodborne'))


# ── the completed-contract branch: importer and hatch ────────────────────────────────────────────

def test_a_game_you_have_already_finished_is_normally_refused():
    """It would land the square complete the instant it was assigned, and the run would finish itself."""
    profile = _member()
    _joined(profile, timezone.now())
    challenge = svc.start(profile, CHALLENGE_TYPE_JOBS)
    job = Job.objects.first()
    # Enough untouched supply that the hatch stays shut.
    others = [_contract(f'Filler {i}', jobs=[job]) for i in range(HATCH_THRESHOLD + 2)]
    assert others
    contract = _contract('Done Already', jobs=[job])
    _mark_completed(profile, contract)

    with _refuses('already finished that one'):
        svc.assign(challenge, profile, job.slug, contract)


def test_the_importer_completes_a_square_from_a_finish_earned_after_you_joined():
    """His idea, and a better anchor than anything the contract engine records: `*_reached_at` is a
    DETECTION stamp, so it would credit a hunter for a contract published after they finished it."""
    profile = _member()
    _joined(profile, timezone.now() - timezone.timedelta(days=365))
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    contract = _contract('Astro Bot')
    _platted_at(profile, contract, timezone.now() - timezone.timedelta(days=30))

    slot = svc.assign(challenge, profile, 'A', contract)

    assert slot.is_completed
    assert slot.completed_via == COMPLETED_VIA_IMPORT
    challenge.refresh_from_db()
    assert challenge.completed_count == 1


def test_the_importer_refuses_a_finish_earned_before_you_joined():
    """The fairness line. A platinum from before the account existed is not a run this hunter was ever
    working on here, and that is the whole argument for allowing the importer at all."""
    profile = _member()
    _joined(profile, timezone.now() - timezone.timedelta(days=30))
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    contract = _contract('Astro Bot')
    _platted_at(profile, contract, timezone.now() - timezone.timedelta(days=365))
    # Keep the hatch shut so the refusal can only come from the date.
    for i in range(HATCH_THRESHOLD + 2):
        _contract(f'Another A Game {i}')

    with _refuses('already finished that one'):
        svc.assign(challenge, profile, 'A', contract)


def test_the_importer_closes_once_you_have_completed_a_run():
    """"Their first completion, not any subsequent ones" -- read as *has never completed one*, which is
    generous about abandoned runs and permanent once a run is finished."""
    profile = _member()
    _joined(profile, timezone.now() - timezone.timedelta(days=365))
    first = svc.start(profile, CHALLENGE_TYPE_AZ)
    Challenge.objects.filter(pk=first.pk).update(is_complete=True, completed_at=timezone.now())

    assert svc.importer_is_available(profile, CHALLENGE_TYPE_AZ) is False

    second = svc.start(profile, CHALLENGE_TYPE_AZ)
    contract = _contract('Astro Bot')
    _platted_at(profile, contract, timezone.now() - timezone.timedelta(days=30))
    for i in range(HATCH_THRESHOLD + 2):
        _contract(f'Another A Game {i}')

    with _refuses('already finished that one'):
        svc.assign(second, profile, 'A', contract)


def test_the_hatch_opens_a_finished_game_when_supply_is_too_thin():
    """The hatch exists because global supply is the wrong measure: eligibility is per hunter, and a
    veteran can have nothing left under a thin letter. Our curation gap, so it pays in full."""
    profile = _member()
    # Importer closed, so only the hatch can explain the fill.
    done = svc.start(profile, CHALLENGE_TYPE_AZ)
    Challenge.objects.filter(pk=done.pk).update(is_complete=True, completed_at=timezone.now())
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)

    contract = _contract('Quest for Glory')
    _mark_completed(profile, contract)

    slot = svc.assign(challenge, profile, 'Q', contract)

    assert slot.is_completed
    assert slot.completed_via == COMPLETED_VIA_HATCH


def test_the_importer_wins_when_both_rules_apply():
    """`import` is the more specific rule and the one with a date behind it, so it is the more honest
    label for what happened."""
    profile = _member()
    _joined(profile, timezone.now() - timezone.timedelta(days=365))
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    contract = _contract('Quest for Glory')
    _platted_at(profile, contract, timezone.now() - timezone.timedelta(days=30))

    slot = svc.assign(challenge, profile, 'Q', contract)

    assert slot.completed_via == COMPLETED_VIA_IMPORT


# ── clearing ─────────────────────────────────────────────────────────────────────────────────────

def test_clearing_an_unfinished_square_empties_it_and_moves_the_counter():
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    svc.assign(challenge, profile, 'B', _contract('Bloodborne'))

    slot = svc.clear(challenge, profile, 'B')
    challenge.refresh_from_db()

    assert slot.is_filled is False
    assert slot.contract_id is None
    assert slot.contract_name == ''
    assert slot.assigned_at is None
    assert challenge.filled_count == 0


def test_a_finished_square_can_never_be_cleared():
    """LOAD-BEARING FOR THE ECONOMY, not tidiness. The job-XP guard is keyed on the slot, so clearing a
    paid square and refilling it is exactly how one job would be paid twice in one run. "One payout per
    job per challenge" holds because this refuses."""
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    contract = _contract('Astro Bot')
    svc.assign(challenge, profile, 'A', contract)
    svc.mark_slot_completed(challenge.slots.get(key='A'))

    with _refuses('stay as they are'):
        svc.clear(challenge, profile, 'A')


def test_clearing_an_empty_square_is_not_an_error():
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)

    assert svc.clear(challenge, profile, 'B').is_filled is False


def test_clearing_a_square_that_is_not_on_this_run_is_refused():
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)

    with _refuses('not a slot on this challenge'):
        svc.clear(challenge, profile, 'not-a-letter')


# ── completion ───────────────────────────────────────────────────────────────────────────────────

def test_completing_a_square_stamps_it_and_moves_the_counter():
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    svc.assign(challenge, profile, 'A', _contract('Astro Bot'))

    assert svc.mark_slot_completed(challenge.slots.get(key='A')) is True
    challenge.refresh_from_db()

    slot = challenge.slots.get(key='A')
    assert slot.is_completed and slot.completed_at is not None
    assert slot.completed_via == COMPLETED_VIA_LIVE
    assert challenge.completed_count == 1


def test_completing_a_square_twice_changes_nothing():
    """Idempotent because two detectors race by design: the sync hook fires on the hunter's own sync and
    the nightly sweep fires for everyone, and both are supposed to be safe."""
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    svc.assign(challenge, profile, 'A', _contract('Astro Bot'))
    slot = challenge.slots.get(key='A')
    svc.mark_slot_completed(slot)
    first_at = challenge.slots.get(key='A').completed_at

    assert svc.mark_slot_completed(challenge.slots.get(key='A')) is False
    assert challenge.slots.get(key='A').completed_at == first_at


def test_an_empty_square_cannot_be_completed():
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)

    assert svc.mark_slot_completed(challenge.slots.get(key='A')) is False


def test_the_last_square_finishes_the_run():
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_JOBS)
    for job in Job.objects.all():
        svc.assign(challenge, profile, job.slug, _contract(f'Game for {job.slug}', jobs=[job]))
    slots = list(challenge.slots.all())
    for slot in slots[:-1]:
        svc.mark_slot_completed(slot)

    challenge.refresh_from_db()
    assert challenge.is_complete is False, 'a run finished one square early'

    svc.mark_slot_completed(slots[-1])
    challenge.refresh_from_db()

    assert challenge.is_complete is True
    assert challenge.completed_at is not None
    assert challenge.completed_count == challenge.total_slots


def test_finishing_a_run_frees_the_type_for_the_next_one():
    """Sequential runs, end to end: the reward for finishing an A-Z is being allowed to start another."""
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    for letter in AZ_LETTERS:
        svc.assign(challenge, profile, letter, _contract(f'{letter}ame'))
    for slot in list(challenge.slots.all()):
        svc.mark_slot_completed(slot)

    second = svc.start(profile, CHALLENGE_TYPE_AZ)

    assert second.pk != challenge.pk
    assert second.name == 'A-Z Challenge (Run 2)'


# ── eligibility ──────────────────────────────────────────────────────────────────────────────────

def test_the_pool_excludes_a_game_used_in_another_square():
    """A JOBS run, because that is the only place this rule can bite: an A-Z contract fits exactly one
    letter, so it can never turn up in another square. A contract carrying two jobs can, and without
    this one completion would fill both and pay twice."""
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_JOBS)
    first, second = list(Job.objects.all()[:2])
    used = _contract('Two Jobs', jobs=[first, second])
    spare = _contract('Also Two', jobs=[first, second])
    svc.assign(challenge, profile, first.slug, used)

    pool = set(eligibility.eligible_contracts(profile, challenge, second.slug)
               .values_list('id', flat=True))

    assert spare.id in pool
    assert used.id not in pool


def test_a_squares_own_occupant_stays_in_its_own_pool():
    """THE OFF-BY-ONE THAT HANDED OUT FREE SQUARES. A slot's pool has to be the same size whether the
    slot is empty or being reassigned, because `hatch_is_open` counts it. An earlier version excluded
    every assigned contract including the slot's own, so a reassignment saw one contract fewer -- and at
    the boundary that opened the hatch a contract early, handing the hunter a permanently locked,
    XP-bearing square the rule did not allow."""
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    held = _contract('Astro Bot')
    before = eligibility.eligible_contracts(profile, challenge, 'A').count()

    svc.assign(challenge, profile, 'A', held)

    after = eligibility.eligible_contracts(profile, challenge, 'A')
    assert after.count() == before
    assert held.id in set(after.values_list('id', flat=True))


def test_the_pool_excludes_games_this_hunter_has_finished():
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    done = _contract('Astro Bot')
    _mark_completed(profile, done)
    spare = _contract('Alan Wake')

    pool = set(eligibility.eligible_contracts(profile, challenge, 'A').values_list('id', flat=True))

    assert spare.id in pool
    assert done.id not in pool


def test_the_pool_is_per_hunter():
    """Somebody else finishing a game must not remove it from your pool. Obvious, and exactly the kind
    of thing a correlated subquery gets wrong by dropping the profile filter."""
    profile, other = _member(), _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    contract = _contract('Astro Bot')
    _mark_completed(other, contract)

    pool = eligibility.eligible_contracts(profile, challenge, 'A').values_list('id', flat=True)

    assert contract.id in set(pool)


def test_a_game_whose_name_starts_with_a_digit_reaches_no_letter_slot():
    """Twelve such contracts are live. Not a special case in the code -- a consequence of prefix
    matching -- but worth pinning so somebody auditing the pool is not surprised by it."""
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    digits = _contract('7 Days to Die')

    for letter in AZ_LETTERS:
        pool = eligibility.eligible_contracts(profile, challenge, letter)
        assert digits.id not in set(pool.values_list('id', flat=True))


def test_the_letter_match_is_case_insensitive():
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    lower = _contract('inside')

    pool = eligibility.eligible_contracts(profile, challenge, 'I').values_list('id', flat=True)

    assert lower.id in set(pool)


def test_the_hatch_is_shut_above_the_threshold_and_open_at_it():
    """The boundary, both sides. Off-by-one here either strands a hunter or hands out the exception to
    everybody."""
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    made = [_contract(f'Quest {i}') for i in range(HATCH_THRESHOLD + 1)]
    assert len(made) == HATCH_THRESHOLD + 1

    assert eligibility.hatch_is_open(profile, challenge, 'Q') is False

    _mark_completed(profile, made[0])   # one fewer available -> exactly at the threshold

    assert eligibility.hatch_is_open(profile, challenge, 'Q') is True


def test_the_completion_date_is_the_earliest_qualifying_moment():
    """STRICT on purpose. A hunter who platted in 2019 and cleaned up DLC in 2024 completed the game in
    2019, and taking the later date would let one recent trophy re-date a pre-join platinum into
    importable territory."""
    profile = _member()
    contract = _contract('Astro Bot')
    old = timezone.now() - timezone.timedelta(days=900)
    game = _platted_at(profile, contract, old)
    ProfileGameFactory(profile=profile, game=game, progress=100,
                       most_recent_trophy_date=timezone.now())

    dates = eligibility.completion_dates(profile, [contract])

    assert dates[contract.id] == old


def test_a_game_you_have_not_finished_has_no_completion_date():
    profile = _member()
    contract = _contract('Astro Bot')
    _member_game(contract)

    assert eligibility.completion_dates(profile, [contract]) == {}


def test_importability_turns_on_the_join_date():
    profile = _member()
    joined = timezone.now() - timezone.timedelta(days=100)
    _joined(profile, joined)
    after = _contract('After Joining')
    before = _contract('Before Joining')
    _platted_at(profile, after, joined + timezone.timedelta(days=1))
    _platted_at(profile, before, joined - timezone.timedelta(days=1))

    importable = eligibility.importable_ids(profile, [after, before], joined)

    assert importable == {after.id}


# ── the gate and the pool must agree (added after the chunk-1 audit) ─────────────────────────────

def test_the_assignment_gate_accepts_a_lowercase_name():
    """The Python-side gate used to spell the A-Z rule itself, and case-folded differently from the
    queryset. Now it delegates, so this exercises the same predicate the pool uses."""
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)

    slot = svc.assign(challenge, profile, 'I', _contract('inside'))

    assert slot.contract_name == 'inside'


def test_the_gate_and_the_pool_agree_about_every_awkward_name():
    """THE DIVERGENCE, pinned against the REAL gate -- i.e. `svc.assign`, not a second queryset.

    The first version of this test compared `eligible_contracts` with `eligibility.fits_slot`. Both are
    querysets running the same `name__istartswith`, so they agreed trivially and the test caught
    nothing: reverting the gate to its Python spelling left it green. Which makes it a fair example of
    the thing the audit it was written for was about.

    Python's `upper()` does full Unicode case mapping (one character to many) while Postgres folds with
    `lower()` (one to one), so these three names disagree in both directions. A name the pool offers and
    the gate refuses is a dead-end click in the picker; a name the gate accepts and the pool never holds
    is invisible to `hatch_is_open`'s count, which is the quieter and worse half.

    One profile per name, because a hunter has one active run per type and `assign` is the only honest
    way to ask the gate.
    """
    for name, key in (('\u0130stanbul Tale', 'I'), ('\ufb01nal Cut', 'F'), ('\u00dfeta Test', 'S')):
        profile = _member()
        challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
        contract = _contract(name)

        in_pool = contract.id in set(
            eligibility.eligible_contracts(profile, challenge, key).values_list('id', flat=True))
        try:
            svc.assign(challenge, profile, key, contract)
            gate_accepted = True
        except svc.ChallengeError:
            gate_accepted = False

        assert in_pool == gate_accepted, (
            f'{name!r} under {key}: the pool says {in_pool} and the gate says {gate_accepted}'
        )


# ── hidden runs, and the boundary cases the audit found unpinned ─────────────────────────────────

def test_a_hidden_run_refuses_writes_until_it_is_started_again():
    """The precondition is re-asserted INSIDE the lock now, not read off a stale object. Hiding takes a
    run out of view, and Start is the way back."""
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    svc.hide(challenge, profile)
    challenge.refresh_from_db()

    with _refuses('is hidden'):
        svc.assign(challenge, profile, 'A', _contract('Astro Bot'))

    resumed = svc.start(profile, CHALLENGE_TYPE_AZ)
    assert svc.assign(resumed, profile, 'A', _contract('Alan Wake')).is_filled


def test_a_finished_run_refuses_clearing_too():
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    svc.assign(challenge, profile, 'A', _contract('Astro Bot'))
    Challenge.objects.filter(pk=challenge.pk).update(is_complete=True, completed_at=timezone.now())
    challenge.refresh_from_db()

    with _refuses('challenge is finished'):
        svc.clear(challenge, profile, 'A')


def test_hiding_a_finished_run_is_allowed():
    """`hide` deliberately does not go through `_lock_challenge`, which refuses a finished run -- a
    hunter must be able to take a completed run off their profile."""
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    Challenge.objects.filter(pk=challenge.pk).update(is_complete=True, completed_at=timezone.now())
    challenge.refresh_from_db()

    assert svc.hide(challenge, profile).is_deleted is True


def test_a_completion_exactly_at_the_join_moment_is_not_importable():
    """The equality boundary. `importable_ids` compares STRICTLY, and without this test flipping `>` to
    `>=` breaks nothing -- the other importer tests are a day either side."""
    profile = _member()
    joined = timezone.now() - timezone.timedelta(days=100)
    _joined(profile, joined)
    contract = _contract('Exactly Then')
    _platted_at(profile, contract, joined)

    assert eligibility.importable_ids(profile, [contract], joined) == set()


def test_completion_dates_ignores_another_hunters_trophies():
    """The aggregate is filtered by profile as well as by concept. Dropping the profile filter would make
    every contract somebody else finished look importable."""
    profile, other = _member(), _member()
    contract = _contract('Astro Bot')
    _platted_at(other, contract, timezone.now() - timezone.timedelta(days=10))

    assert eligibility.completion_dates(profile, [contract]) == {}


def test_a_jobs_run_is_refused_when_the_catalogue_is_empty(monkeypatch):
    """The only guard against a zero-slot run, which the DB would otherwise refuse as a bare
    `challenge_total_slots_positive` IntegrityError rather than a message."""
    monkeypatch.setattr(svc, 'slot_keys_for', lambda challenge_type: [])
    profile = _member()

    with _refuses('not available right now'):
        svc.start(profile, CHALLENGE_TYPE_JOBS)


def test_completing_a_square_the_hunter_just_emptied_is_refused():
    """THE STALE-PIVOT RACE, made deterministic.

    A detector reads a filled, uncompleted square; the hunter clears it; the detector then stamps it.
    Because `clear` and `mark_slot_completed` take the same locks in the same order, `clear` reliably
    commits FIRST, so this is the ordinary outcome rather than a narrow window. Passing the stale object
    reproduces it without threads.

    Re-asserting only `is_completed` on the locked row -- which an earlier version did -- lets the write
    through and violates `challengeslot_completed_is_filled_dated_and_explained`: a completed square with
    no game in it, surfacing as an IntegrityError out of a background sweep rather than as a refusal.
    """
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    svc.assign(challenge, profile, 'A', _contract('Astro Bot'))

    stale = challenge.slots.get(key='A')          # what a detector would be holding
    assert stale.is_filled                        # ... and it looked fillable when read
    svc.clear(challenge, profile, 'A')            # the hunter empties it underneath

    assert svc.mark_slot_completed(stale) is False

    challenge.refresh_from_db()
    assert challenge.completed_count == 0
    assert challenge.slots.get(key='A').is_completed is False
