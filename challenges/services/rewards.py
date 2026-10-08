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
from django.db.models import Q
from django.utils import timezone

from challenges.models import CHALLENGE_TYPE_CALENDAR, CHALLENGE_TYPE_JOBS, Challenge, ChallengeSlot
from challenges.services.challenge_service import ChallengeError
from challenges.models import CALENDAR_DAY_MARKERS
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

#: THE PLAT CALENDAR'S LADDER, by filled days. Five titles, climbed rather than won at the end, because a
#: run keyed on 365 days has no meaningful "first completion / second completion" shape -- finishing one
#: at all needs roughly 2,153 platinums, so an ordinal pair would be a reward almost nobody sees.
#:
#: THE 365 RUNG IS THE ULTIMATE, and there is exactly one. The plan wrote this as "a ladder plus one
#: ultimate PER VIEW" when the board drew three lenses; the collapse to one lens left a single completion
#: condition (`_recount_calendar`: "With one lens there is nothing to compare and nothing to record"), so
#: the top rung and the finish are the same event and share a title.
#:
#: THE NAMES END Champion THEN Legend, which is the shape A-Z and Job Coverage already use (owner,
#: 2026-10-06). Three types, one family.
#:
#: SAME UNIQUENESS REQUIREMENT AS `TITLE_NAMES` ABOVE, and it is a correctness one: `Title.name` is unique
#: site-wide and `UserTitle.unique_together` carries no `source_type`, so a name another system owns hands
#: back THAT row. Whether these five are free in PROD is a question only prod can answer, and was asked
#: there before they shipped.
CALENDAR_DAY_TITLES = {
    50: 'Calendar Marker',
    100: 'Calendar Keeper',
    200: 'Calendar Chronicler',
    300: 'Calendar Champion',
    365: 'Calendar Legend',
}

#: Types whose rewards are NOT a first/second-completion ordinal title, so their absence from
#: `TITLE_NAMES` is correct rather than an oversight.
#:
#: The Plat Calendar is the only one: it pays the day-marker ladder above instead.
#:
#: DECLARED RATHER THAN INFERRED, and the distinction is the whole point. A type missing from BOTH this
#: set and `TITLE_NAMES` grants nothing and reads as "rewards are broken" rather than as a missing dict
#: key -- which is exactly what `test_every_challenge_type_declares_its_reward_shape` exists to catch. A
#: new type has to say which it is; it cannot stay silent.
TYPES_WITHOUT_ORDINAL_TITLES = frozenset({CHALLENGE_TYPE_CALENDAR})


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

def _owed():
    """The predicate for "this square is owed job XP": finished, unpaid, job still in the catalogue.

    THREE QUERIES ASK IT and they must never drift: `redeemable_slots` is what the payout door pays,
    `has_unclaimed_xp` is what the nav pill lights for, and `owed_runs` is what the page and the seeder's
    diagnostic point AT. The docstrings of the first two said "the same predicate as the write, deliberately"
    while spelling the three terms out twice -- so the invariant was a claim held together by one test rather
    than by the code. Edit one copy and the pill sends hunters to a page with nothing to press, which is the one
    way an attention marker is worse than none.

    THE PROFILE-WIDE SCOPE IS HOISTED TOO, into `_owed_slots`, and for the same reason: `has_unclaimed_xp` and
    `owed_runs` both need "this hunter's jobs runs" on top of this predicate, and that pair was written out
    twice within twenty lines. It is the half that decides whether the pill and the page agree about the same
    hunter.

    A FUNCTION RATHER THAN A MODULE CONSTANT because the `key__in` term holds a queryset. A `Q` built once at
    import would share that queryset object across every caller for the life of the process; it is only ever
    compiled as a subquery, so nothing has been observed to go wrong, but a fresh one per call costs nothing
    and needs no reasoning about when a queryset caches its rows.

    THE CATALOGUE TERM IS NOT DEFENSIVE PADDING -- it is what stops the page lying. `redeem_all` skips a
    square whose `Job` was deleted (one staff deletion must not cost a hunter the other squares) and
    deliberately leaves it unstamped, so without this term that square stays "owed" forever: the page offers
    6,000 XP, Claim all pays nothing, and the "nothing owed is not an error" rule means it says nothing at
    all.
    """
    return Q(is_completed=True, xp_redeemed_at__isnull=True, key__in=Job.objects.values('slug'))


def redeemable_slots(challenge):
    """The squares of `challenge` that are owed job XP: completed, not yet paid, job still in the catalogue.

    A QUERYSET rather than a list, because its two callers want different things: one counts it, the other
    locks it.

    THE TERMS LIVE IN `_owed()`, which is also what the nav marker asks -- see there for why the
    catalogue term is load-bearing rather than defensive.

    Ordered by `position` for a stable read; slot-lock order cannot deadlock anyway, because every slot
    writer takes the run's row lock first.
    """
    if challenge.challenge_type != CHALLENGE_TYPE_JOBS:
        return ChallengeSlot.objects.none()
    return challenge.slots.filter(_owed()).order_by('position')


def has_unclaimed_xp(profile):
    """Does this hunter have Job XP waiting to be claimed anywhere? For the nav marker.

    THE PROFILE-WIDE FORM of `redeemable_slots`, and it has to be its own query rather than a loop over
    runs: the marker renders on every page of the site for every signed-in hunter, including the Django
    admin, so it gets one `EXISTS` and nothing else. `chalslot_unclaimed_xp_idx` is the partial index built
    for the slot half of it; whether the planner picks it up has not been checked with `EXPLAIN` on
    prod-shaped data, so treat that as the intent rather than as a measured plan.

    THE SAME PREDICATE AS THE WRITE, and the same profile scope as the page: both come from `_owed_slots`,
    which is `_owed()` plus "this hunter's jobs runs". A marker that lit for a square `redeem_all` will skip
    would send a hunter to a page with nothing to press, which is the one way an attention marker can be worse
    than no marker -- and a marker that disagreed with `owed_runs` would light with no run to point at.

    HIDDEN RUNS COUNT. Hiding is visibility, not a pause: detection keeps completing a hidden run's squares
    and the payout door still pays them, so the XP is owed and the marker says so.
    """
    return _owed_slots(profile).exists()


def _owed_slots(profile):
    """Every square of this hunter's that is owed job XP. The queryset both profile-wide readers start from.

    `_owed()` is the per-square predicate; this adds the two terms that make it about a HUNTER -- their runs,
    and only the jobs type. Written out in both callers first, which is the duplication `_owed()`'s own
    docstring argues against: the pill and the page disagreeing about one hunter is the failure, and a term
    edited in one place is how it happens.

    HIDDEN RUNS ARE IN. `ChallengeManager.get_queryset` deliberately does not filter `is_deleted`, and a join
    bypasses it anyway -- which is correct here: a hidden run's squares owe real XP and the payout door still
    pays them.

    THE TYPE TERM IS SCOPING, NOT PROTECTION, and a mutation run is what established that: dropping it changes
    nothing observable, because `_owed()`'s catalogue term (`key__in=Job.objects.values('slug')`) already
    excludes every A-Z square -- an A-Z key is a LETTER and can never be a job slug. It stays because it says
    what this queryset is about, and because it would become load-bearing the moment a third challenge type
    keyed its slots on something a job slug could collide with. Worth writing down rather than leaving as a
    guard somebody later trusts for a job it is not doing.
    """
    return ChallengeSlot.objects.filter(_owed(), challenge__profile=profile,
                                        challenge__challenge_type=CHALLENGE_TYPE_JOBS)


def owed_runs(profile):
    """Every run of this hunter's that owes job XP, newest first. ONE query, aggregated in the database.

    Returns `[{'challenge_id', 'challenge_type', 'owed', 'xp', 'is_hidden'}]`.

    NOT SCOPED TO A RUN, and that is the whole reason it exists. `pending_xp(run)` answers for one run, which
    is wrong for every surface that has to point a hunter AT the XP: My Challenges' card shows the ACTIVE run
    (or a resumable one, or none), and a FINISHED run with unclaimed squares is neither -- so a card can read
    "Start" while a run nobody is looking at owes 150,000 XP. The nav pill is profile-wide for the same reason,
    and the gap between the two is what made it look stuck.

    HIDDEN RUNS ARE INCLUDED AND FLAGGED. They owe real XP and the payout door still pays them, so leaving them
    out would make this disagree with the pill -- but My Challenges does not list them, so a caller that wants
    to send somebody somewhere needs to know which link is to a run they cannot otherwise reach.

    AGGREGATED IN THE DATABASE, per the per-user queryset rule: one `values().annotate(Count)` over the partial
    index, returning at most a handful of rows, rather than fetching slots and counting them in Python. A
    hunter's runs are bounded by how many they have finished, so this cannot grow with a library.

    ORDERED NEWEST-CREATED FIRST (`-challenge_id`), which is what a caller wanting "one run to link to"
    should use. It is NOT "the run they were last on" -- an earlier version of this line claimed that, and a
    hunter who resumed an older hidden run and filled squares there was last on the one that sorts second.
    Nothing tracks per-run activity, and adding a column for a tie-break nobody has asked for is not worth it.

    `.order_by` IS LOAD-BEARING, not cosmetic: `ChallengeSlot.Meta.ordering` is `['position', 'pk']`, and an
    inherited ordering is injected into the `GROUP BY` -- which would have grouped by slot and returned one row
    per square instead of one per run.
    """
    from django.db.models import Count

    rows = (_owed_slots(profile)
            .values('challenge_id', 'challenge__challenge_type', 'challenge__is_deleted')
            .annotate(owed=Count('id'))
            .order_by('-challenge_id'))
    return [{
        'challenge_id': row['challenge_id'],
        'challenge_type': row['challenge__challenge_type'],
        'owed': row['owed'],
        'xp': row['owed'] * CHALLENGE_SLOT_JOB_XP,
        'is_hidden': row['challenge__is_deleted'],
    } for row in rows]


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


def _eyebrow(count):
    """The ceremony's own line for a challenge payout. ONE SQUARE OR N, in the page's own noun.

    IT HAS TO TRAVEL WITH THE PAYLOAD. The player's default line counts `accepted`, which is a list of
    CONTRACT slugs -- so a challenge payout, which has none, would have been announced as "Contract claimed"
    over a square's reward. The player keeps that default for its original caller and reads this when a
    caller sends one.

    `first_claim` still wins in the player ("Your Pursuit begins"), and should: a hunter's first job XP ever
    is the bigger fact whichever door paid it.
    """
    return ('%d squares claimed' % count) if count > 1 else 'Square claimed'


def _grant(grants, profile):
    """`grant_job_xp_bulk`, wrapped in the bookkeeping that primitive does not do.

    Returns `(granted_xp, ceremony_payload)`.

    THE CEREMONY IS BUILT HERE AND NOWHERE ELSE, because this is the only function inside the bracket. The
    payload's numbers are differences between a reading taken before the grant and one taken after, and the
    before reading cannot be reconstructed later: a level is a threshold, so any other payout landing in the
    gap (a contract claim in another tab, a sync) would be attributed to this square. That was tried, in the
    version of this feature that deferred the ceremony to the next Career visit, and it is what killed that
    design.

    It is the SAME builder the contract claim uses (`contract_service.ceremony_payload`), which is what makes
    "the same animation" structural rather than a resemblance. The only thing this path adds is the eyebrow.

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
    from trophies.services.contract_service import (_has_any_job_xp, _levels_snapshot,
                                                    _log_rank_milestones, _pursuer_level,
                                                    ceremony_payload, grant_job_xp_bulk)

    # BOUNDED TO THE JOBS THIS WRITE PAYS -- at most 25, one per square -- never the hunter's library. Job
    # PK is its slug, which is why `pk` and `key` are interchangeable through this file.
    job_by_id = {g['job'].pk: g['job'] for g in grants}
    first_claim = not _has_any_job_xp(profile)
    pre = _levels_snapshot(profile, job_by_id.keys())
    before = _pursuer_level(profile)
    granted = grant_job_xp_bulk(profile, grants, first_claim=first_claim)
    # ONE READING AFTER, SHARED. The milestone bracket and the ceremony both need the post-grant Pursuer
    # level, and it was being read twice -- two extra queries inside the run, slot and ProfileJobXP row
    # locks, for a value that cannot change between them (same transaction, nothing written in between).
    after = _pursuer_level(profile)
    _log_rank_milestones(profile, before, after, first_claim)
    ceremony = ceremony_payload(profile, job_by_id, pre, before, total=granted, accepted=[],
                                first_claim=first_claim, eyebrow=_eyebrow(len(grants)),
                                post_pursuer=after)

    # SETTLE THE NAV MARKER, on commit. The pill says "Job XP waiting to be claimed", and this is the only
    # thing that can spend it -- so leaving it lit after a claim would be the feature contradicting itself
    # on screen, in the one moment the hunter is looking straight at it.
    #
    # `on_commit` rather than inline, for the reason `contract_service.claim` writes down at its own
    # invalidation: a concurrent render between the cache clear and the commit would re-cache the PRE-claim
    # answer with a fresh TTL, and the pill would survive the claim by up to the full five minutes.
    transaction.on_commit(lambda: _forget_xp_marker(profile))
    return granted, ceremony


def _forget_xp_marker(profile):
    """Drop the cached "XP waiting" answer so the nav picks up the claim.

    BEST-EFFORT, exactly like `contract_service._forget_nav_badge`: a cache that is down must never fail the
    payout that called it. The marker is an ornament on somebody else's transaction.
    """
    try:
        from trophies.services.career_attention import forget_challenge_xp
        forget_challenge_xp(profile)
    except Exception:
        logger.debug('Could not clear the challenge-XP nav marker', exc_info=True)


def _job_for(slot):
    """The `Job` a jobs-run square names, or None if the catalogue no longer has it.

    A square's `key` IS the job slug, so this is one indexed lookup. It returns None rather than raising
    because a deleted `Job` is a real state (the square survives the deletion, documented as the hazard
    that makes a run unwinnable) and the caller's refusal message should say something true about it.
    """
    return Job.objects.filter(slug=slot.key).first()


@transaction.atomic
def redeem_slot(challenge, profile, key):
    """Pay one completed Job Coverage square's XP to its job. Returns `(slot, amount, ceremony)`.

    THE THIRD VALUE IS THE ANIMATION, and it is a return value rather than something the view rebuilds
    because it can only be built in here (see `_grant`). A THREE-TUPLE rather than a dict or a hidden
    attribute, deliberately: every caller that USES the result breaks loudly at the unpack the moment this
    changes, which for a function that writes to an append-only XP ledger is the failure mode to want. (Not
    every caller: `seed_challenge_demo` discards the return and was untouched by the change. An earlier
    version of this said "every existing caller", which claimed a guarantee the shape does not give.)

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

    granted, ceremony = _grant([{
        'job': job,
        'amount': CHALLENGE_SLOT_JOB_XP,
        'source': XP_SOURCE,
        'source_id': slot.pk,
    }], profile)
    return slot, granted, ceremony


@transaction.atomic
def redeem_all(challenge, profile):
    """Pay every square `challenge` owes, in one transaction. Returns `(slots, amount, ceremony)`.

    ONE TRANSACTION AND ONE GRANT CALL, not a loop over `redeem_slot`, for two reasons. A loop would take
    and release the `ProfileJobXP` lock once per square, which is the shape that deadlocks against a
    concurrent contract claim; and `grant_job_xp_bulk` collapses several grants into one levelling pass
    and one `recompute_career_standing`, so a 25-square payout costs what one costs. This is the same
    trade `contract_service.accept_contracts_bulk` makes, lock ordering included.

    NOT AN ERROR WHEN THERE IS NOTHING OWED. A hunter pressing "Claim all" twice has not done anything
    wrong, and the second press is the one that would otherwise be a refusal for a state they cannot see.
    Returns `([], 0, None)` -- and `None` rather than an empty payload, because the ceremony is a thing that
    either happened or did not. The player's own guard is `!payload.jobs.length`, so either would be silent;
    `None` is the one a reader cannot mistake for a claim that paid nothing.
    """
    locked = _lock_run(challenge, profile)

    slots = list(redeemable_slots(locked).select_for_update())
    if not slots:
        return [], 0, None

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
        return [], 0, None

    # ONE FIELD, and not `updated_at`: `ChallengeSlot` does not have that column. Worth stating because
    # the sibling `ProfileJobXP` write in `contract_service` DOES set its own `updated_at` by hand right
    # before its `bulk_update`, with a comment that bulk_update never fires `auto_now` -- true there, and
    # irrelevant here for a different reason.
    ChallengeSlot.objects.bulk_update(paid, ['xp_redeemed_at'])

    granted, ceremony = _grant(grants, profile)
    return paid, granted, ceremony


# ── what the page says it is worth ──────────────────────────────────────────────────────────────────

def summary(challenge):
    """Everything a reward surface needs, in one shape and a bounded number of queries.

    ONE READER FOR THREE SURFACES -- the run page's panel, the row list inside it, and the Start card's
    "what this is worth" line -- because the alternative is three places deciding independently what a
    square pays, and they would disagree the first time the figure moved.

    `rows` IS EVERY FINISHED SQUARE, paid or not, and that is a correction rather than a preference. Owed-only
    rows meant claiming a square DELETED its row, so the "Claimed" state could never render and its CSS was
    dead the day it was written. The panel is a ledger of the run's finished squares, which is what lets a
    claim FLIP a row rather than remove it.

    IT IS NOT THE CONTRACT CARD'S "both states rendered, one revealed" PATTERN, which this docstring used to
    claim and the markup never did. The template is TWO LOOPS over `rows`, split on `is_paid`: an unpaid row
    renders in the open list and a paid one inside the disclosure, so a row appears in exactly one place and
    there is nothing to reveal. (This sentence used to describe an if/elif chain on `claimable`/`is_paid`, which
    is the shape the split REPLACED -- and the open loop's paid arm was deleted as unreachable in the same
    change.) The panel re-renders server-side after a claim instead of flipping a class, which is why the
    acknowledgement had to be added by hand rather than inherited.

    THREE STATES PER ROW, because a square has three: `is_paid` (done), `claimable` (a button), and neither
    (its `Job` left the catalogue, so nothing can ever pay it -- shown without a button rather than hidden,
    since a finished square vanishing from its own ledger is worse than one that cannot be claimed). Exactly
    one renders; they are not three states of one element. `claimable` is exactly `redeemable_slots`, so the
    button and the write agree by construction.

    Each row carries its job ATOM (icon, discipline colour, name) rather than a slug, because
    `_job_chip.html` wants an atom and rebuilding one in the template is how two spellings of a job appear on
    one page.

    A-Z gets `per_square = 0` and no rows, and that is the honest answer rather than an empty special case:
    an A-Z run pays a title, which `title_*` carries for both types.

    QUERY COST is FOUR on a jobs run and ONE on an A-Z one, and the first version of this paragraph got
    every clause of that wrong. The four: the title ordinal's COUNT, `key_atoms`, the claimable keys, and the
    finished rows. The paid total is a Python sum over rows already in hand, not an aggregate. None of them
    touches trophy data or the hunter's library -- all are bounded by the run (<= 25 rows) -- which is the
    property that matters, and it is also why the honest figure is worth stating rather than a smaller one.
    """
    from challenges.services.slot_render import key_atoms, label_for_key

    ordinal = completion_ordinal(challenge)
    # WHAT THE NEXT COMPLETION EARNS, not what this one did, when the run is unfinished: a hunter looking at
    # a run in progress is asking what finishing it gets them. `completion_ordinal` returns 0 for an
    # unfinished run, so the count of finished runs plus one is the ordinal it is playing for.
    if ordinal == 0:
        from challenges.services.challenge_service import completed_run_count
        prospective = completed_run_count(challenge.profile, challenge.challenge_type) + 1
    else:
        prospective = ordinal

    atoms = key_atoms(challenge)
    is_jobs = challenge.challenge_type == CHALLENGE_TYPE_JOBS
    # TWO READS, not one per row: the set of claimable keys, then the finished squares. `values_list` on the
    # first because only the keys are wanted, and a set membership test per row costs nothing.
    claimable = set(redeemable_slots(challenge).values_list('key', flat=True))
    finished = (challenge.slots.filter(is_completed=True).order_by('position')
                if is_jobs else ChallengeSlot.objects.none())
    rows = [{
        'key': slot.key,
        'label': atoms[slot.key]['name'] if slot.key in atoms else label_for_key(slot.key),
        'job': atoms.get(slot.key),
        'game_name': slot.contract_name,
        'xp': CHALLENGE_SLOT_JOB_XP,
        'is_paid': slot.xp_redeemed_at is not None,
        'claimable': slot.key in claimable,
    } for slot in finished]

    paid_rows = [row for row in rows if row['is_paid']]

    return {
        'per_square': CHALLENGE_SLOT_JOB_XP if is_jobs else 0,
        'pending_xp': len(claimable) * CHALLENGE_SLOT_JOB_XP,
        'claimable_count': len(claimable),
        'paid_xp': len(paid_rows) * CHALLENGE_SLOT_JOB_XP,
        # THE LEDGER SPLIT, computed here because the template must not decide what a hunter can act on.
        # `paid_count` labels the disclosure the paid rows collapse behind: 25 finished rows above the board
        # was ~1,250px on desktop and ~2,300px at 375px (the rows wrap), all of it before the thing the page
        # is about -- and on a finished run, the page most likely to be read by somebody else, every one of
        # them is inert. The claimable rows stay out in the open because they are the ones with a button.
        'paid_count': len(paid_rows),
        # AND THE OPEN LIST'S OWN COUNT, so the template can decide whether to render a `<ul>` at all. Gating
        # it on `rows` was wrong once everything is paid: every row moved into the disclosure and an EMPTY
        # `<ul role="list">` was left behind above it, with its own top margin. A template cannot subtract, so
        # the figure belongs here.
        'unpaid_count': len(rows) - len(paid_rows),
        # WHAT A WHOLE RUN PAYS, so the panel can answer "what is this worth" without the headline changing
        # what it measures part-way through a run (owner, 2026-09-30). `total_slots` is the run's own length,
        # so this is right for either type and does not hardcode 25.
        'full_xp': (CHALLENGE_SLOT_JOB_XP * challenge.total_slots) if is_jobs else 0,
        'rows': rows,
        # The title this run has earned, or is playing for. `title_earned` is what distinguishes them, so
        # the template never has to infer it from `is_complete` and get the tense wrong.
        'title_name': title_for(challenge.challenge_type, prospective),
        'title_earned': challenge.is_complete and ordinal > 0,
    }


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

    Ties on `completed_at` count both, so two runs stamped in the same instant BOTH read 2 -- which means
    ordinal 1 is unreachable for that profile and the earlier run's hero renders with no chip. An earlier
    version of this paragraph called that "the safe direction: it can grant a title early, never make one
    unreachable", which is exactly backwards about the tie case. It stays `lte` rather than `lt` because the
    run being asked about must count itself, and the tie needs two separate `timezone.now()` calls to land on
    the same microsecond -- so this is a documented sharp edge, not a live bug.

    Returns 0 for a run that is not complete, so callers that ask too early grant nothing.
    """
    if not challenge.is_complete or challenge.completed_at is None:
        return 0
    return Challenge.objects.filter(
        profile_id=challenge.profile_id, challenge_type=challenge.challenge_type,
        is_complete=True, completed_at__isnull=False, completed_at__lte=challenge.completed_at,
    ).count()


def granted_titles_for(challenges):
    """`{challenge_id: title name}` for runs whose completion title was actually granted. One query.

    IT READS THE GRANT, NOT THE ORDINAL, and that is the whole point of the function existing rather than
    the browse page calling `title_for(completion_ordinal(run))` per row. Two reasons, and the second is the
    one that matters:

    - COST. `completion_ordinal` is a COUNT per run, so a 24-entry page would be 24 extra queries to
      recompute something already written down.
    - TRUTH. This file's rule 4 contains a title-write failure rather than letting it fail the completion,
      and `grant_completion_title` also declines to re-point a row another system already holds under the
      same name -- it logs and leaves it. In both cases the run is finished and the hunter holds no title
      from us. An ordinal recomputed on the page cannot know that, so it would show a prestige chip for a
      title the hunter does not have, on the one page built to celebrate it. Reading the row means the chip
      is absent exactly when the title is, and appears the moment a backfill grants it.

    Runs with no granted title are simply absent from the map, so a caller's `.get(pk)` is the None case
    without a second sentinel. Third and later completions earn nothing and so are legitimately absent.

    MATCHED PER RUN, not by two independent `__in` clauses, and that is the third attempt at this guard.
    The weak spot it protects: `source_id` is a bare `PositiveIntegerField` with no FK and
    `(source_type, source_id)` carries no unique constraint, so the column is a convention rather than a key.

    - A `profile_id__in={...}` set was the second attempt and it does NOT make a cross-profile read
      impossible, though its comment said so. The set is page-wide, so a stray row whose `profile_id` is
      *any* profile on the page passes both clauses: two hunters with finished runs on the same page, plus a
      data migration writing hunter B's `UserTitle` against hunter A's run id, and A's hero shows B's title.
      The set still goes in the query -- it keeps the index useful -- but the pairing is what decides, and
      that has to happen against `(this run's profile, this run's id)`.
    - ASCENDING `earned_at`, because `dict()` is LAST-WINS. The second attempt ordered `-earned_at`
      descending and its comment claimed "the most recent grant wins" -- exactly inverted: descending puts
      the newest row first and the oldest last, so the oldest won. Deterministically, and forever, since
      `earned_at` is `auto_now_add`. Ascending is what the stated intent needs. Two rows for one run is not
      reachable through `grant_completion_title` (one row per call), but it is through the shell backfill
      this file advertises if a `completed_at` is ever edited, and a backfill means the later row.

    ONE PASS OVER `challenges`, which is what makes it safe for any iterable including a generator. An
    earlier version read the argument twice (once for the ids, once for the profiles) and guarded that with a
    `list(...)`; building `owner_of` takes both values in a single comprehension, so the second pass -- and
    the `list()` -- are gone rather than defended. A mutation run is what surfaced the dead call: deleting it
    broke nothing.

    The hazard is worth naming even though it is now structurally absent, because its failure mode was
    SILENT. A consumed generator yields an empty map with a non-empty id set, so every prestige chip
    disappears with no error. `test_the_title_lookup_survives_a_one_shot_iterable` fails if a second
    `for c in challenges` ever returns.
    """
    #: {run id: the profile that run belongs to} -- the pairing the match is made against, built in ONE pass.
    owner_of = {c.pk: c.profile_id for c in challenges}
    if not owner_of:
        return {}
    rows = (
        UserTitle.objects.filter(
            source_type=TITLE_SOURCE, source_id__in=owner_of.keys(),
            profile_id__in=set(owner_of.values()),
        )
        .order_by('earned_at', 'pk')
        .values_list('source_id', 'profile_id', 'title__name')
    )
    return {run_id: name for run_id, profile_id, name in rows
            if owner_of.get(run_id) == profile_id}


def grant_completion_title(challenge):
    """Grant the title `challenge`'s ordinal earns, if any. Returns the `UserTitle` or None.

    IDEMPOTENT BY `get_or_create` on `(profile, title)`, which is what `unique_together` enforces anyway --
    so a second call cannot duplicate a row, and a repair can call this freely.

    IDEMPOTENT IS NOT THE SAME AS CORRECT, though, and the gap cost a visible bug. `source_id` lives in
    `defaults`, so it is written on CREATE only: an existing row kept whatever run it was first granted for.
    `granted_titles_for` matches on that column to draw the Hall of Fame's title band, so a title whose
    original run was hard-deleted became unreadable -- held by the hunter, invisible on the page, with
    nothing logged. The `elif` below repairs exactly that case, and only that case.

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

    user_title, created = _ensure_title(challenge, name)
    if not created and user_title.source_type == TITLE_SOURCE and user_title.source_id != challenge.pk:
        # AN ORPHANED `source_id` IS RE-POINTED, and this is the repair the docstring promises rather than a
        # new behaviour. `source_id` is in `defaults`, so it is written on CREATE only -- an existing row of
        # our own kept whatever run it was first granted for, silently, and `granted_titles_for` matches on
        # exactly that column. So a title whose original run was hard-deleted could never be read again: the
        # hunter holds it, the Hall of Fame hero shows no chip, and nothing logs.
        #
        # ONLY WHEN THE OLD RUN IS GONE. A `source_id` naming a live Challenge is not stale -- that run
        # legitimately owns the title, and stealing it would move the chip to whichever run completed most
        # recently. The narrow trigger is what makes this a repair instead of a race.
        #
        # Reachable in production by a hard delete or a data migration, and reachable constantly in
        # development: `seed_challenge_demo --reset` deletes its runs and (until this was found) left their
        # titles behind, so every reseed orphaned one. That is how it surfaced -- a seeded Job Coverage hero
        # with no title band, reported from the browser.
        already_named = (UserTitle.objects
                         .filter(profile_id=challenge.profile_id, source_type=TITLE_SOURCE,
                                 source_id=challenge.pk)
                         .exclude(pk=user_title.pk)
                         .exists())
        if already_named:
            # TWO OF OUR ROWS MUST NEVER NAME ONE RUN, and without this guard the repair created exactly
            # that. The path is reachable entirely through supported operations:
            #
            #   1. run #1 finishes -> ordinal 1 -> `A-Z Champion`  (source_id = 1)
            #   2. run #2 finishes -> ordinal 2 -> `A-Z Legend`    (source_id = 2)
            #   3. run #1 is hard-deleted -- the event this repair exists for
            #   4. the shell backfill this file advertises calls us for run #2. `completion_ordinal` now
            #      counts only run #2, so it reads 1 and asks for `A-Z Champion` -- whose row is orphaned,
            #      so the repair re-points Champion onto run #2 as well.
            #
            # Now `(Champion, 2)` and `(Legend, 2)` both exist, `granted_titles_for` collapses them to one
            # entry, and the hero for a hunter with ONE finished run reads "A-Z Legend". Declining is
            # correct: the run already has a title of ours, and which ordinal it should hold is a question
            # for whoever is repairing the data, not for a side effect of a grant.
            logger.warning('declining to re-point challenge title %r onto run %s for profile %s: that run '
                           'is already named by another of our titles. The ordinals for this profile need '
                           'a look.', name, challenge.pk, challenge.profile_id)
        elif not Challenge.objects.filter(pk=user_title.source_id).exists():
            # A CONDITIONAL UPDATE, not a read-modify-write. `get_or_create` ... `exists()` ... `save()`
            # takes no lock on the `UserTitle` row, so under READ COMMITTED two callers could both read the
            # stale `source_id`, both find the old run absent, and both write -- a lost update. Filtering on
            # the value being replaced makes it a compare-and-swap: the second writer matches zero rows and
            # says so. (Reachable only via the advertised backfill racing a live completion -- the live
            # paths cannot contend, because one-active-run-per-type plus distinct names per ordinal means two
            # completions never want the same `(profile, title)` row. Narrow, but the write was unserialised
            # and the comment claimed the narrowness made it safe.)
            moved = (UserTitle.objects
                     .filter(pk=user_title.pk, source_id=user_title.source_id)
                     .update(source_id=challenge.pk))
            if moved:
                logger.info('re-pointed challenge title %r for profile %s from deleted run %s to run %s',
                            name, challenge.profile_id, user_title.source_id, challenge.pk)
                user_title.source_id = challenge.pk
            else:
                logger.info('challenge title %r for profile %s was re-pointed concurrently; leaving it',
                            name, challenge.profile_id)
                user_title.refresh_from_db(fields=['source_id'])
    return user_title


# ── the completion hook ───────────────────────────────────────────────────────────────────────────

def _ensure_title(challenge, name):
    """`(UserTitle, created)` for `name` against this run, with the collision logged rather than celebrated.

    THE PART BOTH GRANT PATHS SHARE, and only that part. `UserTitle.unique_together` carries no
    `source_type`, so if a hunter already holds a title of this name from another system, `get_or_create`
    hands that row back and a naive caller reads a successful grant -- the failure
    `badge_adapters.grant_series_title` carries the scar from. The row is left exactly as it is (it is not
    ours to re-point) and the mismatch is logged as an error, because it means one of our names has been
    taken by something else.

    WHAT IT DELIBERATELY DOES NOT DO is the orphaned-`source_id` repair. That belongs to
    `grant_completion_title`, because its guard ("two of our rows must never name one run") is TRUE of
    ordinal titles and FALSE of the ladder: a Calendar run legitimately earns up to five, so "another of
    our titles already names this run" is the normal state there rather than a warning. Sharing the repair
    would have imported a premise that does not hold.
    """
    title, _ = Title.objects.get_or_create(name=name)
    user_title, created = UserTitle.objects.get_or_create(
        profile_id=challenge.profile_id, title=title,
        defaults={'source_type': TITLE_SOURCE, 'source_id': challenge.pk},
    )
    if not created and user_title.source_type != TITLE_SOURCE:
        logger.error('challenge title %r is already held by profile %s from source %r -- the challenge '
                     'grant for run %s did nothing. One of our title names collides with another system.',
                     name, challenge.profile_id, user_title.source_type, challenge.pk)
    return user_title, created


def next_calendar_rung(profile, done=0):
    """`(days, title_name)` for the next Calendar rung this hunter can still EARN, or None. One query.

    "NEXT" MEANS NEXT UNHELD, NOT NEXT ABOVE `done`. The ladder's titles belong to the hunter, not the run:
    `_ensure_title` is a `get_or_create` per profile, so a second Calendar run that passes 50 days grants
    nothing to somebody who already holds Calendar Marker. Advertising "Calendar Marker at 50 days" on that
    hunter's Start card would promise a title they own. So this skips every rung already held and every
    rung `done` has passed, and answers None when the whole ladder is theirs.
    """
    held = set(
        UserTitle.objects.filter(profile=profile, title__name__in=CALENDAR_DAY_TITLES.values())
        .values_list('title__name', flat=True)
    )
    for days in CALENDAR_DAY_MARKERS:
        name = CALENDAR_DAY_TITLES[days]
        if days > done and name not in held:
            return days, name
    return None


def grant_day_markers(challenge):
    """Grant every day-marker title `challenge`'s filled count has reached. Returns the names granted NOW.

    CLIMBED, NOT WON. Unlike the ordinal titles this runs on every recount rather than only on completion,
    because the ladder's whole point is that it moves from a hunter's first platinum -- 50 days is about
    54 platinums, where finishing is about 2,153.

    ASCENDING, AND IT IS LOAD-BEARING. `granted_titles_for` picks a run's title with an ascending
    `(earned_at, pk)` ordering and last-wins, so the highest rung must be the LAST row written -- which is
    what puts "Calendar Legend" rather than "Calendar Marker" on a finished run's Hall of Fame plaque. A
    backfill grants several in one call with `earned_at` values a microsecond apart, so `pk` is what
    actually breaks the tie, and `pk` follows insertion order. Iterating the rungs high-to-low would
    invert the plaque silently.

    IDEMPOTENT, which a ladder needs far more than an ordinal does: this runs on every sync and every
    nightly sweep for every Calendar run, so the second call and the ten-thousandth must do nothing.
    `get_or_create` on `(profile, title)` is the guarantee, and it is the same column pair
    `unique_together` enforces -- so a race writes one row and the loser reads it back.

    IT RETURNS ONLY WHAT IT GRANTED THIS CALL, so a caller can tell "nothing to do" from "granted three"
    without a second query. Nothing uses that yet; the opening ceremony is the reader it is shaped for.
    """
    reached = [days for days in CALENDAR_DAY_MARKERS if challenge.filled_count >= days]
    if not reached:
        return []

    granted = []
    for days in reached:
        name = CALENDAR_DAY_TITLES[days]
        _user_title, created = _ensure_title(challenge, name)
        if created:
            granted.append(name)
    return granted


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
