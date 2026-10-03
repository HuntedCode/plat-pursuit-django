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

`start` takes the Profile lock; every slot writer here takes Challenge then Slot. Detection will run
`contract_service.mark_contract_reached` before `mark_slot_completed` -- that function takes no row lock
of its own, so it contributes ordering rather than a lock -- while `accept_contracts_bulk` and
`revoke_contract` DO lock `EarnedContract` and then `ProfileJobXP`, which is where XP redemption will
end up. Reversing any pair trades a race for a deadlock, which is the note `revoke_contract` already
leaves about staying in step with `accept_contracts_bulk`.

**NO SLOT IS WRITTEN OUTSIDE ITS CHALLENGE'S ROW LOCK.** That invariant is what makes `_recount` safe
against concurrent slot writes, and it is the one a sweep would be tempted to break with a
`bulk_update`. A future writer touching many slots takes the Challenge lock per run.

AND FOR WHOEVER WRITES DETECTION: match an unfinished slot to a contract on the LIVE FK
(`contract_id`), not on the frozen `contract_slug`. An earlier version of this note said the opposite
and reasoned from causes that do not apply: a re-anchor, an `igdb_id` edit or an absorbed concept all
change which CONCEPTS a Contract covers, while the Contract ROW stays put -- so both keys keep working
through every one of them.

Where they actually diverge: a staff edit to `Contract.slug` breaks the slug match and leaves the FK
correct, and deleting the Contract nulls the FK (`on_delete=SET_NULL`) and leaves the slug behind. The
first is realistic churn and argues for the FK; the second cannot matter to detection, because a
deleted contract is never reached in the first place. The snapshot's job is the IDENTITY of a finished
square -- what it says on the card years later -- not matching.

The lookup still belongs in this module when it is written, so there is one rule rather than one per
detector.
"""
from django.conf import settings
from django.db import models, transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from challenges.models import (
    AZ_LETTERS,
    CHALLENGE_TYPE_AZ,
    CHALLENGE_TYPE_CALENDAR,
    CHALLENGE_TYPE_CHOICES,
    CHALLENGE_TYPE_JOBS,
    CHALLENGE_TYPES,
    COMPLETED_VIA_HATCH,
    COMPLETED_VIA_IMPORT,
    COMPLETED_VIA_LIVE,
    CalendarDay,
    Challenge,
    ChallengeQuerySet,
    ChallengeSlot,
    calendar_day_keys,
)
from challenges.services import eligibility
from trophies.models import EarnedContract, Job, Profile


class ChallengeError(Exception):
    """A refusal a caller is expected to show the hunter. The message is user-facing."""


class ConfirmationRequired(ChallengeError):
    """Not a refusal: a stop. The caller must confirm and retry, and `via` says what they are confirming.

    A SUBCLASS of `ChallengeError` on purpose, so an existing caller that catches the base class and shows
    the message still does something correct and safe -- it declines to write and tells the hunter why. A
    caller that wants the two-step flow catches this first and offers the confirmation.

    `via` is `COMPLETED_VIA_IMPORT` or `COMPLETED_VIA_HATCH`; `contract_name` is the snapshot-to-be, so the
    confirmation can name the game without a second query.
    """

    def __init__(self, message, *, via, contract_name):
        super().__init__(message)
        self.via = via
        self.contract_name = contract_name


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
    a page, never bounced -- `tests/engine/test_challenges_live.py` pins that (it was `test_challenges_coming_soon.py` until the real
    pages took the URL, and was inverted rather than deleted). The same applies
    here: `trophies.mixins.PremiumRequiredMixin` would REDIRECT to `beta_access_required`, which is the
    behaviour that was rejected, so this module refuses with a message the page renders in place.
    """
    if not creation_is_open_to(profile):
        raise ChallengeError(
            'Starting a challenge is in beta for members first. '
            'Everyone can start one when the beta ends.'
        )


def _check_type(challenge_type):
    if challenge_type not in CHALLENGE_TYPES:
        raise ChallengeError('That is not a challenge type.')
    return challenge_type


def _require_owner(challenge, profile):
    """Ownership, asked about the row rather than about the URL. ONLY ownership.

    It used to refuse a hidden run too, which put that rule in two places and left the more useful
    wording in the one a hunter almost never reached: this check runs on the caller's stale object and
    fires first, so `_lock_challenge`'s better message was only reachable inside a race window. The
    hidden rule now lives in `_lock_challenge` alone, where it is re-asserted under the lock.

    Dropping it here also fixes the ordering: a non-owner poking a hidden run is now told it is not
    theirs rather than that it is hidden.
    """
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

    It sets the run's ordinal in `_auto_name` for BOTH types, and it is half of whether the history
    importer is available -- the other half being that the importer is A-Z only, which
    `importer_is_available` owns rather than this. Deliberately counts COMPLETIONS rather than runs created:
    a hunter who starts a run, hides it and starts again has not finished anything, and should not be
    treated as though they had.
    """
    return Challenge.objects.filter(
        profile=profile, challenge_type=challenge_type, is_complete=True,
    ).count()


def importer_is_available(profile, challenge_type):
    """Is the first-run history importer open to this hunter?

    A-Z ONLY (owner, 2026-09-28). The importer shipped for both types and that was wrong: it is a fix for a
    specific unfairness in the ALPHABET, where a hunter already holds completions covering half the letters
    and the run would otherwise ask them to re-earn work they have done.

    SAID MORE CAREFULLY THAN IT WAS, because the wider phrasing contradicted the shipped predicate: this used
    to say a hunter "arrives with a library" full of such games, and a library they arrived WITH is exactly
    what `eligibility.importable_dates` excludes (`when > joined_at`). Only completions earned after the
    account existed import, so this rewards time spent here rather than a back catalogue. The boundary is the
    signup INSTANT and not the day (`date_joined` is a datetime), so a contract finished an hour after
    joining does import -- the claim to avoid is the wider one about a library, not the narrow one about the
    clock. That is the anchor working as intended, but no copy anywhere may promise the wider thing.

    Job Coverage has no equivalent claim -- a job is a shape of game rather than a name, its squares are not scarce in the
    same way, and the thing that actually protects a thin job square is the hatch, which applies to BOTH
    types and always did (`eligibility.hatch_is_open` has never looked at the challenge type).

    So a Job Coverage run now has exactly one catch-up rule instead of two, and it is the one that describes
    its real failure mode: our supply for that job was too thin.

    THE SECOND HALF, unchanged: "their first completion, not any subsequent ones" (owner, 2026-09-26), read
    as *has never completed one* rather than *this is literally run 1*. The generous reading, and the
    faithful one -- abandoning a run should not burn the catch-up, and completing one closes it permanently,
    so it cannot be farmed.

    ONE GATE, and that is why this is the only place it changed. Three callers reach it -- `catchup_offers`,
    `picker.history_panel` and `ChallengeDetailView` (whether the page's door opens at all) -- and not one of
    them re-expresses the rule, which is the property that matters. So the panel cannot offer an import the
    write would refuse, or the reverse.

    An earlier version of this docstring said `catchup_offers` was the SINGLE caller, which was never true of
    the page door and stopped being true of the panel the day the history importer got its own view. The
    claim to make about a gate like this is that nothing copies it, not that nothing else calls it -- a
    caller count is a fact about the codebase today and rots on the next feature.
    """
    return challenge_type == CHALLENGE_TYPE_AZ and completed_run_count(profile, challenge_type) == 0


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

    if challenge_type == CHALLENGE_TYPE_CALENDAR:
        return calendar_day_keys()

    if challenge_type == CHALLENGE_TYPE_JOBS:
        from trophies.services.job_render import discipline_order
        return list(
            Job.objects.order_by(discipline_order(), 'display_order', 'name')
            .values_list('slug', flat=True)
        )

    # EXPLICIT PER TYPE, WITH NO FALL-THROUGH, and the fall-through this replaces was a live trap. The
    # jobs branch used to be the bare `else`, so the moment `calendar` was added to
    # `CHALLENGE_TYPE_CHOICES` -- which is also what let it past the `CHALLENGE_TYPES` check in
    # `start_reporting` -- a Calendar run would have been created with twenty-five JOB slots and a
    # `total_slots` of 25. No exception, no empty list, just a silently wrong run of the wrong shape.
    # A new type is exactly when this function must refuse rather than guess.
    raise ChallengeError('That challenge is not available right now.')


# ── starting, resuming and hiding ────────────────────────────────────────────────────────────────

#: What `start_reporting` did. A run that was already going is NOT the same event as one resumed from
#: hiding, and the page has to say different things about them -- so the branch is reported rather
#: than inferred from `filled_count`, which cannot distinguish an empty resumed run from a fresh one
#: and cannot distinguish a resumed run from one that never went away.
CREATED = 'created'
RESUMED = 'resumed'
ALREADY_ACTIVE = 'already_active'


def start(profile, challenge_type):
    """`start_reporting`, for callers that only want the run. One implementation.

    Every caller is now a TEST -- the one production call site (`challenges.views.StartChallengeView`)
    was migrated to `start_reporting`, because the page needs the outcome. Kept because ~85 test call
    sites read better without an index, not because anything ships against it."""
    return start_reporting(profile, challenge_type)[0]


@transaction.atomic
def start_reporting(profile, challenge_type):
    """Start a run of `challenge_type` -- or hand back the one already in progress.

    Returns `(challenge, outcome)` -- the same ARITY as Django's `get_or_create`, though that returns a
    bool and this returns one of three values, which is the entire reason the function exists.

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

    # The locked row is BOUND, not discarded, because the beta gate below now reads it -- and rule 2
    # says a precondition inside a lock is re-asserted on the row that came back. While the gate sat
    # above the lock, reading the caller's instance was plainly best-effort; inside it, it is not.
    locked_profile = Profile.objects.select_for_update().filter(pk=profile.pk).first() or profile

    active = active_run(profile, challenge_type)
    if active is not None:
        return active, ALREADY_ACTIVE

    # `-updated_at` because more than one hidden unfinished run is reachable only by writing around
    # this service (the partial unique does not cover hidden rows). The newest is the one a hunter
    # means; the others stay hidden and harmless.
    # `select_for_update`, because this branch MUTATES the row it finds and rule 2 applies to it as much
    # as to a slot write: without the lock, detection completing the last square of a hidden run in the
    # window hands back a run that is already finished, after which every write answers "That challenge
    # is finished." It self-heals on the next Start, which is why it was easy to miss.
    #
    # HOW it is safe is worth naming, because it is not exclusion. If detection wins, Postgres re-checks
    # the WHERE against the new row version once the lock is granted (EvalPlanQual); the row now has
    # `is_complete=True`, fails the qual, and with `LIMIT 1` the statement returns nothing. `.first()` is
    # None, and a fresh run is created -- which is legal, because `challenge_one_active_per_type` is
    # partial on the unfinished-and-visible predicate. So this can return None while another hidden row
    # exists, which is benign only because of the newest-wins rule stated just above.
    hidden = _hidden_unfinished(profile, challenge_type).select_for_update().first()
    if hidden is not None:
        hidden.is_deleted = False
        hidden.deleted_at = None
        hidden.save(update_fields=['is_deleted', 'deleted_at', 'updated_at'])
        return hidden, RESUMED

    # THE BETA GATE FIRES HERE, not at the top, and the difference is a lapsed member's own run.
    # It gates CREATING a run, never keeping or resuming one -- the same line
    # `gamelists._refuse_if_not_member` draws, where a lapsed member keeps everything and only loses
    # making more. Checked at the top, a hunter whose membership ended (or anyone at all, if the flag
    # is switched on after runs exist) was refused a Continue on a run they had already started,
    # while the page showed them a live button for it.
    #
    # ONE CONSEQUENCE OF MOVING IT, stated because rule 1 says a write refuses before it starts: a
    # gated request now takes the Profile row lock and runs two SELECTs before being refused, where it
    # used to be refused lock-free. Immaterial in practice -- the lock is per-profile and the door is
    # rate limited per user -- but it is no longer literally true that this refusal costs nothing.
    _refuse_if_beta_gated(locked_profile)

    keys = slot_keys_for(challenge_type)
    if not keys:
        # Only reachable for `jobs` against an empty Job catalogue, which would otherwise create a
        # zero-slot run and be refused by `challenge_total_slots_positive` as a bare IntegrityError.
        raise ChallengeError('That challenge is not available right now.')

    challenge = Challenge.objects.create(
        profile=profile, challenge_type=challenge_type,
        name=_auto_name(profile, challenge_type), total_slots=len(keys),
    )
    if challenge_type == CHALLENGE_TYPE_CALENDAR:
        # A Calendar run's rows are `CalendarDay`, not `ChallengeSlot`: its atom is a date rather than a
        # contract, so the slot's contract FK, frozen snapshot, contract-uniqueness constraint and
        # `xp_redeemed_at` would all be dead weight. `slot_keys_for` returns (month, day) pairs here,
        # which is why `total_slots` above is still simply `len(keys)` -- one function answers "what does
        # a run of this type require" for every type, so the count and the rows cannot disagree.
        CalendarDay.objects.bulk_create([
            CalendarDay(challenge=challenge, month=month, day=day) for month, day in keys
        ])
    else:
        ChallengeSlot.objects.bulk_create([
            ChallengeSlot(challenge=challenge, key=key, position=i) for i, key in enumerate(keys)
        ])
    return challenge, CREATED


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
        # The row is gone. Return an object that TELLS THE TRUTH about what the caller asked for --
        # this run is not in the hub -- rather than the caller's instance still claiming `is_deleted`
        # False, which a view would render as "still on your profile". `delete_list` returns the stale
        # instance here; that is the one place this deliberately improves on the precedent.
        #
        # NOT PERSISTED, and it must not be: `Model.save()` falls back to an INSERT when its UPDATE
        # matches no rows, so saving this would resurrect the row it is reporting gone.
        challenge.is_deleted = True
        challenge.deleted_at = challenge.deleted_at or timezone.now()
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
def assign(challenge, profile, key, contract, *, acknowledge_lock=False):
    """Put `contract` in the `key` slot, and complete the slot if the hunter has already finished it.

    Returns the saved slot. Every refusal happens before the first write.

    THE COMPLETED-CONTRACT BRANCH is the interesting half. Normally an already-completed contract is
    not selectable at all -- it would land the slot complete instantly. Two rules lift that, and they
    are checked in this order because the labels are not interchangeable:

    - the **importer** (**A-Z only**; first run only; and only for a completion earned after the hunter
      joined), and
    - the **hatch** (**both types**; any run; but only when supply for this slot is down to
      `HATCH_THRESHOLD`).

    THE TYPE AXIS IS STATED ON BOTH LINES ON PURPOSE. This listed only the first-run-versus-any-run
    distinction, which reads as "both apply to everything, they differ in when" -- and that was the
    description a reader of the write door would have trusted. On a Job Coverage run there is exactly ONE
    catch-up rule.

    `import` wins when both apply: it is the more specific rule and the one with a fairness date behind
    it, so it is the more honest label for what happened.

    A slot completed this way LOCKS immediately, like any other completed slot -- and that is why this
    function refuses to do it silently. `acknowledge_lock=False` raises `ConfirmationRequired` for any
    placement that would land the square complete, so the confirmation is a property of the WRITE rather
    than of whichever UI happened to call it.

    IN THE SERVICE, NOT THE CLIENT, for two reasons. A dialog is the right place to ASK, but a confirmation
    the server does not know about is one that a stale tab, a double-submit or a second client can skip --
    and this is the one action on a run that cannot be undone afterwards. And the picker already has to
    know which label applies (`catchup_reasons`), so the alternative was two places deciding when to warn.

    BOTH RULES, not just the importer. The owner asked for a confirmation on importing history; the hatch
    lands a square complete-and-locked by the same mechanism, so it gets the same stop. Narrower would mean
    explaining to a hunter why one irreversible click asked and the other did not.
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

    # ONE GAME, ONE SQUARE PER RUN, and it takes TWO clauses because a square can hold a game whose
    # `Contract` row no longer exists.
    #
    # THE LIVE FK is the main rule. A staff edit to `Contract.slug` breaks a snapshot match and leaves the
    # FK correct -- this module's header says so, and the point had been applied to detection but not here.
    # The cost was a jobs game already in one square being accepted into a second, because neither this
    # guard nor the pool's exclusion recognised it any more: a latent double payout (latent because
    # `xp_redeemed_at` still has no writer, and an earlier version of this comment said "was" rather than
    # "would be").
    #
    # `challengeslot_unique_contract` now keys on the same FK, so the database backs this up rather than
    # disagreeing with it. That mismatch used to be described here as unfixed; the migration that fixed it
    # is `0002_slot_uniqueness_keys_on_the_contract_fk`.
    #
    # THE DEAD-SNAPSHOT CLAUSE closes what moving the key opened, and it has to live here because no index
    # can express it. `contract` is `SET_NULL`, so deleting a `Contract` nulls the FK on every square that
    # held it while the snapshot survives -- and a partial unique does not constrain NULLs. The game then
    # comes BACK as a new row (staff re-create it, or `evaluate_contract_candidates._stage_contract` restages
    # it: its only guard is `Contract.objects.filter(igdb_id=...).exists()`, which the delete just freed, and
    # its slug is `slugify(name)`, so the new row usually lands on the same slug). With its `EarnedContract`
    # cascaded away the hunter no longer reads as having completed it, so nothing else refuses -- and the run
    # would quietly hold one game in two squares with two completions. The old slug-keyed constraint caught
    # this by accident, as an `IntegrityError`; a clean refusal is better than either.
    #
    # COMPARED ON THE SLUG because that is all a dead square has. Two limits, both stated rather than
    # discovered later: a genuinely DIFFERENT game that later takes the dead slug is refused too (wrong, but
    # it errs toward refusing, and only a staff rename plus a reuse produces it), and a dead square whose
    # game returns under a NEW slug is not caught at all. A frozen `igdb_id` would be the precise key for the
    # matched case, but `Contract.igdb_id` is null for admin/episodic contracts, so it would need this same
    # fallback underneath it and is not the complete answer it looks like.
    twin = locked.slots.exclude(pk=slot.pk).filter(
        Q(contract_id=contract.id)
        | Q(contract_id__isnull=True, contract_slug=contract.slug)
    )
    if twin.exists():
        raise ChallengeError('That game is already in another square of this run.')

    completed_via = None
    if _hunter_has_completed(profile, contract):
        completed_via = _why_a_completed_contract_is_allowed(profile, locked, key, contract)
        if completed_via is None:
            raise ChallengeError(
                'You have already finished that one. Pick a game you can still complete.'
            )

    if completed_via is not None and not acknowledge_lock:
        # AFTER every other check, so a confirmation is only ever asked for a placement that would
        # actually succeed. Asking first and refusing second would train a hunter to confirm a dialog that
        # sometimes does nothing.
        raise ConfirmationRequired(
            'You have already finished that one, so this square will be complete and locked straight '
            'away. Confirm to use it.',
            via=completed_via, contract_name=contract.name)

    # ASSIGNED AFTER THE RAISE, not before it. Nothing escaped when these four lines sat above -- no DB
    # write happens until `save()` and `@transaction.atomic` rolls back regardless -- but it left a loaded
    # gun: a future caller that caught `ConfirmationRequired` and then saved this same instance would
    # commit a completing placement nobody acknowledged, with `is_completed` still False.
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

    DELEGATED to `eligibility.fits_slot`, which is the same predicate the pool and the hatch's count use.
    An earlier version re-expressed the A-Z half in Python as
    `contract.name.upper().startswith(key.upper())`, which DISAGREES with the queryset's
    `name__istartswith`: both fold with UPPER, but Python's does FULL Unicode case mapping (an fi
    ligature becomes FI, a sharp s becomes SS) where libc's is per-character. So this accepted names the
    pool never held, and a contract the pool never holds is one `hatch_is_open` cannot count. One rule,
    one spelling. See `eligibility`'s module docstring for the measured cases.
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
    return EarnedContract.objects.filter(profile=profile, contract=contract).exists()


def catchup_reasons(profile, challenge, key, contracts):
    """{contract_id: COMPLETED_VIA_IMPORT | COMPLETED_VIA_HATCH} for the ones a rule lifts.

    Already-completed contracts are normally unselectable -- a slot filled with one would land complete
    instantly. Two rules lift that, and a contract absent from this mapping is one neither lifts.

    PRECEDENCE IS `import` OVER `hatch`, and the two labels are not interchangeable: `import` carries a
    fairness date (the completion post-dates the account), `hatch` carries an admission that our supply
    for this slot failed the hunter. A square records which one applied, and a hunter reading their own
    finished run is entitled to see it. So the ordering below is load-bearing: the importer pass writes
    first and the hatch pass uses `setdefault`. (Precedence only ever bites on A-Z, since that is the only
    type where both rules can apply at once.)

    THE BULK FORM IS THE REAL ONE. The picker needs this for a page of results at once, and the
    single-contract caller (`assign`) is the special case -- expressed that way round because the
    alternative is two spellings of a precedence rule, and the picker offering `hatch` where `assign`
    records `import` would mislabel a permanently locked, XP-bearing square. Costs are unchanged either
    way: `importer_is_available` is one query on an A-Z run and ZERO on a jobs run (the type check
    short-circuits before the count), `importable_dates` batches over the whole list, and `hatch_is_open` is
    one COUNT about one slot. It said `importable_ids`, which was renamed when the dates stopped being
    thrown away.

    NEVER MAP THIS OVER A WHOLE RUN. `hatch_is_open` is a COUNT per slot, so 26 slots is 26 counts; the
    docstring there says the same thing. One slot, when its picker opens.
    """
    return {cid: via for cid, (via, _) in catchup_offers(profile, challenge, key, contracts).items()}


def catchup_offers(profile, challenge, key, contracts):
    """{contract_id: (via, completed_at)} -- the labels AND the dates, computed once.

    THE DATE COMES BACK WITH THE LABEL because working it out is the expensive half and the picker needs
    both. `importable_dates` derives its set from `completion_dates` (five queries over `Trophy`,
    `EarnedTrophy` and `ProfileGame`) and then throws the dates away; the picker then asked for them again.
    Ten queries over exactly the tables the whale rule exists to protect, per panel open, where five do.

    `completed_at` is None for a `hatch` row, which displays no date -- the hatch is about our supply
    being thin, not about when the hunter did anything.
    """
    contracts = list(contracts)
    if not contracts:
        return {}

    offers = {}
    if importer_is_available(profile, challenge.challenge_type):
        joined_at = getattr(getattr(profile, 'user', None), 'date_joined', None)
        for contract_id, when in eligibility.importable_dates(profile, contracts, joined_at).items():
            offers[contract_id] = (COMPLETED_VIA_IMPORT, when)
    if eligibility.hatch_is_open(profile, challenge, key):
        for contract in contracts:
            offers.setdefault(contract.id, (COMPLETED_VIA_HATCH, None))
    return offers


def _why_a_completed_contract_is_allowed(profile, challenge, key, contract):
    """`COMPLETED_VIA_IMPORT`, `COMPLETED_VIA_HATCH`, or None if neither rule lifts the exclusion.

    One contract's worth of `catchup_reasons`, which is where the rule lives.
    """
    return catchup_reasons(profile, challenge, key, [contract]).get(contract.id)


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
def mark_slot_completed(slot, *, via=COMPLETED_VIA_LIVE):
    """Record that a filled slot's contract is finished. Idempotent. Returns True if anything changed.

    The write half of detection, which lives here rather than in the detector so that every path to a
    completed slot -- the sync hook, the nightly sweep, the importer, the hatch -- goes through one
    function and cannot disagree about what completing a slot entails.

    `completed_at` is NOW, deliberately, and there is no parameter to override it. The tempting
    alternative is the contract's reach timestamp, which sits right there in the subquery -- but that is
    itself a DETECTION stamp (see the module header on `*_reached_at`), so backdating to it would trade a
    date that is honestly "when we noticed" for one that merely looks like an achievement date. A square
    the nightly sweep backfills says tonight, and that is true.

    `completed_at` is OURS and is never read back from `EarnedContract`. That row gets DELETED when staff
    run `reconcile_contracts` over a contract whose derived membership stopped qualifying -- staff-run
    and never scheduled, because it deletes banked XP (`tests/engine/test_nightly.py` pins that it is
    not a cron step) -- and a run must survive catalogue bookkeeping the hunter never saw.
    """
    if slot.is_completed or not slot.is_filled:
        return False

    # `.first()` rather than `.get()`, for the reason `hide` gives: this runs from a sweep, and a row
    # removed by a `Profile` cascade or a shell must not turn a background job into an unhandled
    # `DoesNotExist`. A vanished slot is simply nothing to complete.
    locked_challenge = Challenge.objects.select_for_update().filter(pk=slot.challenge_id).first()
    fresh = ChallengeSlot.objects.select_for_update().filter(pk=slot.pk).first()
    if locked_challenge is None or fresh is None:
        return False
    # THE RUN'S precondition, re-asserted under the lock as well as the square's. Both detectors read
    # `challenge__is_complete=False` unlocked, so without this the rule "a finished run is never written
    # to again" rested entirely on a stale read -- and a finished run has a badge and a title granted
    # against it. Unreachable while the counters hold (`completed <= filled <= total` leaves no pending
    # square on a finished run), and reachable the moment anything writes `total_slots` or `is_complete`
    # out of band. A test in this suite does exactly that, which is the point.
    if locked_challenge.is_complete:
        return False
    # BOTH preconditions re-asserted on the row that came back, not just `is_completed`.
    #
    # WHY `clear` USUALLY WINS, stated correctly this time: it is not that the two take the same locks in
    # the same order -- that prevents a deadlock and says nothing about who commits first. It is that the
    # detector READ its slot with no lock held, so `clear` is never blocked during the window and lands
    # while the detector is still holding a stale object. The reverse interleaving is possible too, and
    # is harmless: `clear` then blocks on the Challenge lock and refuses with "that square is finished".
    #
    # Without re-asserting `is_filled` a detector would stamp a square the hunter had just emptied,
    # violating `challengeslot_completed_is_filled_dated_and_explained` -- an IntegrityError out of a
    # background sweep. Exactly the stale-pivot failure rule 2 names, and that
    # `game_list_service._lock_item` was written to close.
    if fresh.is_completed or not fresh.is_filled:
        return False

    fresh.is_completed = True
    fresh.completed_at = timezone.now()
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
    lands: if a FILLED slot row were ever added out of band (a shell -- the admin cannot, being fully
    read-only), `filled_count` would exceed `total_slots` and this UPDATE would raise on
    `challenge_filled_within_total` for every subsequent assign and completion.

    Clear is the way out, and that is not luck: it writes the slot row BEFORE recounting, so emptying
    any one filled, unfinished square brings the count back inside the bound. The run is only truly
    stuck if every filled square is already completed, since a completed square refuses to clear -- and
    then a repair command is the only route. An out-of-band EMPTY slot violates nothing.
    """
    # THE PREVIOUS COMPLETED COUNT, read before the aggregate overwrites it. It is authoritative: this
    # function is the only writer of the denormalized counters and it runs under the run's row lock.
    was_completed = challenge.completed_count
    counts = challenge.slots.aggregate(
        filled=models.Count('pk', filter=~models.Q(contract_slug='')),
        completed=models.Count('pk', filter=models.Q(is_completed=True)),
    )
    challenge.filled_count = counts['filled'] or 0
    challenge.completed_count = counts['completed'] or 0
    fields = ['filled_count', 'completed_count', 'updated_at']

    just_completed = not challenge.is_complete and challenge.completed_count >= challenge.total_slots
    if just_completed:
        challenge.is_complete = True
        challenge.completed_at = timezone.now()
        fields += ['is_complete', 'completed_at']

    challenge.save(update_fields=fields)

    # THE REWARDS HOOK LIVES HERE, and this line is the reason this function is worth having. TWO writes
    # can finish a run -- `mark_slot_completed` (detection) and `assign` (a catch-up placement lands its
    # square complete) -- and both go through this one aggregate, so this is the only place the flip
    # happens once. Hooking either call site alone would miss half of every hunter's completions.
    #
    # AFTER the save, so the title is granted against a run that is already stamped complete: the ordinal
    # comes from `completed_run_count`, which counts completed runs, and this run has to be one of them.
    # Imported inside the function because `rewards` imports back into this module for that count.
    if just_completed:
        from challenges.services import rewards
        rewards.on_run_completed(challenge)

    # AND THE NAV MARKER, which a completed square can ARM: on a jobs run, finishing a square creates XP
    # waiting to be claimed, and the pill on My Pursuit says so. The paying side clears it in `rewards`.
    #
    # BOTH HALVES OR NEITHER, which is the lesson `career_attention`'s own comment records about the claim
    # count: it shipped with only the spending half invalidated, so every page render re-cached a zero with
    # a fresh TTL moments before the reward landed, and the badge stayed dark for most of its TTL at exactly
    # the moment it had something to say.
    #
    # NEITHER CONDITION IS A GATE; BOTH ARE THERE FOR COST, and being exact matters because this comment
    # read as though the first were load-bearing. Arming is a cache DELETE, so running it for a run that owes
    # nothing could not light anything: the next read re-queries and `has_unclaimed_xp` answers on its own
    # terms. What they buy is the absence of pointless work -- and the second one was missing, which is worse
    # than it sounds. `_recount` runs on every `assign` and every `clear`, not only on a completion, so a
    # hunter filling a 25-square board was spending 25 Redis DELETEs and forcing 25 misses on a key that is
    # read on EVERY page of the site. That is precisely the cost `career_attention`'s "an attention marker
    # must never be the reason a page got slower" rule is about, paid by the marker itself.
    #
    # `> was_completed` is the honest test because a completed square can never be cleared, so the count only
    # ever rises: a rise is exactly "a square just became owed".
    if challenge.challenge_type == CHALLENGE_TYPE_JOBS and challenge.completed_count > was_completed:
        from challenges.services.rewards import _forget_xp_marker
        # RESOLVED NOW, NOT IN THE CALLBACK. `challenge` came from `select_for_update()` with no
        # `select_related('profile')`, so `challenge.profile` is a lazy fetch -- and written inside the lambda
        # it would have run AFTER the commit, as the argument, which is OUTSIDE `_forget_xp_marker`'s own
        # try/except. That defeats the "best effort, never fail the write that called it" guarantee the helper
        # is built on: a hiccup on that fetch raises from Django's commit-hook runner with the write already
        # durable, and the nightly sweep has no guard around `mark_slot_completed` at all. It also stops the
        # closure retaining the whole `Challenge` instance until commit.
        profile = challenge.profile
        transaction.on_commit(lambda: _forget_xp_marker(profile))

    return challenge


# ── detection ────────────────────────────────────────────────────────────────────────────────────

def pending_slots(profile=None):
    """Filled, uncompleted slots on runs that are still going. The ONE definition, for both detectors.

    Completion is matched on the LIVE FK, in the `Exists` subqueries below, never on `contract_slug` --
    see the module header. The snapshot is what a finished square SAYS; the FK is what it IS while the
    run is live. (This function takes no contract argument; an earlier docstring described one.)

    ONE WAY A SQUARE STAYS HERE FOREVER, documented because the header only covers the opposite risk: if
    the `Contract` row is deleted the FK goes NULL (`SET_NULL`) while the slug snapshot survives, so the
    square remains pending and the `Exists` can never match -- SQL NULL semantics, and the
    `EarnedContract` rows cascaded away with the contract regardless. Correct, but it blocks the run from
    ever finishing until the hunter clears that square. Contract deletion is rare; a repair command is
    the answer if it stops being.

    HIDDEN RUNS ARE INCLUDED, and that asymmetry is deliberate. A hunter cannot assign or clear on a
    hidden run (`_lock_challenge` refuses it), but the world keeps turning for it: hiding is a visibility
    act, not a pause, so progress earned while a run is out of sight is still theirs when they bring it
    back. A hidden run that completes this way does NOT reach the Hall of Fame, because
    `ChallengeQuerySet.completed()` is built on `visible()`.

    Bounded to ~51 rows in practice -- 26 letters plus 25 jobs -- which is why neither detector bothers
    scoping by contract. But bounded by SERVICE DISCIPLINE, not by construction, and the difference
    matters here: `challenge_one_active_per_type` is PARTIAL on the unfinished-and-visible predicate, so
    it does not cover the hidden rows this query deliberately includes. What actually holds the bound is
    `start` resuming the newest hidden run before creating a new one -- and even that is not absolute:
    the race documented on `start`'s own unhide branch lets its locked lookup return nothing while a
    hidden unfinished row exists, so the service can itself leave a second one behind. Write around the
    service and the count is unbounded, and this set grows with it.
    """
    qs = ChallengeSlot.objects.filter(challenge__is_complete=False, is_completed=False)
    qs = qs.exclude(contract_slug='')
    if profile is not None:
        qs = qs.filter(challenge__profile=profile)
    return qs


def detect_for_profile(profile):
    """Complete every pending slot whose contract this hunter has now finished. Returns how many.

    The sync hook's half. One query finds the slots that qualify -- no per-slot completion check, and no
    Python filtering of a profile-scoped queryset -- then each is written through `mark_slot_completed`
    so the locking and recount rules hold and the last square still finishes the run.

    The return value counts squares WRITTEN, not squares offered, and no test can tell that apart from
    `len(rows)` -- `test_the_count_matches_how_many_squares_...` says so rather than implying otherwise.
    The reason is NOT that the writer's guards are all in the query's filter (they are re-read per square,
    while the filter ran once): it is the counter invariant. `_recount` derives both counters from rows
    and sets `is_complete` only at `completed_count >= total_slots`, while the check constraints force
    `completed <= filled <= total` -- so every row still in hand is a filled, uncompleted square, and the
    run can only reach completion on the LAST one. Faking it out of band raises instead.

    DELIBERATELY UNSCOPED by contract, unlike `contract_service.check_profile_contracts` which narrows to
    the concepts a sync touched. Narrowing buys nothing here: the candidate set is already ~51 rows, so
    building a concept-to-contract map would cost more than it saves -- and being unscoped makes this a
    drift net too, catching a contract published after the hunter last touched the game.
    """
    qualifying = pending_slots(profile).filter(
        Exists(EarnedContract.objects.filter(profile=profile, contract_id=OuterRef('contract_id')))
    )
    return sum(1 for slot in list(qualifying) if mark_slot_completed(slot))


def completable_slots():
    """Every pending slot, site-wide, whose owner has finished its contract. The nightly sweep's half.

    One query for the whole site rather than a loop over profiles, and specifically NOT a loop over
    `Profile`: the sweep's cost should scale with how many runs are in flight, not with how many accounts
    exist. `process_contracts` has to walk candidate profiles because a contract's membership is derived
    and it cannot know who qualifies; this can, because a slot names its own contract.

    HALF AN INDEX NOW BACKS THIS PREDICATE, as a side effect rather than by design.
    `challengeslot_unique_contract` became a partial unique on `(challenge_id, contract_id) WHERE
    contract_id IS NOT NULL` in `0002_slot_uniqueness_keys_on_the_contract_fk`, which is the leading pair
    this scan wants; what it does not carry is `is_completed`. Still a decision rather than an omission:
    `ChallengeSlot` holds 26 rows for a letter run and 25 for a jobs run, so even a few thousand runs is a
    trivial scan once a night. If the table ever reaches six figures, the remaining half is a partial
    condition of `is_completed = false` on the columns that index already has.
    """
    return pending_slots().filter(
        Exists(EarnedContract.objects.filter(
            profile_id=OuterRef('challenge__profile_id'),
            contract_id=OuterRef('contract_id'),
        ))
    )


# ── reads the page needs ─────────────────────────────────────────────────────────────────────────

def _hidden_unfinished(profile, challenge_type):
    """The hidden, unfinished runs of this type, newest first. ONE predicate, two callers.

    `start` locks it and un-hides; `resumable_run` reads it so the page can say "Resume" instead of
    "Start". Shared rather than spelled twice, because the two disagreeing about what counts as
    resumable is a page that offers Start and then hands back somebody's half-finished run.

    `-updated_at` because more than one hidden unfinished run is reachable (the partial unique does not
    cover hidden rows, and `start`'s own race can leave a second behind). The newest is the one a hunter
    means; the others stay hidden and harmless.
    """
    return Challenge.objects.filter(
        profile=profile, challenge_type=challenge_type, is_complete=False, is_deleted=True,
    ).order_by('-updated_at')


def active_run(profile, challenge_type):
    """The run of this type currently in progress and visible, or None.

    Built on `ChallengeQuerySet.active()` rather than repeating its predicate, and `start` calls this
    rather than spelling it a third time. The argument `_hidden_unfinished` makes about sharing one
    predicate applies identically here, and an earlier version made it while having three copies.
    """
    return Challenge.objects.active().filter(
        profile=profile, challenge_type=challenge_type,
    ).first()


def visible_completed_count(profile, challenge_type):
    """Finished runs a reader can SEE. For display only.

    NOT `completed_run_count`, which deliberately counts hidden finished runs too -- correct for
    `_auto_name` (a run's ordinal should not shift because you hid one) and for
    `importer_is_available` (hiding must not reopen the catch-up). Wrong on a card, where it made the
    same page say "3 finished before" above a Finished list showing two.
    """
    return Challenge.objects.completed().filter(
        profile=profile, challenge_type=challenge_type,
    ).count()


def completed_counts(profile, challenge_type):
    """Both completed-run counts in ONE query: `{'all': n, 'visible': n}`.

    TWO QUESTIONS, ONE SCAN. `completed_run_count` counts hidden finished runs and
    `visible_completed_count` does not, and both are right for what they answer -- but My Challenges needs
    each once per card (the reward line's ordinal, and "N finished before"). Without this, adding the reward
    line would have taken the page from two COUNTs over this table to four. A filtered aggregate answers
    both in one pass, so the page kept its previous cost while gaining the line.

    THE TWO FUNCTIONS STAY, and this does not replace them: every other caller wants exactly one of the two
    and should not have to know the other exists. Nor does this become "the" reader -- a caller wanting one
    number should ask for that number, which is the difference between a shared query and a shared shape.

    THE FILTER IS `ChallengeQuerySet.VISIBLE`, not a copy of it, and that is not tidiness. That constant
    exists precisely so the predicate has ONE spelling, and its own comment records the bug from having two:
    add a term to `visible()` -- the candidate it names is a suspended profile -- and anonymous readers would
    respect it while signed-in ones would not. A literal `Q(is_deleted=False)` here would have made the Start
    card's "N finished before" diverge from the Finished list on that day, which is the exact failure
    `visible_completed_count` was added to fix.
    """
    return Challenge.objects.filter(
        profile=profile, challenge_type=challenge_type, is_complete=True,
    ).aggregate(
        all=models.Count('pk'),
        visible=models.Count('pk', filter=ChallengeQuerySet.VISIBLE),
    )


def resumable_run(profile, challenge_type):
    """The hidden run `start` would bring back, or None. Read-only: no lock, no write.

    This is what lets My Challenges label its button honestly. Without it the page shows "Start an A-Z
    Challenge" to a hunter who hid one with twelve squares filled, and pressing it hands that run back --
    correct, and a surprise. The hub still never shows hidden runs; this is the owner's own page.
    """
    return _hidden_unfinished(profile, challenge_type).first()
