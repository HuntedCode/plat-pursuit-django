"""The only thing that writes a challenge.

Four rules hold everywhere below. THREE are `gamelists.services.game_list_service`'s, for the same
reasons it states them; the fourth this feature adds, and it is arguably the most important one here:

1. **A write refuses before it starts.** Every gate runs before the first INSERT, so a refusal leaves
   nothing behind.
2. **The lock comes before the value it protects, and the precondition is re-asserted on the row that
   came back.** Both halves. Reading a pivot off the caller's stale object after taking a lock is a
   silent corruption rather than an error.
3. **Denormalized counts move with the rows they count**, inside the same transaction, and they are
   derived from the ROWS rather than incremented -- `filled_count` and `completed_count` are what a
   card renders and what `challenge_completed_within_filled` polices, so drift is visible and fatal.
4. **State that a reward was paid against is never unwound.** A completed slot locks, so the XP guard
   keyed on that slot cannot be re-armed by clearing it. Note the payout itself is NOT in this module --
   `ChallengeSlot.xp_redeemed_at` has no writer yet and gains one with the rewards chunk. The rule is
   here now because the lock has to exist before the thing it protects does.

TWO GATES THIS MODULE DELIBERATELY DOES NOT HAVE, because their absence is a design decision rather
than an omission:

- **No `all_ugc` restriction check.** Every other user-content surface calls
  `restriction_service.is_restricted_from`, and challenges do not, because a challenge contains no
  words a hunter wrote. Names are GENERATED here (`_auto_name`); there is no description, no note and
  no free text anywhere in the feature. A restriction exists to stop somebody publishing prose, and
  there is none to stop. If a future change adds a field a hunter types into, that change owes this
  module the gate.
- **No banned-word check, no report path, no `text_hidden`.** Same reason, and this is most of why the
  feature is cheap: auto-naming removed an entire moderation surface rather than deferring it.

THE LOCK ORDER, written down because the next two chunks each add a link to it and a chain nobody
agreed on is a deadlock waiting for load:

    Profile -> EarnedContract -> Challenge -> ChallengeSlot -> ProfileJobXP

`start` takes the Profile lock; every slot writer here takes Challenge then Slot; detection will call
`contract_service.mark_contract_reached` (EarnedContract) before `mark_slot_completed`; and XP
redemption will end at `ProfileJobXP`, which `contract_service.revoke_contract` already locks from the
`EarnedContract` side. Reversing any pair trades a race for a deadlock -- the same note
`revoke_contract` leaves about staying in step with `accept_contracts_bulk`.

**NO SLOT IS WRITTEN OUTSIDE ITS CHALLENGE'S ROW LOCK.** That invariant is what makes `_recount` safe
against concurrent slot writes, and it is the one a sweep would be tempted to break with a
`bulk_update`. A future writer touching many slots takes the Challenge lock per run.

AND FOR WHOEVER WRITES DETECTION: match a slot to a contract on `contract_slug`, NEVER on
`contract_id`. The slot holds a SNAPSHOT, so the two differ precisely when the catalogue has moved
under a run -- a re-anchor, a staff `igdb_id` edit, an absorbed concept -- which is the case detection
exists to survive. A `filter(contract=contract)` looks correct, passes every happy-path test, and
silently misses exactly the runs that needed it. The lookup belongs in this module when it is written,
so there is one rule rather than one per detector.
"""
from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

from challenges.models import (
    AZ_LETTERS,
    CHALLENGE_TYPE_AZ,
    CHALLENGE_TYPE_CHOICES,
    CHALLENGE_TYPE_JOBS,
    CHALLENGE_TYPES,
    COMPLETED_VIA_HATCH,
    COMPLETED_VIA_IMPORT,
    COMPLETED_VIA_LIVE,
    Challenge,
    ChallengeSlot,
)
from challenges.services import eligibility
from trophies.models import Job, Profile


class ChallengeError(Exception):
    """A refusal a caller is expected to show the hunter. The message is user-facing."""


# ── gates ────────────────────────────────────────────────────────────────────────────────────────

def _refuse_if_unlinked(profile):
    """The same bar every other personal surface sets. A challenge is built out of the hunter's own
    completions, so without a linked PSN account there is nothing for it to read."""
    if profile is None or not profile.is_linked:
        raise ChallengeError('Link your PSN account to start a challenge.')


def creation_is_open_to(profile):
    """Can this hunter START a run right now?

    The beta gate, and it is a DIAL rather than a door: the hub and every run on it are public to
    everyone from day one, and what members get during the beta is to go FIRST. That is what the
    storefront already sells by name -- "First through the door when Challenges returns" against an
    everyone-side of "Everything, when it ships" -- so members-create-first honours the words as
    written, and beta's end opens creation to everybody without taking anything back.

    Flipped by `CHALLENGES_BETA_MEMBERS_ONLY`, an env var rather than a code constant, so ending the
    beta is a Render setting change and not a deploy.
    """
    if not getattr(settings, 'CHALLENGES_BETA_MEMBERS_ONLY', False):
        return True
    return bool(profile is not None and profile.user_is_premium)


def _refuse_if_beta_gated(profile):
    """A refusal with the reason in it, because the page this backs is a REAL page for free hunters.

    When Challenges was parked the decision was that somebody following a link into it gets told so on
    a page, never bounced -- `tests/engine/test_challenges_coming_soon.py` pins that. The same applies
    here: `trophies.mixins.PremiumRequiredMixin` would REDIRECT to `beta_access_required`, which is the
    behaviour that was rejected, so this module refuses with a message the page renders in place.
    """
    if not creation_is_open_to(profile):
        raise ChallengeError(
            'Challenges are in beta for members first. '
            'Everyone can start one when the beta ends -- browsing is open to all now.'
        )


def _check_type(challenge_type):
    if challenge_type not in CHALLENGE_TYPES:
        raise ChallengeError('That is not a challenge type.')
    return challenge_type


def _require_owner(challenge, profile):
    """Ownership, asked about the row rather than about the URL."""
    if challenge.is_deleted:
        raise ChallengeError('That challenge is hidden.')
    if challenge.profile_id != profile.id:
        raise ChallengeError('That is not your challenge.')


def _lock_challenge(challenge):
    """Re-read FOR UPDATE and re-assert the precondition on the row that came back.

    Both halves, the way `game_list_service._lock_list` does it, and BOTH preconditions. An earlier
    version re-asserted only `is_complete` while claiming a hidden run was writable anyway -- which was
    false, since `_require_owner` refuses one -- and that left `is_deleted` checked on a stale row, so
    `hide` committing in the window let a write land on a hidden run.

    Why each refuses: a FINISHED run must refuse every further write, or a square could be reassigned
    under a badge and a title already granted. A HIDDEN run refuses because the hunter took it out of
    view, and `start` is the way back -- it un-hides.
    """
    locked = Challenge.objects.select_for_update().get(pk=challenge.pk)
    if locked.is_complete:
        raise ChallengeError('That challenge is finished.')
    if locked.is_deleted:
        raise ChallengeError('That challenge is hidden. Start it again to pick it back up.')
    return locked


def _lock_slot(locked_challenge, key):
    """The pivot, re-read under the parent's lock and scoped to the parent.

    Scoped to the challenge as well as the key, so this doubles as the ownership check every caller
    would otherwise have to remember -- and so a key from one run can never address a slot in another.
    """
    slot = (
        ChallengeSlot.objects.select_for_update()
        .filter(challenge=locked_challenge, key=key)
        .first()
    )
    if slot is None:
        raise ChallengeError('That is not a slot on this challenge.')
    return slot


# ── reading a hunter's history ───────────────────────────────────────────────────────────────────

def completed_run_count(profile, challenge_type):
    """How many runs of this type this hunter has FINISHED. One query, and the answer to two questions.

    It sets the run's ordinal in `_auto_name`, and it decides whether the history importer is available
    (`importer_is_available`). Deliberately counts COMPLETIONS rather than runs created: a hunter who
    starts a run, hides it and starts again has not finished anything, and should not be treated as
    though they had.
    """
    return Challenge.objects.filter(
        profile=profile, challenge_type=challenge_type, is_complete=True,
    ).count()


def importer_is_available(profile, challenge_type):
    """Is the first-run history importer open to this hunter?

    "Their first completion, not any subsequent ones" (owner, 2026-09-26), read as *has never completed
    one* rather than *this is literally run 1*. The generous reading, and the faithful one: abandoning a
    run should not burn the catch-up, and completing one closes it permanently, so it cannot be farmed.
    """
    return completed_run_count(profile, challenge_type) == 0


def _auto_name(profile, challenge_type):
    """The run's name, GENERATED. No hunter ever types one.

    That is what keeps this feature free of a moderation surface -- no banned-word check, no report
    path, no `text_hidden`, no mod queue -- and it costs nothing, because a run's identity to a reader
    is its owner and its type, both of which are already on the card.
    """
    label = dict(CHALLENGE_TYPE_CHOICES)[challenge_type]
    ordinal = completed_run_count(profile, challenge_type) + 1
    return label if ordinal == 1 else f'{label} (Run {ordinal})'


def slot_keys_for(challenge_type):
    """The slot keys for a new run, in render order.

    A-Z is the alphabet. Job Coverage is every job in the catalogue, ordered the way Career orders them
    -- `job_render.discipline_order()` exists precisely because sorting on the `discipline` COLUMN gives
    a different sequence from the canonical radar one (they agree for two disciplines and then diverge,
    so the wrong order reads as right until the third row).

    The returned list IS the snapshot: whatever the catalogue holds now is what this run will ever
    require, because the `ChallengeSlot` rows are created from it once and never recomputed.
    """
    if challenge_type == CHALLENGE_TYPE_AZ:
        return list(AZ_LETTERS)

    from trophies.services.job_render import discipline_order
    return list(
        Job.objects.order_by(discipline_order(), 'display_order', 'name')
        .values_list('slug', flat=True)
    )


# ── starting, resuming and hiding ────────────────────────────────────────────────────────────────

@transaction.atomic
def start(profile, challenge_type):
    """Start a run of `challenge_type` -- or hand back the one already in progress.

    RESUME BEFORE CREATE, and that is the whole shape of "there is no delete, only hide". Three cases,
    in order:

    1. An active run exists -> return it. Makes the call idempotent, so a double-submitted Start button
       cannot race `challenge_one_active_per_type` into a bare IntegrityError.
    2. A HIDDEN, unfinished run exists -> un-hide it and return it, progress intact. This is the rule:
       hiding takes a run off your profile and out of the hub, and pressing Start brings that same run
       back rather than dealing a fresh one. Nothing to reroll into anyway -- the hunter picks every
       slot themselves, so a "new" run is identical in every respect except that it would have thrown
       away completed work.
    3. Otherwise create one, with its full set of empty slots.

    The lock is on the PROFILE, not on the challenges. `@transaction.atomic` alone does nothing here:
    at READ COMMITTED two requests both find no active run, both insert, and one dies on the unique
    with a 500 instead of a message. `SELECT ... FOR UPDATE` locks rows that EXIST, so locking a
    filtered challenge queryset locks nothing in exactly the case that matters -- the account always
    exists. Same reasoning `game_list_service.create_list` writes down for the same shape.
    """
    _refuse_if_unlinked(profile)
    challenge_type = _check_type(challenge_type)
    _refuse_if_beta_gated(profile)

    Profile.objects.select_for_update().filter(pk=profile.pk).first()

    active = Challenge.objects.filter(
        profile=profile, challenge_type=challenge_type, is_complete=False, is_deleted=False,
    ).first()
    if active is not None:
        return active

    # `-updated_at` because more than one hidden unfinished run is reachable only by writing around
    # this service (the partial unique does not cover hidden rows). The newest is the one a hunter
    # means; the others stay hidden and harmless.
    # `select_for_update`, because this branch MUTATES the row it finds and rule 2 applies to it as much
    # as to a slot write: without the lock, detection completing the last square of a hidden run in the
    # window hands back a run that is already finished, after which every write answers "That challenge
    # is finished." It self-heals on the next Start, which is why it was easy to miss.
    hidden = Challenge.objects.select_for_update().filter(
        profile=profile, challenge_type=challenge_type, is_complete=False, is_deleted=True,
    ).order_by('-updated_at').first()
    if hidden is not None:
        hidden.is_deleted = False
        hidden.deleted_at = None
        hidden.save(update_fields=['is_deleted', 'deleted_at', 'updated_at'])
        return hidden

    keys = slot_keys_for(challenge_type)
    if not keys:
        # Only reachable for `jobs` against an empty Job catalogue, which would otherwise create a
        # zero-slot run and be refused by `challenge_total_slots_positive` as a bare IntegrityError.
        raise ChallengeError('That challenge is not available right now.')

    challenge = Challenge.objects.create(
        profile=profile, challenge_type=challenge_type,
        name=_auto_name(profile, challenge_type), total_slots=len(keys),
    )
    ChallengeSlot.objects.bulk_create([
        ChallengeSlot(challenge=challenge, key=key, position=i) for i, key in enumerate(keys)
    ])
    return challenge


@transaction.atomic
def hide(challenge, profile):
    """Take a run off the hunter's profile and out of the hub. NOT a delete, and not called one.

    Nothing is destroyed and no progress is lost: `start` brings this same run back. The word matters
    in the UI for that reason -- a button labelled Delete would promise something this does not do, and
    a hunter who expected erasure would be surprised twice.

    Idempotent, so a double-submit is not an error.
    """
    _require_owner_allowing_hidden(challenge, profile)
    # `.first()` not `.get()`, and NOT `_lock_challenge`: that helper refuses a finished run, and a
    # finished run must still be hideable. `.get()` is the bug `game_list_service.delete_list` documents
    # fixing -- a row removed by a shell or a `Profile` cascade turns an idempotent soft-delete into an
    # unhandled `DoesNotExist`, i.e. a 500 where this promises a no-op.
    locked = Challenge.objects.select_for_update().filter(pk=challenge.pk).first()
    if locked is None:
        return challenge
    if locked.is_deleted:
        return locked
    locked.is_deleted = True
    locked.deleted_at = timezone.now()
    locked.save(update_fields=['is_deleted', 'deleted_at', 'updated_at'])
    return locked


def _require_owner_allowing_hidden(challenge, profile):
    """Ownership without the `is_deleted` refusal `_require_owner` makes.

    `hide`'s whole subject is that flag, so refusing on it would make it refuse the state it exists to
    change. It is the only caller: the un-hide is three inline lines in `start`, reached through a
    `profile=profile` filter that makes ownership implicit rather than checked.
    """
    if challenge.profile_id != profile.id:
        raise ChallengeError('That is not your challenge.')


# ── assigning and clearing ───────────────────────────────────────────────────────────────────────

@transaction.atomic
def assign(challenge, profile, key, contract):
    """Put `contract` in the `key` slot, and complete the slot if the hunter has already finished it.

    Returns the saved slot. Every refusal happens before the first write.

    THE COMPLETED-CONTRACT BRANCH is the interesting half. Normally an already-completed contract is
    not selectable at all -- it would land the slot complete instantly. Two rules lift that, and they
    are checked in this order because the labels are not interchangeable:

    - the **importer** (first run only, and only for a completion earned after the hunter joined), and
    - the **hatch** (any run, but only when supply for this slot is down to `HATCH_THRESHOLD`).

    `import` wins when both apply: it is the more specific rule and the one with a fairness date behind
    it, so it is the more honest label for what happened.

    A slot completed this way LOCKS immediately, like any other completed slot. That is why the caller
    must confirm before invoking the importer -- it is the one irreversible action a hunter can take on
    their own run.
    """
    _refuse_if_unlinked(profile)
    _require_owner(challenge, profile)
    locked = _lock_challenge(challenge)
    slot = _lock_slot(locked, key)

    if slot.is_completed:
        raise ChallengeError('That square is finished. Finished squares stay as they are.')
    if contract is None or not contract.is_live:
        raise ChallengeError('That game is not on the Job Board.')

    _refuse_if_wrong_shape(locked, key, contract)

    if locked.slots.exclude(pk=slot.pk).filter(contract_slug=contract.slug).exists():
        raise ChallengeError('That game is already in another square of this run.')

    completed_via = None
    if _hunter_has_completed(profile, contract):
        completed_via = _why_a_completed_contract_is_allowed(profile, locked, key, contract)
        if completed_via is None:
            raise ChallengeError(
                'You have already finished that one. Pick a game you can still complete.'
            )

    slot.contract = contract
    slot.contract_slug = contract.slug
    slot.contract_name = contract.name
    slot.assigned_at = timezone.now()
    fields = ['contract', 'contract_slug', 'contract_name', 'assigned_at']

    if completed_via is not None:
        slot.is_completed = True
        slot.completed_at = timezone.now()
        slot.completed_via = completed_via
        fields += ['is_completed', 'completed_at', 'completed_via']

    slot.save(update_fields=fields)
    _recount(locked)
    return slot


def _refuse_if_wrong_shape(challenge, key, contract):
    """Does this contract belong in this slot at all?

    DELEGATED to `eligibility.fits_slot`, which is the same predicate the picker's pool and the hatch's
    count use. An earlier version re-expressed the A-Z half in Python as
    `contract.name.upper().startswith(key.upper())`, and that DISAGREES with the queryset's
    `name__istartswith` in both directions: Python's `upper()` does full Unicode case mapping while
    Postgres folds with `lower()`. A name the pool offered and this refused was a dead-end click; a
    name this accepted and the pool never held was invisible to the hatch's count. One rule, one
    spelling.
    """
    if eligibility.fits_slot(challenge, key, contract):
        return
    if challenge.challenge_type == CHALLENGE_TYPE_AZ:
        raise ChallengeError(f'That game does not start with {key}.')
    raise ChallengeError('That game does not cover that job.')


def _hunter_has_completed(profile, contract):
    """One indexed existence check, and the only exclusion the pool still has.

    `EarnedContract` existing IS completion -- the row is created the moment a tier is reached, and
    either tier counts because platinum is not required.
    """
    from trophies.models import EarnedContract
    return EarnedContract.objects.filter(profile=profile, contract=contract).exists()


def _why_a_completed_contract_is_allowed(profile, challenge, key, contract):
    """`COMPLETED_VIA_IMPORT`, `COMPLETED_VIA_HATCH`, or None if neither rule lifts the exclusion."""
    if importer_is_available(profile, challenge.challenge_type):
        joined_at = getattr(getattr(profile, 'user', None), 'date_joined', None)
        if contract.id in eligibility.importable_ids(profile, [contract], joined_at):
            return COMPLETED_VIA_IMPORT
    if eligibility.hatch_is_open(profile, challenge, key):
        return COMPLETED_VIA_HATCH
    return None


@transaction.atomic
def clear(challenge, profile, key):
    """Empty an UNFINISHED slot so the hunter can change their mind. Returns the slot.

    A finished square is never clearable, and that is load-bearing rather than tidy. The job-XP guard
    is keyed on the slot, so clearing a paid square and refilling it is precisely how one job could be
    paid twice in one run -- "one payout per job per challenge" (owner, 2026-09-26) holds BECAUSE this
    refuses. Completed squares also represent work that happened, and there is no version of taking
    that off a run that a hunter would thank us for.
    """
    _refuse_if_unlinked(profile)
    _require_owner(challenge, profile)
    locked = _lock_challenge(challenge)
    slot = _lock_slot(locked, key)

    if slot.is_completed:
        raise ChallengeError('That square is finished. Finished squares stay as they are.')
    if not slot.is_filled:
        return slot

    slot.contract = None
    slot.contract_slug = ''
    slot.contract_name = ''
    slot.assigned_at = None
    slot.save(update_fields=['contract', 'contract_slug', 'contract_name', 'assigned_at'])
    _recount(locked)
    return slot


# ── completion ───────────────────────────────────────────────────────────────────────────────────

@transaction.atomic
def mark_slot_completed(slot, *, when=None, via=COMPLETED_VIA_LIVE):
    """Record that a filled slot's contract is finished. Idempotent. Returns True if anything changed.

    The write half of detection, which lives here rather than in the detector so that every path to a
    completed slot -- the sync hook, the nightly sweep, the importer, the hatch -- goes through one
    function and cannot disagree about what completing a slot entails.

    `completed_at` is OURS and is never read back from `EarnedContract`. That row gets DELETED when staff
    run `reconcile_contracts` over a contract whose derived membership stopped qualifying -- staff-run
    and never scheduled, because it deletes banked XP (`tests/engine/test_nightly.py` pins that it is
    not a cron step) -- and a run must survive catalogue bookkeeping the hunter never saw.
    """
    if slot.is_completed or not slot.is_filled:
        return False

    locked_challenge = Challenge.objects.select_for_update().get(pk=slot.challenge_id)
    fresh = ChallengeSlot.objects.select_for_update().get(pk=slot.pk)
    # BOTH preconditions re-asserted on the row that came back, not just `is_completed`. `clear` takes
    # these same locks in this same order, so it reliably commits FIRST -- which means a detector that
    # re-checked only completion would stamp a square the hunter had just emptied, violating
    # `challengeslot_completed_is_filled_dated_and_explained` and turning a background sweep into a 500.
    # Exactly the stale-pivot failure rule 2 names, and that `game_list_service._lock_item` was written
    # to close.
    if fresh.is_completed or not fresh.is_filled:
        return False

    fresh.is_completed = True
    fresh.completed_at = when or timezone.now()
    fresh.completed_via = via
    fresh.save(update_fields=['is_completed', 'completed_at', 'completed_via'])
    _recount(locked_challenge)
    return True


def _recount(challenge):
    """Recompute the denormalized counters FROM THE ROWS, and finish the run if that was the last slot.

    Derived rather than incremented, which is the same call `game_list_service` makes about `position`:
    a counter that is incremented is load-bearing for correctness, and an admin edit or a repair can
    then leave it wrong in a way no user action fixes. Here it is also policed --
    `challenge_completed_within_filled` and `challenge_filled_within_total` turn drift into an error
    rather than a wrong-looking number.

    One aggregate, not two queries, and the completion stamp rides the same UPDATE.

    The constraints are not only a drift DETECTOR, and it is worth being plain about where the error
    lands: if a slot row were ever added out of band (a shell -- the admin cannot, being fully
    read-only), `filled_count` would exceed `total_slots` and this UPDATE would raise on
    `challenge_filled_within_total` for every subsequent assign, clear and completion on that run,
    permanently, with nothing in the UI that repairs it. A repair command would have to.
    """
    counts = challenge.slots.aggregate(
        filled=models.Count('pk', filter=~models.Q(contract_slug='')),
        completed=models.Count('pk', filter=models.Q(is_completed=True)),
    )
    challenge.filled_count = counts['filled'] or 0
    challenge.completed_count = counts['completed'] or 0
    fields = ['filled_count', 'completed_count', 'updated_at']

    if not challenge.is_complete and challenge.completed_count >= challenge.total_slots:
        challenge.is_complete = True
        challenge.completed_at = timezone.now()
        fields += ['is_complete', 'completed_at']

    challenge.save(update_fields=fields)
    return challenge
