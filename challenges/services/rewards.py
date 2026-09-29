"""What a finished Challenge pays, and the only thing that writes it.

FOUR RULES, and the first three are the whole reason this file is separate from `challenge_service`.

1. **A payout is guarded by a ROW LOCK, not by statement order.** The job-XP ledger is APPEND-ONLY --
   rows are never rewritten and the only honest reversal is a negating row -- so a double-pay cannot be
   undone, only offset. The property that prevents one is: stamp and grant happen in ONE atomic block
   while holding the run's row lock, and every precondition is re-read on the locked row. Order within
   that block is not the safety property and an earlier version of this list said it was -- which taught
   the wrong lesson to whoever writes the next writer, because swapping the two statements changes
   nothing an audit could observe. `ChallengeSlot.xp_redeemed_at` is what turns a second press into a
   SENTENCE; `xpgrant_challenge_once_per_slot` is the second line, and it surfaces as an `IntegrityError`
   and therefore a 500, which is why the stamp exists at all.

   And the grant is BRACKETED, not bare. `accept_contracts_bulk` wraps the same primitive in a Pursuer
   level reading before and after, because `grant_job_xp_bulk` logs job-tier milestones and nothing else.
   Skipping that bracket is not a cosmetic omission: `ranks_crossed(old, new)` only ever returns ranks in
   `(old, new]`, so a rank crossed by an unbracketed redeem is never logged and can never be logged
   afterwards -- the next claim starts from the already-raised level. The Career hero reads those rows, so
   the loss shows up as permanently blank dates on rungs the hunter really did cross.

2. **A-Z pays no XP, ever** (owner, 2026-09-28). Not "not yet": a letter is not a job, so there is no job
   for the XP to land in, and inventing one would be a random payout into a system it does not belong to.
   A-Z pays a title and, later, a badge. This is enforced here rather than by a constraint because no
   table constraint can see a slot's challenge TYPE -- that lives one FK away.

3. **Nothing this file writes is ever unwound.** `challenge_service`'s rule 4 states it for the whole
   feature: a reward is paid against a square that can never be cleared, so the reward needs no reversal
   path. That is only true while a completed square stays locked; if that ever changes, every function
   here becomes wrong at the same moment.

4. **A reward failing must not cost the hunter the thing they earned.** `on_run_completed` contains its
   own failures, because the run completing is the FACT and the title is derived from it. The three paths
   that can finish a run fail in three different ways, which is worth stating precisely -- an earlier
   version of this claimed all three were detection loops that log and move on, and that was true of one:

   - the **sync hook** swallows per-profile (`token_keeper`), so a raise costs that hunter the rest of
     their squares on that sync;
   - the **nightly sweep** (`process_challenges`) has NO guard around `mark_slot_completed`, so a raise
     aborts the whole command for everybody;
   - **`assign`** is a user request, so a raise is a 500 on a placement that otherwise worked.

   Every one of those is worse than a missing title, and the widest of them is the sweep. The XP path is
   the opposite -- user-triggered, one square at a time -- and must raise so the client can say why.

WHY THE TITLES ARE NOT `BadgeSeries.title`: recorded on `UserTitle.SOURCE_CHOICES` before this file
existed. A challenge awards TWO titles per type (first completion and second) and that field is one
nullable FK, so a second would have rippled through `badge_adapters`, `sync_series_titles` and
`title_views`. These rows are ours, with `source_type='challenge'` and `source_id` = the Challenge id.
"""
import logging

from django.db import transaction
from django.utils import timezone

from challenges.models import CHALLENGE_TYPE_JOBS, Challenge, ChallengeSlot
from challenges.services.challenge_service import ChallengeError
from trophies.models import Job, Title, UserTitle
from trophies.util_modules.constants import CHALLENGE_SLOT_JOB_XP

logger = logging.getLogger(__name__)

#: `UserTitle.source_type` for every title this file grants. Already in that model's `SOURCE_CHOICES`.
TITLE_SOURCE = 'challenge'

#: `ContractXPGrant.source` for every XP row this file writes. TWO CONSTANTS FOR ONE STRING, deliberately,
#: because they are unrelated enums that happen to agree today. One name served both for a while, and the
#: hazard is asymmetric: renaming it for a TITLE reason would start writing grants whose `source` no longer
#: matches `xpgrant_challenge_once_per_slot`'s `condition=Q(source='challenge')` -- silently disarming the
#: partial unique index that is the last thing standing between a bug and unbounded double-pay.
XP_SOURCE = 'challenge'

#: The two titles each type awards, by completion ordinal. FIRST completion and SECOND, per type, which
#: is `completed_run_count` AFTER the run was stamped complete -- so 1 and 2, not 0 and 1.
#:
#: THE NAMES MUST BE UNIQUE ACROSS THE WHOLE `Title` TABLE, not just across this dict, and that is a
#: correctness requirement rather than a style one: `UserTitle.unique_together` is `(profile, title)` with
#: NO `source_type`, so `Title.objects.get_or_create(name=...)` against a name some other system already
#: owns hands back THAT row, and the grant silently attaches a hunter to a badge title they did not earn.
#: `badge_adapters.grant_series_title` carries the scar from the one time this happened.
#: `test_challenge_rewards` proves the grant NOTICES a collision rather than reporting a false success;
#: whether these names are free in PROD data is a question only prod can answer, and was asked there.
TITLE_NAMES = {
    'az': {1: 'A-Z Champion', 2: 'A-Z Legend'},
    'jobs': {1: 'Job Challenge Champion', 2: 'Job Challenge Legend'},
}


class RewardError(ChallengeError):
    """A refusal the caller is expected to show the hunter. The message is user-facing.

    A SUBCLASS, and the first version of this file got that wrong in a way that would have 500d every
    refusal. It declared its own base and justified it with a circular import that does not exist:
    `challenge_service` imports this module inside `_recount`'s body, not at module scope, so importing
    `ChallengeError` here is clean in either load order.

    The cost of the invented constraint was concrete. Every handler in `challenges/views.py` catches
    `ChallengeError` and nothing else, so a sibling exception would have sailed past all of them the
    moment a redeem door was written -- turning "That square has already been redeemed" into a 500.
    Subclassing means the existing handlers cover this module for free.
    """


def _lock_run(challenge, profile):
    """Take `challenge`'s row lock and re-assert who owns it. Returns the locked row.

    NOT `challenge_service._lock_challenge`, which refuses a FINISHED run ("That challenge is finished.")
    and a hidden one. Both refusals are right for an edit and wrong for a payout: the normal case here is
    a hunter redeeming squares on a run they have just finished, and hiding is visibility rather than a
    pause -- detection keeps completing a hidden run's squares, so refusing to pay one would be
    inconsistent with the rest of the feature.

    THE LOCK ITSELF IS NOT OPTIONAL, though, and an earlier draft of this file argued that it was. The
    service states the invariant plainly: no slot is written outside its challenge's row lock, which is
    what makes `_recount`'s aggregate safe against concurrent slot writes. A redeem does not move the
    counters `_recount` derives, so the invariant's REASON does not bite here -- but an invariant with a
    quiet exception in it is one nobody can rely on, and the cost of honouring it is one locked SELECT.
    It also puts this path in the documented order: Challenge -> ChallengeSlot -> ProfileJobXP.
    """
    locked = Challenge.objects.select_for_update().filter(pk=challenge.pk).first()
    if locked is None:
        raise RewardError('That challenge no longer exists.')
    if locked.profile_id != profile.pk:
        raise RewardError('That is not your challenge.')
    if locked.challenge_type != CHALLENGE_TYPE_JOBS:
        # The A-Z rule, as its own refusal rather than folded into "nothing to redeem": those are
        # different facts and a hunter is entitled to the real one.
        raise RewardError('Only the Job Coverage Challenge pays job XP.')
    return locked


# ── the job-XP redemption ─────────────────────────────────────────────────────────────────────────

def redeemable_slots(challenge):
    """The squares of `challenge` that are owed job XP: completed, not yet paid, job still in the catalogue.

    A QUERYSET rather than a list, because its two callers want different things: one counts it, the other
    locks it.

    THE CATALOGUE TERM IS NOT DEFENSIVE PADDING -- it is what stops the page lying. `redeem_all` skips a
    square whose `Job` was deleted (one staff deletion must not cost a hunter the other squares) and
    deliberately leaves it unstamped, so without this filter that square stays "owed" forever: the page
    offers 6,000 XP, Claim all pays nothing, and the "nothing owed is not an error" rule means it says
    nothing at all. Filtering here makes one predicate answer for both surfaces.

    Ordered by `position` for a stable read; slot-lock order cannot deadlock anyway, because every slot
    writer takes the run's row lock first.
    """
    if challenge.challenge_type != CHALLENGE_TYPE_JOBS:
        return ChallengeSlot.objects.none()
    return (challenge.slots
            .filter(is_completed=True, xp_redeemed_at__isnull=True, key__in=Job.objects.values('slug'))
            .order_by('position'))


def pending_xp(challenge):
    """What `challenge` currently owes, in XP. One COUNT, multiplied -- never a Python sum over rows.

    Every square pays the same flat figure, so the count IS the answer and the arithmetic is free.

    NO BRANCH ON `completed_via`, which is the one thing to be deliberate about: a square filled by the
    history importer or by the scarcity hatch pays exactly what a live one pays. The hatch is our supply
    gap rather than the hunter's shortcut, and the importer is a head start we offered. (The parity is a
    decision of this chunk. An earlier version of this docstring attributed it to the feature doc, which
    at the time said the opposite -- that nothing anywhere stated what a challenge was worth.)
    """
    return redeemable_slots(challenge).count() * CHALLENGE_SLOT_JOB_XP


def _grant(grants, profile):
    """`grant_job_xp_bulk`, wrapped in the bookkeeping that primitive does not do. Returns the XP granted.

    ONE HELPER SO NEITHER PATH CAN FORGET, which is exactly how the first version of this file lost data:
    both call sites called the primitive bare, and `grant_job_xp_bulk` logs JOB_TIER milestones only. A
    25-square payout is 50 job levels and therefore ~50 Pursuer levels, and `ranks_crossed` is
    `old < min_level <= new` -- so every rank the redeem crossed was unlogged AND unloggable, because the
    next claim reads the already-raised level. `accept_contracts_bulk` has always bracketed the same
    primitive this way; this is that envelope, not a new idea.

    `first_claim` IS DERIVED rather than defaulted, for the same reason. It is the onboarding flag, and a
    hunter whose first job XP ever comes from a challenge would otherwise lose it twice: false on these
    milestones, and false again on their real first contract claim, because by then `_has_any_job_xp` is
    true. Deriving it here is what `accept_contracts_bulk` does when its caller passes None.

    NO `multiplier`, deliberately and now on the record. A double-XP event does NOT scale challenge XP:
    the constant's whole legibility argument is "exactly two job levels a square", the figure is quoted to
    the hunter before they start, and a weekend that silently made it three would break the one promise the
    number makes. The ledger row therefore records `1.00`, which is the truth about what was paid.
    """
    from trophies.services.contract_service import (_has_any_job_xp, _log_rank_milestones,
                                                    _pursuer_level, grant_job_xp_bulk)

    first_claim = not _has_any_job_xp(profile)
    before = _pursuer_level(profile)
    granted = grant_job_xp_bulk(profile, grants, first_claim=first_claim)
    _log_rank_milestones(profile, before, _pursuer_level(profile), first_claim)
    return granted


def _job_for(slot):
    """The `Job` a jobs-run square names, or None if the catalogue no longer has it.

    A square's `key` IS the job slug, so this is one indexed lookup. It returns None rather than raising
    because a deleted `Job` is a real state (the square survives the deletion, documented as the hazard
    that makes a run unwinnable) and the caller's refusal message should say something true about it.
    """
    return Job.objects.filter(slug=slot.key).first()


@transaction.atomic
def redeem_slot(challenge, profile, key):
    """Pay one completed Job Coverage square's XP to its job. Returns `(slot, amount)`.

    ORDER OF OPERATIONS, and it is the point of the function:
      1. lock the run, then the slot row (`_lock_run` explains why not `_lock_challenge`);
      2. re-assert every precondition ON THE LOCKED ROW (rule 2 of the service: a precondition read
         before the lock is a precondition another request can invalidate);
      3. write the stamp;
      4. grant.
    A second request that arrives between 1 and 4 blocks on the lock, then reads `xp_redeemed_at` as set
    and refuses with a sentence. It never reaches the ledger, so it never trips the unique index -- which
    would surface as an `IntegrityError` and therefore a 500 rather than a message.

    `@transaction.atomic` HERE because `grant_job_xp_bulk` does not open one and says so: it locks
    `ProfileJobXP` rows and expects the caller to own the transaction. The lock order matches the one
    written down at the top of `challenge_service`: Challenge -> ChallengeSlot -> ProfileJobXP.
    """
    locked = _lock_run(challenge, profile)

    slot = locked.slots.select_for_update().filter(key=key).first()
    if slot is None:
        raise RewardError('That square is not part of this challenge.')
    if not slot.is_completed:
        raise RewardError('That square is not finished yet.')
    if slot.xp_redeemed_at is not None:
        raise RewardError('That square has already been redeemed.')

    job = _job_for(slot)
    if job is None:
        # The Job was deleted under a live run. Nothing here can invent a job to pay, and the square is
        # already unfillable-by-design in that state; say so rather than paying somebody else's job.
        raise RewardError('That job is no longer in the catalogue.')

    # `ChallengeSlot` has no `updated_at` -- only `Challenge` does -- so naming one here is a
    # `FieldDoesNotExist`, not a no-op. The stamp IS the slot's modification record.
    slot.xp_redeemed_at = timezone.now()
    slot.save(update_fields=['xp_redeemed_at'])

    granted = _grant([{
        'job': job,
        'amount': CHALLENGE_SLOT_JOB_XP,
        'source': XP_SOURCE,
        'source_id': slot.pk,
    }], profile)
    return slot, granted


@transaction.atomic
def redeem_all(challenge, profile):
    """Pay every square `challenge` owes, in one transaction. Returns `(slots, amount)`.

    ONE TRANSACTION AND ONE GRANT CALL, not a loop over `redeem_slot`, for two reasons. A loop would take
    and release the `ProfileJobXP` lock once per square, which is the shape that deadlocks against a
    concurrent contract claim; and `grant_job_xp_bulk` collapses several grants into one levelling pass
    and one `recompute_career_standing`, so a 25-square payout costs what one costs. This is the same
    trade `contract_service.accept_contracts_bulk` makes, lock ordering included.

    NOT AN ERROR WHEN THERE IS NOTHING OWED. A hunter pressing "Claim all" twice has not done anything
    wrong, and the second press is the one that would otherwise be a refusal for a state they cannot see.
    Returns `([], 0)`.
    """
    locked = _lock_run(challenge, profile)

    slots = list(redeemable_slots(locked).select_for_update())
    if not slots:
        return [], 0

    jobs = {job.slug: job for job in Job.objects.filter(slug__in=[s.key for s in slots])}
    now = timezone.now()
    grants, paid = [], []
    for slot in slots:
        job = jobs.get(slot.key)
        if job is None:
            # A RACE GUARD ONLY, and worth labelling as such: `redeemable_slots` already filters out a
            # square whose Job is gone, so this fires just for a deletion that lands between that filter
            # and this read. Skipped rather than failing the batch either way -- refusing to pay the other
            # squares because one job left the catalogue would punish the hunter for a staff edit. It is
            # deliberately not mutation-covered, because nothing can reach it deterministically.
            logger.warning('challenge slot %s names job %r, which no longer exists; not paid',
                           slot.pk, slot.key)
            continue
        slot.xp_redeemed_at = now
        paid.append(slot)
        grants.append({'job': job, 'amount': CHALLENGE_SLOT_JOB_XP,
                       'source': XP_SOURCE, 'source_id': slot.pk})

    if not grants:
        return [], 0

    # ONE FIELD, and not `updated_at`: `ChallengeSlot` does not have that column. Worth stating because
    # the sibling `ProfileJobXP` write in `contract_service` DOES set its own `updated_at` by hand right
    # before its `bulk_update`, with a comment that bulk_update never fires `auto_now` -- true there, and
    # irrelevant here for a different reason.
    ChallengeSlot.objects.bulk_update(paid, ['xp_redeemed_at'])

    granted = _grant(grants, profile)
    return paid, granted


# ── the completion titles ─────────────────────────────────────────────────────────────────────────

def title_for(challenge_type, ordinal):
    """The title name this completion earns, or None. Pure, so the page can ask before it happens."""
    return TITLE_NAMES.get(challenge_type, {}).get(ordinal)


def completion_ordinal(challenge):
    """WHICH completion this run is for its hunter: 1 for their first of the type, 2 for the second.

    A PROPERTY OF THE RUN, not a live count of the set, and that distinction is the whole function. The
    first version read `completed_run_count`, the current total -- which makes the title depend on WHEN it
    is granted rather than on which run earned it, and that breaks the recovery this file's rule 4 promises.
    Concretely: if the title write fails on run 1 (contained, logged), then once run 2 completes the count
    reads 2, so calling this again for run 1 says "Legend" and re-grants a row the hunter already has.
    `A-Z Champion` becomes unreachable by any code path. Counting only runs finished no later than this one
    makes a later call for run 1 still say 1, so a backfill actually backfills.

    Ties on `completed_at` count both, so two runs stamped in the same instant would both read the higher
    ordinal. That is the safe direction: it can grant a title early, never make one unreachable.

    Returns 0 for a run that is not complete, so callers that ask too early grant nothing.
    """
    if not challenge.is_complete or challenge.completed_at is None:
        return 0
    return Challenge.objects.filter(
        profile_id=challenge.profile_id, challenge_type=challenge.challenge_type,
        is_complete=True, completed_at__isnull=False, completed_at__lte=challenge.completed_at,
    ).count()


def grant_completion_title(challenge):
    """Grant the title `challenge`'s ordinal earns, if any. Returns the `UserTitle` or None.

    IDEMPOTENT BY `get_or_create` on `(profile, title)`, which is what `unique_together` enforces anyway --
    so a second call cannot duplicate a row, and a repair can call this freely.

    IT ALSO NOTICES A COLLISION RATHER THAN CELEBRATING IT. `UserTitle.unique_together` carries no
    `source_type`, so if a hunter already holds a title of this name from another system, `get_or_create`
    hands that row back and every caller reads a successful grant. That is the failure
    `badge_adapters.grant_series_title` carries the scar from, and the only honest thing to do about it
    here is say so loudly: the row is left exactly as it is (it is not ours to re-point) and the mismatch
    is logged as an error, because it means one of our four names has been taken by something else.

    Third and later completions earn nothing and return None rather than raising: finishing another run is
    a good thing, it just does not mint a new title.
    """
    name = title_for(challenge.challenge_type, completion_ordinal(challenge))
    if name is None:
        return None

    title, _ = Title.objects.get_or_create(name=name)
    user_title, created = UserTitle.objects.get_or_create(
        profile_id=challenge.profile_id, title=title,
        defaults={'source_type': TITLE_SOURCE, 'source_id': challenge.pk},
    )
    if not created and user_title.source_type != TITLE_SOURCE:
        logger.error('challenge title %r is already held by profile %s from source %r -- the challenge '
                     'grant for run %s did nothing. One of the four challenge title names collides with '
                     'another system.', name, challenge.profile_id, user_title.source_type, challenge.pk)
    return user_title


# ── the completion hook ───────────────────────────────────────────────────────────────────────────

def on_run_completed(challenge):
    """Everything that happens the moment a run's last square lands. Returns the granted title or None.

    CALLED FROM `challenge_service._recount`, which is the ONE place `is_complete` flips -- and that
    matters because TWO different writes can finish a run: `mark_slot_completed` (detection) and `assign`
    (a catch-up placement lands its square complete). Hooking either call site alone would miss half the
    completions, and hooking both would be two places to keep in step.

    IT CONTAINS ITS OWN FAILURES, per rule 4 above: the run completing is the fact the hunter earned and
    the title is derived from it, so a raise here would cost far more than a missing title (see rule 4 for
    what each of the three completion paths loses).

    THE BACKFILL THAT MAKES THAT TRADE HONEST is `grant_completion_title(challenge)`, callable from a
    shell for any completed run. It only became true once the ordinal stopped being a live count -- see
    `completion_ordinal`, which is where that bug and its fix are written down.

    THE NOTIFICATION IS DEFERRED TO `on_commit`, not sent inline: it is a write to another app plus a
    cache invalidation, and sending it inside this transaction means telling a hunter about a completion
    that then rolls back. `contract_service.claim` defers for the same transactional reason, though what it
    defers is a cache delete rather than a send.

    `robust=True` because the callback runs AFTER the commit, on the caller's thread -- which for the sync
    hook is inside `token_keeper`'s per-profile guard and for the nightly sweep is inside nothing at all.
    An exception escaping an `on_commit` callback propagates out of the atomic block's exit, so without
    this the notification could cost a hunter the rest of their squares, or abort the whole sweep. That is
    the exact outcome rule 4 exists to prevent, reached through the one call the rule did not cover.
    """
    try:
        user_title = grant_completion_title(challenge)
    except Exception:
        logger.exception('challenge %s completed but its title could not be granted', challenge.pk)
        user_title = None

    transaction.on_commit(lambda: _notify_completion(challenge.pk), robust=True)
    return user_title


def _notify_completion(challenge_id):
    """Tell the hunter their run is finished. Runs AFTER commit, so it re-reads the row.

    RE-READ RATHER THAN CLOSED OVER, because an `on_commit` callback runs after the transaction that
    built the in-memory instance has gone: the row is the truth by then, and the instance might describe
    a state that was rolled back on a sibling path.

    WRITE-ONLY FOR NOW, knowingly. The notification inbox is parked -- `/notifications/` redirects home
    and `test_notifications_hidden` pins that every page and API path is gone -- so this row is produced
    and nothing reads it yet. That is the house position (a rebuild keeps the producers), and it means
    this must NOT grow a bell, a route or a poller to make itself visible.
    """
    from notifications.models import NotificationTemplate
    from notifications.services.notification_service import NotificationService
    from notifications.services.template_service import TemplateService

    challenge = Challenge.objects.select_related('profile__user').filter(pk=challenge_id).first()
    if challenge is None or not challenge.is_complete:
        return
    user = getattr(challenge.profile, 'user', None)
    if user is None:
        # `Notification.recipient` is a CustomUser, not a Profile, and a profile without one is a real
        # state in this codebase (`roadmap_note_service` guards the same way).
        return

    try:
        template = NotificationTemplate.objects.get(name='challenge_completed')
    except NotificationTemplate.DoesNotExist:
        logger.warning('challenge_completed template missing; skipping the notification. '
                       'Run loaddata notifications/fixtures/initial_templates.json to install it.')
        return

    # EVERY KEY THE TEMPLATE NAMES, because `TemplateService.render_template` uses `str.format` and
    # raises `KeyError` on a missing one -- which the guard below would swallow into a log line, so a
    # missing key reads as a silent non-delivery rather than as a bug.
    context = {'challenge_name': challenge.name, 'challenge_id': challenge.pk,
               'profile_name': challenge.profile.psn_username or ''}
    try:
        rendered = TemplateService.render_template(template, context)
        NotificationService.create_notification(
            recipient=user,
            notification_type=template.notification_type,
            title=rendered['title'],
            message=rendered['message'],
            icon=template.icon,
            action_url=rendered.get('action_url'),
            action_text=template.action_text,
            priority=template.priority,
            metadata=context,
            template=template,
        )
    except Exception:
        # A notification is the least important thing in this chain and the only one with a dependency on
        # another app's template data. It must never be able to fail a completion.
        logger.exception('could not notify the completion of challenge %s', challenge_id)
