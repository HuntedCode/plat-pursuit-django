"""`challenge_service` -- the only thing that writes a challenge.

What is worth pinning here is not that the functions run, but the RULES they encode, several of which
are the product of a decision that could have gone the other way and would look equally reasonable in
code. Those are the ones a future reader will be tempted to "simplify":

- **`start` resumes before it creates.** Hiding a run is not deleting it, so pressing Start again brings
  the same run back with its progress. A version that created a fresh run would pass any test that only
  checked "a run exists afterwards", and would silently throw away completed squares.
- **A finished square never clears.** This is load-bearing for the XP economy, not tidiness: the job-XP
  guard is keyed on the slot, so clearing a paid square and refilling it is exactly how one job gets
  paid twice in one run (latent: `xp_redeemed_at` gains its writer with the rewards chunk).
- **An already-completed contract is refused, unless the hatch (either type) or the A-Z-only importer lifts it** -- and the two
  are labelled differently on purpose, because `import` carries a fairness date and `hatch` carries an
  admission that our supply failed the hunter.
- **The beta gate refuses with a message rather than redirecting.** When Challenges was parked the
  decision was that a free hunter gets told so on a page; `PremiumRequiredMixin` would bounce them.

The Job catalogue comes from the migration seed (`conftest.py` repairs it per session), so `jobs` runs
depend on it being non-empty -- `slot_keys_for` reads it and the count becomes `total_slots`.
"""
import contextlib

import pytest
from django.db import connection
from django.db.models import Q
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from challenges.models import (
    AZ_LETTERS,
    CHALLENGE_TYPE_AZ,
    CHALLENGE_TYPE_CALENDAR,
    CHALLENGE_TYPE_JOBS,
    CHALLENGE_TYPES,
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
    """Assert a REFUSAL: a `ChallengeError` that is not a confirmation stop, saying the right thing.

    THE TYPE CHECK IS THE POINT, and without it this helper could pass on the opposite outcome.
    `ConfirmationRequired` SUBCLASSES `ChallengeError` (deliberately -- a caller that only knows the base
    class then declines to write and shows the message), and the two messages for an already-finished game
    both open with "You have already finished that one". So `pytest.raises(ChallengeError, match=...)` was
    satisfied either by "pick a game you can still complete" (a refusal) or by "confirm to use it" (a stop
    that a second call turns into a WRITE).

    That is not a hypothetical: a test written to prove the importer is closed on a Job Coverage run passed
    with the gate deleted, because the importer opening turned the refusal into a confirmation whose message
    shared the fragment being matched. Assert the outcome, not a substring of the prose describing it.
    """
    with pytest.raises(svc.ChallengeError) as caught:
        yield
    assert not isinstance(caught.value, svc.ConfirmationRequired), (
        'this is a confirmation STOP, not a refusal -- confirming it would write. Assert '
        '`pytest.raises(svc.ConfirmationRequired)` directly, as the two stop tests do.'
    )
    assert fragment in str(caught.value), '%r not in %r' % (fragment, str(caught.value))



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
    """`genre`, NOT `calendar`, and the swap is the point.

    This test used to pass `'calendar'` as its unknown type, written while the plan said the Platinum
    Calendar would not return. When it did return (owner, 2026-10-02) `calendar` became a REAL type and
    this test began asserting that a valid type is refused -- it failed loudly, which is the good
    outcome, but it is worth choosing an example that cannot be promoted later. `genre` is the retired
    challenge type: gone, not coming back, and the likeliest thing an old caller would actually send.
    """
    profile = _member()

    with _refuses('not a challenge type'):
        svc.start(profile, 'genre')

    assert not Challenge.objects.filter(profile=profile).exists()


def test_the_calendar_cannot_be_started_until_its_rules_exist():
    """THE DOOR IS SHUT WHILE THE MECHANICS ARE MISSING, and it was open for one commit.

    Adding `calendar` to `CHALLENGE_TYPE_CHOICES` was enough to ship a working Start button, because My
    Challenges builds its cards by iterating that list and renders a POST per card. Pressing it created a
    run with no fill logic and no completion path -- which, given one-active-run-per-type and no delete,
    the hunter could never replace, and which listed itself publicly as `0/365` while describing itself
    with the Job Coverage copy.

    THE GATE IS IN THE SERVICE, not the template, which is what this asserts. The card is hidden too, but
    a hidden button is not a closed door: `challenge_start` is a URL.
    """
    profile = _member()

    with _refuses('not ready yet'):
        svc.start(profile, CHALLENGE_TYPE_CALENDAR)

    assert not Challenge.objects.filter(profile=profile).exists()

    # AND IT IS STILL A VALID TYPE for everything that only READS. The two questions are different: a
    # type can be real enough to render while its rules are being built.
    assert CHALLENGE_TYPE_CALENDAR in CHALLENGE_TYPES


def test_a_calendar_run_gets_365_days_and_no_slots(monkeypatch):
    """THE CALENDAR'S ROWS ARE `CalendarDay`, NOT `ChallengeSlot`, and both halves are asserted.

    A day's atom is a DATE, so a slot's contract FK, frozen snapshot, contract-uniqueness constraint and
    `xp_redeemed_at` are all dead weight for it. The run must therefore build days and build NO slots.

    WHY THE "no slots" HALF EXISTS. `slot_keys_for` used to end in a bare `else` returning the job
    catalogue, so the moment `calendar` joined `CHALLENGE_TYPE_CHOICES` -- which is also what let it past
    the `CHALLENGE_TYPES` check -- a Calendar run would have been created with 25 JOB slots and
    `total_slots=25`. No exception and no empty list: just a silently wrong run. Asserting the count
    alone would not have caught it either, since 25 is a plausible-looking number.

    365, NOT 366: a run is keyed on (month, day) across all years, so 29 February belongs to no year.

    THE CREATION GATE IS LIFTED FOR THIS TEST, deliberately rather than by calling an internal: the
    shape is what is being pinned, and it must keep being pinned on the day the gate is removed. Patching
    the constant means that day needs no edit here. `test_the_calendar_cannot_be_started_until_its_rules_exist`
    owns the gate itself.
    """
    monkeypatch.setattr(svc, 'TYPES_NOT_YET_CREATABLE', frozenset())
    challenge = svc.start(_member(), CHALLENGE_TYPE_CALENDAR)

    days = list(challenge.calendar_days.values_list('month', 'day'))
    assert len(days) == 365, 'a calendar run must be 365 days, not %d' % len(days)
    assert challenge.total_slots == 365, (
        '`total_slots` disagrees with the rows created: %d' % challenge.total_slots)
    assert not challenge.slots.exists(), (
        'a calendar run built ChallengeSlots -- it is the one type whose atom is not a contract')

    # ORDERED, AND FEBRUARY IS 28. The keys come from `calendar_day_keys()`, so this pins the shape that
    # function produces rather than re-deriving it.
    assert days == sorted(days), 'the days are not in calendar order'
    # PER-MONTH LENGTHS, not just the total. A typo that OFFSETS two months (April 31, June 29) keeps
    # 365 rows, stays sorted and keeps February at 28 -- so the count alone would pass it.
    from challenges.models import CALENDAR_MONTH_DAYS
    for month, expected in enumerate(CALENDAR_MONTH_DAYS, start=1):
        got = len([d for m, d in days if m == month])
        assert got == expected, 'month %d has %d days, expected %d' % (month, got, expected)
    assert len(set(days)) == 365, 'a day is duplicated, so some other day is missing'
    assert (2, 29) not in days, '29 February belongs to no year in a month/day calendar'
    assert (2, 28) in days and (12, 31) in days, 'the calendar is missing its boundaries'
    assert len([d for m, d in days if m == 2]) == 28, 'February is not 28 days'

    # NOTHING IS FILLED AT CREATION. The backfill is a separate step, and a day that arrives already
    # true would make the opening ceremony's numbers meaningless.
    assert not challenge.calendar_days.filter(
        Q(in_all=True) | Q(in_clean=True)).exists()
    assert not challenge.calendar_days.exclude(earned_on=None, filled_at=None).exists(), (
        'a day arrived with a fill timestamp but no fill')


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

    Two things have to hold at once: the old FK is freed by the same row's UPDATE, so
    `challengeslot_unique_contract` is satisfied without a separate delete, and `filled_count` must not
    move -- one square held a game before and holds one after. (It said "the old slug" while the constraint
    keyed on the snapshot; the key is the `contract` FK now.)"""
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

    slot = svc.assign(challenge, profile, 'A', contract, acknowledge_lock=True)

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

    slot = svc.assign(challenge, profile, 'Q', contract, acknowledge_lock=True)

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

    slot = svc.assign(challenge, profile, 'Q', contract, acknowledge_lock=True)

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

    importable = set(eligibility.importable_dates(profile, [after, before], joined))

    assert importable == {after.id}


# ── the gate and the pool must agree (added after the chunk-1 audit) ─────────────────────────────

def test_the_assignment_gate_accepts_a_lowercase_name():
    """Plain ASCII case-insensitivity, which is all this covers.

    It does NOT cover the Unicode divergence, and an earlier docstring implied it did: `'inside'.upper()`
    starts with 'I' in Python too, so reverting the gate to its Python spelling leaves this green. The
    mutation it does catch is `istartswith` becoming `startswith` in `_shape`."""
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
    for name, key in (('\ufb01nal Cut', 'F'), ('\u00dfeta Test', 'S')):
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

        # ABSOLUTE as well as equal. Agreement alone is satisfied by both sides being wrong together,
        # and these two names must be REFUSED: libc's per-character `upper()` does not expand them, so
        # the pool cannot hold them and the gate must not take them.
        assert in_pool is False, f'{name!r} unexpectedly in the pool under {key}'
        assert gate_accepted is False, f'{name!r} accepted under {key} but the pool never held it'


# ── hidden runs, and the boundary cases the audit found unpinned ─────────────────────────────────

def test_a_hidden_run_refuses_writes_until_it_is_started_again():
    """The hidden rule lives in `_lock_challenge` and nowhere else, so this reaches it.

    An earlier version matched the fragment 'is hidden', which BOTH `_require_owner` and
    `_lock_challenge` produced -- so deleting either check left the test green and it pinned only
    "something, somewhere refuses a hidden run". `_require_owner` no longer mentions hiding at all, and
    the fragment here is unique to the surviving message.
    """
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    svc.hide(challenge, profile)
    challenge.refresh_from_db()

    with _refuses('Start it again'):
        svc.assign(challenge, profile, 'A', _contract('Astro Bot'))

    resumed = svc.start(profile, CHALLENGE_TYPE_AZ)
    assert resumed.pk == challenge.pk, 'Start created a new run instead of resuming the hidden one'
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
    """The equality boundary. `importable_dates` compares STRICTLY, and without this test flipping `>` to
    `>=` breaks nothing -- the other importer tests are a day either side."""
    profile = _member()
    joined = timezone.now() - timezone.timedelta(days=100)
    _joined(profile, joined)
    contract = _contract('Exactly Then')
    _platted_at(profile, contract, joined)

    assert set(eligibility.importable_dates(profile, [contract], joined)) == set()


def test_completion_dates_ignores_another_hunters_trophies():
    """The aggregate is filtered by profile as well as by concept. Dropping the profile filter would make
    every contract somebody else finished look importable."""
    profile, other = _member(), _member()
    contract = _contract('Astro Bot')
    game = _platted_at(other, contract, timezone.now() - timezone.timedelta(days=10))
    # BOTH aggregates, not just the platinum one: an earlier version created no `ProfileGame`, so the
    # 100% half was empty either way and dropping its profile filter would have gone unnoticed.
    ProfileGameFactory(profile=other, game=game, progress=100,
                       most_recent_trophy_date=timezone.now())

    assert eligibility.completion_dates(profile, [contract]) == {}


def test_a_jobs_run_is_refused_when_the_catalogue_is_empty(monkeypatch):
    """The only guard against a zero-slot run, which the DB would otherwise refuse as a bare
    `challenge_total_slots_positive` IntegrityError rather than a message.

    PATCHES `slot_keys_for` rather than emptying the Job table, which is the pragmatic call -- the
    catalogue is seeded per session by `conftest.py` and truncating it would break every other jobs
    test in the file. So this pins the GUARD, not the claim that an empty catalogue produces an empty
    key list."""
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


# ── fitting_keys: the contract-first question, and its agreement with the slot-first one ──────────

@pytest.mark.parametrize('name, expected', [
    ('Bloodborne', {'B'}),
    ('astro bot', {'A'}),                  # uppercased by Postgres, not by the caller
    ('   Spaced', set()),                  # a leading space is not a letter, so it fits nothing
    ('2064: Read Only Memories', set()),   # digit-initial: 12 such contracts are live and unusable
])
def test_an_az_contract_fits_exactly_the_letter_it_starts_with(name, expected):
    challenge = svc.start(_member(), CHALLENGE_TYPE_AZ)

    assert eligibility.fitting_keys(challenge, _contract(name)) == expected


def test_a_non_ascii_initial_fits_nothing_without_needing_a_branch():
    """The live example is a macron O (Okami). It uppercases to a character no A-Z run has a slot for, so
    it drops out of the set -- the same way it drops out of `_shape`'s prefix match, with no special case
    in either place."""
    challenge = svc.start(_member(), CHALLENGE_TYPE_AZ)

    assert eligibility.fitting_keys(challenge, _contract('\u014ckami')) == set()


def test_a_jobs_contract_fits_every_job_it_carries():
    challenge = svc.start(_member(), CHALLENGE_TYPE_JOBS)
    jobs = list(Job.objects.order_by('slug')[:3])

    keys = eligibility.fitting_keys(challenge, _contract('Astro Bot', jobs=jobs))

    assert keys == {j.slug for j in jobs}


def test_a_dead_contract_fits_nothing_on_either_type():
    """`_shape` filters `is_live=True`, so an unpublished contract is in no pool. The picker must agree, or
    it offers a game that `assign` then refuses with "not on the Job Board"."""
    profile = _member()
    az = svc.start(profile, CHALLENGE_TYPE_AZ)
    jobs_run = svc.start(profile, CHALLENGE_TYPE_JOBS)
    dead = _contract('Bloodborne', jobs=[Job.objects.first()], live=False)

    assert eligibility.fitting_keys(az, dead) == set()
    assert eligibility.fitting_keys(jobs_run, dead) == set()


#: Names whose first character stresses case folding, because that is where the two query shapes could
#: come apart. Measured against this Postgres: the long s and dotless i fold to S and I in both Python
#: and Postgres; the fi/fl/st ligatures and sharp s expand to two letters in Python and are left alone by
#: Postgres, so both say "not a single A-Z letter". None of them diverges TODAY -- they are here so that
#: the day one does, this test says so.
FOLD_CASES = [
    'final',                      # the control
    '\ufb01nal',                  # fi ligature: Python -> 'FI', Postgres -> unchanged
    '\ufb02ame',                  # fl ligature
    '\ufb06tone',                 # st ligature
    '\u00dfeta',                  # sharp s: Python -> 'SS'
    '\u017fword',                 # long s: folds to S in BOTH
    '\u0131sland',                # dotless i: folds to I in BOTH
    '\u014ckami',                 # macron O: a real live catalogue row
    '\u212atest',                 # Kelvin sign: uppercase already, and not ASCII
    '2064: Read Only Memories',   # digit initial
    '  leading space',
    'astro bot',                  # plain lowercase
]


@pytest.mark.parametrize('challenge_type', [CHALLENGE_TYPE_AZ, CHALLENGE_TYPE_JOBS])
def test_fitting_keys_agrees_with_fits_slot_on_every_key(challenge_type):
    """THE PROPERTY THAT KEEPS THE TWO QUERY SHAPES FROM DRIFTING.

    `fits_slot` answers "does this contract fit key K?"; `fitting_keys` answers "which keys does it fit?".
    Two questions, one rule -- which holds only while they agree.

    WHAT THIS TEST DOES AND DOES NOT CATCH, stated because a mutation run made the difference concrete.
    Replacing the DB-side fold with `contract.name[:1].upper()` does NOT fail this test: probed against
    this Postgres, the two agree on every character above, so the Python version is correct today. Only
    `test_fitting_keys_costs_one_query_whatever_the_run_length` notices that mutation, by the query count.

    So this test is forward-looking rather than a trap for that specific mistake. Its job is to fail if an
    ICU or collation change ever makes Postgres fold differently from the last time somebody checked --
    which would otherwise show up as the picker offering a square `assign` refuses, for one letter, for
    some hunters. `FOLD_CASES` is why it would fail loudly rather than never.
    """
    profile = _member()
    challenge = svc.start(profile, challenge_type)
    some_jobs = list(Job.objects.order_by('slug')[:4])
    contracts = [_contract(name, jobs=some_jobs[:2] if i % 2 else some_jobs[2:])
                 for i, name in enumerate(FOLD_CASES)]
    keys = list(challenge.slots.values_list('key', flat=True))

    for contract in contracts:
        claimed = eligibility.fitting_keys(challenge, contract)
        actual = {k for k in keys if eligibility.fits_slot(challenge, k, contract)}
        assert claimed == actual, '%s: fitting_keys says %s, fits_slot says %s' % (
            ascii(contract.name), sorted(claimed), sorted(actual))


def test_fitting_keys_ignores_whether_the_slot_is_already_taken():
    """It decides what could be OFFERED, not what is allowed -- `assign` owns that, and two places
    deciding it is how they come to disagree. The picker filters by its own slots, which it already
    holds, rather than spending a query to re-ask."""
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    svc.assign(challenge, profile, 'B', _contract('Bloodborne'))

    # A second B-game still reports B, even though B is now full.
    assert eligibility.fitting_keys(challenge, _contract('Brothers')) == {'B'}


def test_fitting_keys_costs_one_query_whatever_the_run_length():
    """The reason this function exists. Mapping `fits_slot` over 26 keys is 26 queries to fill in one
    square; a jobs run would be 25. Both shapes below are ONE."""
    profile = _member()
    az = svc.start(profile, CHALLENGE_TYPE_AZ)
    jobs_run = svc.start(profile, CHALLENGE_TYPE_JOBS)
    az_contract = _contract('Bloodborne')
    jobs_contract = _contract('Astro Bot', jobs=list(Job.objects.order_by('slug')[:3]))

    with CaptureQueriesContext(connection) as captured:
        eligibility.fitting_keys(az, az_contract)
    assert len(captured.captured_queries) == 1

    with CaptureQueriesContext(connection) as captured:
        eligibility.fitting_keys(jobs_run, jobs_contract)
    assert len(captured.captured_queries) == 1


# ── catchup_reasons: one precedence rule, two callers ─────────────────────────────────────────────

def test_catchup_reasons_is_empty_when_neither_rule_lifts_the_exclusion():
    """A deep pool and a hunter who has already finished one run: nothing lifts anything, so an
    already-completed contract stays unselectable."""
    profile = _member()
    _joined(profile, timezone.now() - timezone.timedelta(days=365))
    # Close the importer by recording a finished run.
    Challenge.objects.create(profile=profile, challenge_type=CHALLENGE_TYPE_AZ, name='Old',
                             total_slots=26, is_complete=True, completed_at=timezone.now())
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    # A pool well past HATCH_THRESHOLD so the hatch stays shut.
    for i in range(HATCH_THRESHOLD + 2):
        _contract('Bloodborne %d' % i)
    done = _contract('Brothers')
    _mark_completed(profile, done)

    assert svc.catchup_reasons(profile, challenge, 'B', [done]) == {}


def test_the_hatch_lifts_it_when_the_pool_is_thin():
    profile = _member()
    _joined(profile, timezone.now() - timezone.timedelta(days=365))
    Challenge.objects.create(profile=profile, challenge_type=CHALLENGE_TYPE_AZ, name='Old',
                             total_slots=26, is_complete=True, completed_at=timezone.now())
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    done = _contract('Brothers')
    _mark_completed(profile, done)

    assert svc.catchup_reasons(profile, challenge, 'B', [done]) == {done.id: COMPLETED_VIA_HATCH}


def test_import_wins_over_hatch_when_both_apply():
    """THE PRECEDENCE, and it is not cosmetic. Both rules can apply at once -- a thin pool AND a
    completion that post-dates the account, on a first run. `import` is the more specific rule and the one
    with a fairness date behind it, so it is the more honest label for what happened. A square records
    which applied, permanently."""
    profile = _member()
    _joined(profile, timezone.now() - timezone.timedelta(days=365))
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    done = _contract('Brothers')
    _platted_at(profile, done, timezone.now() - timezone.timedelta(days=30))

    reasons = svc.catchup_reasons(profile, challenge, 'B', [done])

    assert reasons == {done.id: COMPLETED_VIA_IMPORT}, 'the hatch must not claim an importable square'


def test_catchup_reasons_labels_a_mixed_page_row_by_row():
    """The picker's actual shape: one page holding both kinds and some that neither rule lifts. The old
    single-contract function could only answer one at a time, which is why the picker would have been
    tempted to decide precedence itself."""
    profile = _member()
    _joined(profile, timezone.now() - timezone.timedelta(days=365))
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    importable = _contract('Brothers')
    _platted_at(profile, importable, timezone.now() - timezone.timedelta(days=30))
    # Completed, but BEFORE the account existed, so the importer's date rule refuses it.
    too_old = _contract('Bloodborne')
    _platted_at(profile, too_old, timezone.now() - timezone.timedelta(days=800))

    reasons = svc.catchup_reasons(profile, challenge, 'B', [importable, too_old])

    assert reasons[importable.id] == COMPLETED_VIA_IMPORT
    # The hatch still covers it -- the B pool is thin -- but under the hatch's label, not the importer's.
    assert reasons[too_old.id] == COMPLETED_VIA_HATCH


@pytest.mark.parametrize('days_ago, expect_import', [(30, True), (800, False)])
def test_the_bulk_and_single_forms_cannot_disagree(days_ago, expect_import):
    """THE SHARING, PINNED. `assign` asks about one contract and the picker asks about a page, and they
    must return the same verdict for the same contract -- otherwise the picker offers `hatch` where the
    square gets stamped `import`, mislabelling a permanently locked, XP-bearing square.

    Asserted as an equivalence rather than by re-listing the expected labels, so it keeps holding if the
    precedence itself is ever revisited.
    """
    profile = _member()
    _joined(profile, timezone.now() - timezone.timedelta(days=365))
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    done = _contract('Brothers')
    _platted_at(profile, done, timezone.now() - timezone.timedelta(days=days_ago))

    bulk = svc.catchup_reasons(profile, challenge, 'B', [done]).get(done.id)
    single = svc._why_a_completed_contract_is_allowed(profile, challenge, 'B', done)

    assert bulk == single
    assert (bulk == COMPLETED_VIA_IMPORT) is expect_import


def test_catchup_reasons_asks_nothing_for_an_empty_page():
    """The picker calls this with whatever its slice held, which on a slot with no completed contracts is
    nothing. It must not spend the importer and hatch queries to answer about zero rows."""
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)

    with CaptureQueriesContext(connection) as captured:
        assert svc.catchup_reasons(profile, challenge, 'B', []) == {}

    assert captured.captured_queries == []


# ── the lock acknowledgement ──────────────────────────────────────────────────────────────────────

def test_a_completing_placement_stops_and_asks_before_it_writes():
    """THE ONE IRREVERSIBLE ACTION on a run, so it is the one the service refuses to do silently.

    An already-completed contract lands the square complete, and a completed square never clears -- it
    cannot be reassigned, and its job XP is keyed to it. So `assign` raises rather than writing, and
    nothing has changed when it does.
    """
    profile = _member()
    _joined(profile, timezone.now() - timezone.timedelta(days=365))
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    contract = _contract('Quest for Glory')
    _platted_at(profile, contract, timezone.now() - timezone.timedelta(days=30))

    with pytest.raises(svc.ConfirmationRequired) as caught:
        svc.assign(challenge, profile, 'Q', contract)

    assert caught.value.via == COMPLETED_VIA_IMPORT
    assert caught.value.contract_name == 'Quest for Glory'
    # NOTHING WAS WRITTEN. A stop that had already filled the square would be worse than no stop.
    slot = challenge.slots.get(key='Q')
    assert not slot.is_filled
    assert not slot.is_completed
    challenge.refresh_from_db()
    assert challenge.filled_count == 0


def test_the_hatch_asks_too_not_just_the_importer():
    """The owner asked for a confirmation on importing history. The hatch lands a square
    complete-and-locked by the same mechanism, so it gets the same stop -- narrower would mean explaining
    why one irreversible click asked and the other did not."""
    profile = _member()
    _joined(profile, timezone.now() - timezone.timedelta(days=365))
    Challenge.objects.create(profile=profile, challenge_type=CHALLENGE_TYPE_AZ, name='Old',
                             total_slots=26, is_complete=True, completed_at=timezone.now())
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    contract = _contract('Quest for Glory')
    _mark_completed(profile, contract)

    with pytest.raises(svc.ConfirmationRequired) as caught:
        svc.assign(challenge, profile, 'Q', contract)

    assert caught.value.via == COMPLETED_VIA_HATCH


def test_an_ordinary_placement_needs_no_confirmation():
    """The stop is for completing placements only. Filling an empty square with a game you have NOT
    finished is reversible -- you can clear it -- so asking would be noise."""
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)

    slot = svc.assign(challenge, profile, 'B', _contract('Bloodborne'))

    assert slot.is_filled and not slot.is_completed


def test_the_confirmation_is_a_challenge_error_so_an_old_caller_stays_safe():
    """`ConfirmationRequired` subclasses `ChallengeError` deliberately. A caller that only knows the base
    class still does something correct -- it declines to write and shows the message -- rather than
    crashing or, worse, writing anyway."""
    profile = _member()
    _joined(profile, timezone.now() - timezone.timedelta(days=365))
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    contract = _contract('Quest for Glory')
    _platted_at(profile, contract, timezone.now() - timezone.timedelta(days=30))

    with pytest.raises(svc.ChallengeError) as caught:
        svc.assign(challenge, profile, 'Q', contract)

    assert 'complete and locked' in str(caught.value)


def test_the_confirmation_comes_after_every_other_refusal():
    """A hunter must never be asked to confirm something that would then fail anyway -- that trains them to
    click through a dialog which sometimes does nothing. So an ineligible contract is refused on its shape
    even when it is one they have already finished."""
    profile = _member()
    _joined(profile, timezone.now() - timezone.timedelta(days=365))
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    wrong_letter = _contract('Astro Bot')
    _platted_at(profile, wrong_letter, timezone.now() - timezone.timedelta(days=30))

    with pytest.raises(svc.ChallengeError) as caught:
        svc.assign(challenge, profile, 'Q', wrong_letter)

    assert not isinstance(caught.value, svc.ConfirmationRequired)


# ── the duplicate guard, keyed on the live FK ─────────────────────────────────────────────────────

def test_a_staff_slug_edit_cannot_let_one_game_fill_two_squares():
    """THE DOUBLE-PAYOUT HOLE, and it had no test until an audit found it.

    The guard matched on `contract_slug`, the frozen snapshot. A Job Coverage game carries up to six jobs, so
    after staff edited that contract's slug the guard no longer recognised the game already sitting in one
    square -- and accepted it into a second. One completion, two squares, and since the job-XP guard is keyed
    on the slot, two payouts from one game. That is precisely what "one payout per job per challenge"
    forbids, and what `clear`'s refusal exists to protect.

    `challenge_service`'s own module header argues the live FK over the snapshot for exactly this reason; the
    argument had been applied to detection and not to this guard. A staff rename is routine churn, not an
    exotic case.
    """
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_JOBS)
    two = list(Job.objects.order_by('slug')[:2])
    game = _contract('Astro Bot', jobs=two)
    svc.assign(challenge, profile, two[0].slug, game)

    Contract.objects.filter(pk=game.pk).update(slug='renamed-by-staff')
    game.refresh_from_db()

    with _refuses('already in another square'):
        svc.assign(challenge, profile, two[1].slug, game)

    assert challenge.slots.filter(contract_id=game.pk).count() == 1


def test_a_renamed_contract_also_leaves_the_slots_pool():
    """The other half of the same bug: the pool excluded already-placed games by slug too, so a renamed one
    was offered again. The picker and `assign` were wrong together -- so the refusal a hunter would expect
    became an acceptance, with no disagreement between the two to notice."""
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_JOBS)
    two = list(Job.objects.order_by('slug')[:2])
    game = _contract('Astro Bot', jobs=two)
    svc.assign(challenge, profile, two[0].slug, game)

    Contract.objects.filter(pk=game.pk).update(slug='renamed-by-staff')

    pool = eligibility.eligible_contracts(profile, challenge, two[1].slug)
    assert game.pk not in set(pool.values_list('pk', flat=True))


# ── the importer belongs to A-Z alone ─────────────────────────────────────────────────────────────

def test_the_importer_is_closed_on_a_job_coverage_run():
    """A-Z ONLY (owner, 2026-09-28), and nothing covered this before -- the gate read the run count and never
    the type, so a fresh hunter's Job Coverage run offered history imports for months of dev use.

    The importer answers an unfairness specific to the ALPHABET: a hunter arrives with a library that already
    covers half the letters. A job is a shape of game rather than a name, and what protects a thin job square
    is the hatch."""
    profile = _member()
    _joined(profile, timezone.now() - timezone.timedelta(days=365))

    assert svc.importer_is_available(profile, CHALLENGE_TYPE_AZ) is True
    assert svc.importer_is_available(profile, CHALLENGE_TYPE_JOBS) is False


def test_a_job_square_refuses_a_finished_game_when_supply_is_thick():
    """THE WRITE DOOR, not just the panel. `assign` reaches the rule through the same `catchup_offers`, so
    gating the importer in one place has to close both -- and with supply thick the hatch cannot explain the
    fill either, so there is nothing left to lift the exclusion."""
    profile = _member()
    _joined(profile, timezone.now() - timezone.timedelta(days=365))
    challenge = svc.start(profile, CHALLENGE_TYPE_JOBS)
    job = Job.objects.order_by('slug').first()
    done = _contract('Astro Bot', jobs=[job])
    _platted_at(profile, done, timezone.now() - timezone.timedelta(days=30))
    # Thick supply, so the hatch is shut and only the importer could have allowed this.
    for i in range(HATCH_THRESHOLD + 2):
        _contract(f'Filler {i}', jobs=[job])

    with _refuses('already finished that one'):
        svc.assign(challenge, profile, job.slug, done)


def test_the_hatch_still_lifts_a_job_square_when_supply_is_thin():
    """THE OTHER HALF, so the fix cannot be read as "job runs have no catch-up". The hatch never looked at
    the challenge type and still does not: a thin job square is our curation gap, and it pays in full."""
    profile = _member()
    _joined(profile, timezone.now() - timezone.timedelta(days=365))
    challenge = svc.start(profile, CHALLENGE_TYPE_JOBS)
    job = Job.objects.order_by('slug').first()
    done = _contract('Astro Bot', jobs=[job])
    _platted_at(profile, done, timezone.now() - timezone.timedelta(days=30))

    slot = svc.assign(challenge, profile, job.slug, done, acknowledge_lock=True)

    assert slot.is_completed is True
    assert slot.completed_via == COMPLETED_VIA_HATCH


def test_an_az_square_still_imports_from_history():
    """THE POSITIVE CONTROL beside the two negatives, so "A-Z only" is pinned as a restriction rather than as
    a removal."""
    profile = _member()
    _joined(profile, timezone.now() - timezone.timedelta(days=365))
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    done = _contract('Astro Bot')
    _platted_at(profile, done, timezone.now() - timezone.timedelta(days=30))
    for i in range(HATCH_THRESHOLD + 2):
        _contract(f'Another A Game {i}')

    slot = svc.assign(challenge, profile, 'A', done, acknowledge_lock=True)

    assert slot.completed_via == COMPLETED_VIA_IMPORT


def test_a_second_job_coverage_run_is_numbered():
    """THE ONE JOBS CONSUMER OF `completed_run_count`, and it had no test.

    Restricting the importer to A-Z left `_auto_name` as the only thing that asks this question about a jobs
    run -- and an audit proved the gap by making `completed_run_count` return 0 for every non-A-Z type, which
    left the entire challenge suite green. The consequence of that regression is not an exception: it is a
    hunter's second, third and tenth Job Coverage run all being called "Job Coverage Challenge" forever.

    This is the invariant a future "simplify: fold the type check down into the count" refactor would break
    silently, which is exactly why the docstring's claim that it "sets the run's ordinal for BOTH types" needs
    something behind it.
    """
    profile = _member()
    first = svc.start(profile, CHALLENGE_TYPE_JOBS)
    assert first.name == 'Job Coverage Challenge'
    Challenge.objects.filter(pk=first.pk).update(is_complete=True, completed_at=timezone.now())

    second = svc.start(profile, CHALLENGE_TYPE_JOBS)

    assert second.name == 'Job Coverage Challenge (Run 2)'
    # And the A-Z ordinal is counted separately, so finishing one type does not number the other.
    assert svc.start(profile, CHALLENGE_TYPE_AZ).name == 'A-Z Challenge'


def test_the_jobs_hatch_does_not_care_when_the_game_was_finished():
    """THE HATCH AND THE IMPORTER'S DATE RULE, DISENTANGLED. The other jobs-hatch test uses a completion dated
    AFTER the account was created, so on the jobs side the two rules were never separated -- a future change
    that made the hatch consult the join date would have passed.

    A pre-join completion is the case that can only be the hatch: the importer would refuse it on the date even
    if it applied to this type, which it no longer does."""
    profile = _member()
    _joined(profile, timezone.now() - timezone.timedelta(days=365))
    challenge = svc.start(profile, CHALLENGE_TYPE_JOBS)
    job = Job.objects.order_by('slug').first()
    done = _contract('Astro Bot', jobs=[job])
    # BEFORE the account existed, so no reading of the importer could allow it.
    _platted_at(profile, done, timezone.now() - timezone.timedelta(days=800))

    slot = svc.assign(challenge, profile, job.slug, done, acknowledge_lock=True)

    assert slot.is_completed is True
    assert slot.completed_via == COMPLETED_VIA_HATCH


def test_a_deleted_and_restaged_contract_cannot_take_a_second_square():
    """THE HOLE MOVING THE UNIQUENESS KEY OPENED, refused cleanly instead of silently allowed.

    `contract` is `SET_NULL`, so deleting a `Contract` nulls the FK on every square that held it while the
    snapshot survives -- and a partial unique does not constrain NULLs. The game then comes back as a NEW row
    (staff re-create it, or `evaluate_contract_candidates._stage_contract` restages it once the delete frees
    its igdb id), its `EarnedContract` having cascaded away so the hunter no longer reads as having finished
    it. Nothing else refuses, and the run would quietly hold one game in two squares with two completions.

    The old slug-keyed constraint caught this by accident, as an `IntegrityError` -- a 500. This is the same
    protection as a refusal a hunter can read.
    """
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_JOBS)
    job, other = list(Job.objects.order_by('slug')[:2])
    original = _contract('Astro Bot', jobs=[job, other])
    slug, igdb_id = original.slug, original.igdb_id
    svc.assign(challenge, profile, job.slug, original)

    # Staff delete it: the square keeps its snapshot and loses its FK.
    Contract.objects.filter(pk=original.pk).delete()
    held = challenge.slots.get(key=job.slug)
    assert held.contract_id is None
    assert held.contract_slug == slug

    # The same game returns as a new row, on the slug the delete freed.
    restaged = Contract.objects.create(name='Astro Bot', slug=slug, is_live=True, igdb_id=igdb_id)
    restaged.jobs.set([job, other])

    with _refuses('already in another square'):
        svc.assign(challenge, profile, other.slug, restaged)


def test_a_renamed_contract_frees_its_slug_for_a_different_game():
    """THE 500 THE KEY MOVE FIXED, exercised through the service rather than by building rows by hand.

    `Contract.slug` is globally unique, so two contracts cannot share one -- but a staff rename frees the
    string, and the next contract to take it then matches a snapshot some square froze earlier. While the
    constraint keyed on that snapshot, this second placement raised `IntegrityError` AFTER the service had
    already allowed it (different FK), and nothing caught it: a 500, and a square that stayed empty on every
    retry.

    The model-level test of this builds the two rows directly; this one is the path a hunter actually takes.
    """
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_JOBS)
    job, other = list(Job.objects.order_by('slug')[:2])
    first = _contract('Sonic Frontiers', jobs=[job])
    svc.assign(challenge, profile, job.slug, first)
    frozen = challenge.slots.get(key=job.slug).contract_slug

    # The rename frees the slug; a DIFFERENT game takes it.
    Contract.objects.filter(pk=first.pk).update(slug=frozen + '-ps5')
    second = _contract('Sonic Superstars', jobs=[other])
    Contract.objects.filter(pk=second.pk).update(slug=frozen)
    second.refresh_from_db()

    slot = svc.assign(challenge, profile, other.slug, second)

    assert slot.contract_id == second.pk
    assert slot.contract_slug == frozen
    # Two squares, one frozen slug, two different games -- which is what it always was.
    assert challenge.slots.filter(contract_slug=frozen).count() == 2
