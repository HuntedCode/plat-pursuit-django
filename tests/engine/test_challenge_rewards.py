"""What a finished Challenge pays: the job-XP redemption and the completion titles.

THIS IS THE HIGHEST-STAKES FILE IN THE FEATURE, and the reason is the ledger rather than the feature.
`ContractXPGrant` is append-only: rows are never rewritten and the only honest reversal is a negating row,
so a double-pay is permanent. `grant_job_xp` has no idempotency for null-`earned_contract` grants --
`xp-economy.md` warned about that in advance -- so this integration had to bring its own.

NOT THE LEDGER'S FIRST NON-CONTRACT SOURCE, which this docstring claimed until an audit caught it. It is
the first CONSTRAINED one. `seed_career_demo` has been writing `source='seed'` all along, and two separate
model comments were written specifically to retract that same wrong claim -- so this file managed to copy a
falsehood back out of the retraction that fixed it.

THREE GUARDS STAND BEHIND ONE PAYOUT, and each is tested for a different failure:

- `ChallengeSlot.xp_redeemed_at`, the stamp, which turns a second press into a SENTENCE;
- `xpgrant_challenge_once_per_slot`, the partial unique, which turns a bug past the stamp into an
  `IntegrityError` rather than free XP (pinned in `test_challenge_models`, mutation-checked there);
- `xpgrant_challenge_needs_source_id`, which is what makes the unique mean anything at all.

The double-redeem test here is MUTATION-VERIFIED: deleting the stamp check must fail it. A test that
passes with the guard removed would be the most expensive kind of false comfort in this codebase.

A-Z PAYS NO XP, EVER (owner, 2026-09-28) -- not "not yet". A letter is not a job, so there is nothing for
the XP to land in. That is a rule with its own tests, not an absence of tests.
"""
import pytest
from django.db.models import Sum
from django.utils import timezone

from challenges.models import CHALLENGE_TYPE_AZ, CHALLENGE_TYPE_JOBS
from challenges.services import challenge_service as svc
from challenges.services import rewards
from tests.factories import ConceptFactory, GameFactory, IGDBMatchFactory, ProfileFactory
from trophies.models import Contract, ContractXPGrant, EarnedContract, Job, ProfileJobXP, Title, UserTitle
from trophies.util_modules.constants import CHALLENGE_SLOT_JOB_XP

pytestmark = pytest.mark.django_db

_SEQ = {'n': 0}


def _member():
    return ProfileFactory(user_is_premium=True)


def _contract(name, *, jobs=()):
    _SEQ['n'] += 1
    c = Contract.objects.create(name=name, slug=f"{name.lower().replace(' ', '-')}-{_SEQ['n']}",
                                is_live=True, igdb_id=900_000 + _SEQ['n'])
    if jobs:
        c.jobs.set(jobs)
    concept = ConceptFactory(anchor_migration_completed_at=timezone.now())
    IGDBMatchFactory(concept=concept, igdb_id=c.igdb_id, status='accepted')
    GameFactory(concept=concept)
    return c


def _finished(profile, contract):
    return EarnedContract.objects.create(profile=profile, contract=contract, has_platinum=True,
                                         platinum_reached_at=timezone.now())


def _jobs_run_with_completed_squares(profile, count=1):
    """A Job Coverage run with `count` squares completed through the normal path.

    Completed by DETECTION rather than by writing `is_completed` directly, so the fixture exercises the
    same route a hunter's run takes. A square completed by hand would let a redeem test pass against a
    state the feature cannot actually produce.
    """
    challenge = svc.start(profile, CHALLENGE_TYPE_JOBS)
    jobs = list(Job.objects.order_by('slug')[:count])
    for job in jobs:
        contract = _contract(f'Game for {job.slug}', jobs=[job])
        svc.assign(challenge, profile, job.slug, contract)
        _finished(profile, contract)
    svc.detect_for_profile(profile)
    challenge.refresh_from_db()
    return challenge, jobs


def _az_run_completed(profile):
    """A COMPLETE A-Z run: every letter filled and finished. The only way to reach a title grant."""
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    for slot in challenge.slots.order_by('position'):
        contract = _contract(f'{slot.key}ll The Way Down')
        svc.assign(challenge, profile, slot.key, contract)
        _finished(profile, contract)
    svc.detect_for_profile(profile)
    challenge.refresh_from_db()
    return challenge


# ── the payout, and the guard that makes it once ─────────────────────────────────────────────────

def test_redeeming_a_square_pays_its_job_the_flat_figure():
    profile = _member()
    challenge, jobs = _jobs_run_with_completed_squares(profile)
    job = jobs[0]

    slot, granted, _ceremony = rewards.redeem_slot(challenge, profile, job.slug)

    assert granted == CHALLENGE_SLOT_JOB_XP
    assert slot.xp_redeemed_at is not None
    grant = ContractXPGrant.objects.get(profile=profile, job=job, source='challenge')
    assert grant.amount == CHALLENGE_SLOT_JOB_XP
    assert grant.source_id == slot.pk, 'the slot id IS the idempotency key'
    assert grant.earned_contract_id is None, 'a challenge grant is not a contract grant'


def test_a_second_redeem_of_the_same_square_pays_nothing():
    """THE TEST THIS FILE EXISTS FOR, and it is mutation-verified: comment out the `xp_redeemed_at`
    check in `redeem_slot` and this must fail.

    It asserts on the LEDGER, not on the return value. A guard that refused but had already written the
    row would satisfy a return-value assertion and still have double-paid permanently -- and the ledger
    is the thing that cannot be un-written.
    """
    profile = _member()
    challenge, jobs = _jobs_run_with_completed_squares(profile)
    job = jobs[0]
    rewards.redeem_slot(challenge, profile, job.slug)

    with pytest.raises(rewards.RewardError) as err:
        rewards.redeem_slot(challenge, profile, job.slug)
    assert 'already been redeemed' in str(err.value)

    assert ContractXPGrant.objects.filter(profile=profile, source='challenge').count() == 1
    assert ProfileJobXP.objects.get(profile=profile, job=job).total_xp == CHALLENGE_SLOT_JOB_XP


def test_the_cache_equals_the_sum_of_the_ledger_after_a_redeem():
    """`ProfileJobXP` is a denormalised `Sum(ContractXPGrant.amount)` and the ledger is the truth. If a
    redeem ever writes one without the other, `recompute_job_xp` silently reverts the hunter's XP."""
    profile = _member()
    challenge, jobs = _jobs_run_with_completed_squares(profile, count=3)
    rewards.redeem_all(challenge, profile)

    for job in jobs:
        ledger = (ContractXPGrant.objects.filter(profile=profile, job=job)
                  .aggregate(t=Sum('amount'))['t'] or 0)
        assert ProfileJobXP.objects.get(profile=profile, job=job).total_xp == ledger


def test_challenge_xp_survives_a_cache_rebuild():
    """`recompute_profile_job_xp` filters on `profile` with NO source filter, so challenge grants are
    rebuilt like any other. Pinned because a source filter added there later would silently delete this
    XP, and nothing else in the codebase would notice."""
    from trophies.services.contract_service import recompute_profile_job_xp

    profile = _member()
    challenge, jobs = _jobs_run_with_completed_squares(profile)
    rewards.redeem_slot(challenge, profile, jobs[0].slug)
    before = ProfileJobXP.objects.get(profile=profile, job=jobs[0]).total_xp

    recompute_profile_job_xp(profile)

    assert ProfileJobXP.objects.get(profile=profile, job=jobs[0]).total_xp == before


# ── claim all ────────────────────────────────────────────────────────────────────────────────────

def test_redeem_all_pays_every_owed_square_once():
    profile = _member()
    challenge, jobs = _jobs_run_with_completed_squares(profile, count=4)

    paid, granted, _ceremony = rewards.redeem_all(challenge, profile)

    assert len(paid) == 4
    assert granted == 4 * CHALLENGE_SLOT_JOB_XP
    assert ContractXPGrant.objects.filter(profile=profile, source='challenge').count() == 4
    assert {g.source_id for g in ContractXPGrant.objects.filter(source='challenge')} == {s.pk for s in paid}


def test_redeem_all_a_second_time_is_not_an_error_and_pays_nothing():
    """A hunter pressing Claim all twice has done nothing wrong, and the second press is for a state they
    cannot see. It returns empty rather than refusing."""
    profile = _member()
    challenge, _jobs = _jobs_run_with_completed_squares(profile, count=2)
    rewards.redeem_all(challenge, profile)

    paid, granted, _ceremony = rewards.redeem_all(challenge, profile)

    assert (paid, granted) == ([], 0)
    assert ContractXPGrant.objects.filter(profile=profile, source='challenge').count() == 2


def test_redeem_all_skips_a_square_whose_job_left_the_catalogue_and_pays_the_rest():
    """One staff deletion must not cost a hunter the other squares' XP. The skipped square keeps its
    unredeemed stamp, so it is not silently marked paid either."""
    profile = _member()
    challenge, jobs = _jobs_run_with_completed_squares(profile, count=3)
    orphan_slug = jobs[1].slug
    orphan_key_pk = challenge.slots.get(key=orphan_slug).pk
    Job.objects.filter(slug=orphan_slug).delete()

    paid, granted, _ceremony = rewards.redeem_all(challenge, profile)

    assert granted == 2 * CHALLENGE_SLOT_JOB_XP
    assert orphan_key_pk not in {s.pk for s in paid}
    assert challenge.slots.get(key=orphan_slug).xp_redeemed_at is None


# ── what is refused ──────────────────────────────────────────────────────────────────────────────

def test_an_a_z_run_never_pays_job_xp():
    """Rule, not omission (owner, 2026-09-28): a letter is not a job, so there is nothing to pay into."""
    profile = _member()
    challenge = _az_run_completed(profile)

    with pytest.raises(rewards.RewardError) as err:
        rewards.redeem_slot(challenge, profile, 'A')
    assert 'Job Coverage' in str(err.value)

    assert rewards.pending_xp(challenge) == 0
    assert rewards.redeemable_slots(challenge).count() == 0
    assert not ContractXPGrant.objects.filter(profile=profile, source='challenge').exists()


def test_an_unfinished_square_cannot_be_redeemed():
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_JOBS)
    job = Job.objects.order_by('slug').first()
    svc.assign(challenge, profile, job.slug, _contract('Not Done Yet', jobs=[job]))

    with pytest.raises(rewards.RewardError) as err:
        rewards.redeem_slot(challenge, profile, job.slug)
    assert 'not finished' in str(err.value)
    assert not ContractXPGrant.objects.filter(source='challenge').exists()


def test_somebody_elses_run_cannot_be_redeemed():
    """MATCHES THE MESSAGE, because a bare `raises` passed for three different refusals -- including ones
    that would fire for reasons having nothing to do with ownership.

    It also asserts the OWNER's square, which is the consequence a missing check actually has: the write
    stamps the owner's slot while paying the thief, so the owner loses that square's XP permanently and
    the thief's balance is only half the damage.
    """
    owner, thief = _member(), _member()
    challenge, jobs = _jobs_run_with_completed_squares(owner)

    with pytest.raises(rewards.RewardError, match='not your challenge'):
        rewards.redeem_slot(challenge, thief, jobs[0].slug)

    assert not ContractXPGrant.objects.filter(profile=thief).exists()
    assert challenge.slots.get(key=jobs[0].slug).xp_redeemed_at is None, "the owner's square is untouched"


def test_a_hidden_run_can_still_be_redeemed():
    """Hiding is visibility, not a pause -- detection keeps completing a hidden run's squares, so the XP
    those squares earned is still the hunter's. Refusing here would be the inconsistency."""
    profile = _member()
    challenge, jobs = _jobs_run_with_completed_squares(profile)
    svc.hide(challenge, profile)

    _slot, granted, _ceremony = rewards.redeem_slot(challenge, profile, jobs[0].slug)

    assert granted == CHALLENGE_SLOT_JOB_XP


def test_pending_xp_counts_only_finished_unpaid_squares():
    profile = _member()
    challenge, jobs = _jobs_run_with_completed_squares(profile, count=2)
    assert rewards.pending_xp(challenge) == 2 * CHALLENGE_SLOT_JOB_XP

    rewards.redeem_slot(challenge, profile, jobs[0].slug)

    assert rewards.pending_xp(challenge) == CHALLENGE_SLOT_JOB_XP


def test_a_square_whose_job_left_the_catalogue_is_refused_by_name():
    """The message matters here: "no longer in the catalogue" tells a hunter something true about a staff
    edit, where a generic refusal would read as a bug in their run."""
    profile = _member()
    challenge, jobs = _jobs_run_with_completed_squares(profile)
    slug = jobs[0].slug
    Job.objects.filter(slug=slug).delete()

    with pytest.raises(rewards.RewardError, match='no longer in the catalogue'):
        rewards.redeem_slot(challenge, profile, slug)


def test_the_page_and_the_payout_agree_about_an_orphaned_square():
    """A square whose Job was deleted is skipped by `redeem_all` and left unstamped, so without the
    catalogue term in `redeemable_slots` it would be owed forever: the page offers XP, Claim all pays
    nothing, and the "nothing owed is not an error" rule means it says nothing at all."""
    profile = _member()
    challenge, jobs = _jobs_run_with_completed_squares(profile, count=2)
    Job.objects.filter(slug=jobs[0].slug).delete()

    assert rewards.pending_xp(challenge) == CHALLENGE_SLOT_JOB_XP, 'only the payable square is owed'

    rewards.redeem_all(challenge, profile)

    assert rewards.pending_xp(challenge) == 0, 'and nothing is left owed that can never be paid'


def test_a_key_that_is_not_a_square_of_this_run_is_refused():
    profile = _member()
    challenge, _jobs = _jobs_run_with_completed_squares(profile)

    with pytest.raises(rewards.RewardError, match='not part of this challenge'):
        rewards.redeem_slot(challenge, profile, 'not-a-real-job')


def test_a_deleted_run_cannot_be_redeemed():
    """`_lock_run` re-reads the row, so a run deleted between the page render and the POST refuses with a
    sentence rather than raising `Challenge.DoesNotExist` into a 500."""
    profile = _member()
    challenge, jobs = _jobs_run_with_completed_squares(profile)
    slug = jobs[0].slug
    challenge.__class__.objects.filter(pk=challenge.pk).delete()

    with pytest.raises(rewards.RewardError, match='no longer exists'):
        rewards.redeem_slot(challenge, profile, slug)


def test_a_redeem_logs_every_pursuer_rank_it_crosses():
    """THE LOSS THE FIRST VERSION SHIPPED. `grant_job_xp_bulk` logs job-tier milestones only; Pursuer rank
    crossings come from the bracket `accept_contracts_bulk` puts around it, which this file's first draft
    omitted at both call sites.

    It is unrecoverable, which is why it is pinned: `ranks_crossed(old, new)` is `old < min <= new`, so a
    rank crossed by an unbracketed redeem is never logged and never loggable -- the next contract claim
    starts from the already-raised level. The Career hero reads these rows, so the hunter is left with
    blank dates on rungs they really did cross.

    FIVE SQUARES, not one: a single square is two job levels and lands a fresh hunter on Pursuer 27, which
    crosses nothing (Recruit is 35). Five reaches exactly 35.
    """
    from trophies.models import ProgressionMilestone

    profile = _member()
    challenge, _jobs = _jobs_run_with_completed_squares(profile, count=5)
    assert not ProgressionMilestone.objects.filter(
        profile=profile, kind=ProgressionMilestone.PURSUER_RANK).exists()

    rewards.redeem_all(challenge, profile)

    crossed = ProgressionMilestone.objects.filter(profile=profile,
                                                  kind=ProgressionMilestone.PURSUER_RANK)
    assert crossed.count() >= 1, 'the rank this redeem crossed was not logged'
    assert crossed.filter(key='recruit').exists()


def test_first_claim_is_derived_from_the_hunter_rather_than_defaulted(monkeypatch):
    """`first_claim` is the onboarding flag. A hunter whose first job XP ever is a challenge redeem would
    otherwise lose it twice: false on these grants, and false again on their real first contract claim,
    because by then `_has_any_job_xp` is true.

    ASSERTS THE KWARG, not a milestone row, and the first version of this test asserted the row and failed
    for a reason that had nothing to do with the flag: a square is two job levels and the first JOB_TIER
    sits at level 10, so a redeem crosses no tier and writes no row to carry the flag. Reaching level 10 on
    one job needs five completed runs, which is not a fixture. What this file owns is the CONTRACT with the
    primitive -- what the primitive then does with the flag is its own tests' business.
    """
    from trophies.services import contract_service

    seen = []
    real = contract_service.grant_job_xp_bulk

    def spy(profile, grants, **kwargs):
        seen.append(kwargs.get('first_claim'))
        return real(profile, grants, **kwargs)

    monkeypatch.setattr(contract_service, 'grant_job_xp_bulk', spy)

    profile = _member()
    challenge, jobs = _jobs_run_with_completed_squares(profile, count=2)

    rewards.redeem_slot(challenge, profile, jobs[0].slug)
    assert seen == [True], 'their first job XP ever'

    rewards.redeem_slot(challenge, profile, jobs[1].slug)
    assert seen == [True, False], 'and not the second time'


def test_the_ledger_row_records_no_multiplier():
    """A double-XP weekend does NOT scale challenge XP, deliberately: the constant's legibility claim is
    "exactly two job levels a square", and the figure is quoted to the hunter before they start."""
    profile = _member()
    challenge, jobs = _jobs_run_with_completed_squares(profile)
    rewards.redeem_slot(challenge, profile, jobs[0].slug)

    grant = ContractXPGrant.objects.get(profile=profile, source='challenge')
    assert grant.amount == CHALLENGE_SLOT_JOB_XP
    assert grant.multiplier == 1


# ── the completion titles ────────────────────────────────────────────────────────────────────────

def test_a_first_completion_grants_the_champion_title():
    profile = _member()
    challenge = _az_run_completed(profile)

    held = UserTitle.objects.get(profile=profile, title__name='A-Z Champion')
    assert held.source_type == 'challenge'
    assert held.source_id == challenge.pk, 'the CHALLENGE id, not a slot id'
    assert challenge.is_complete is True


def test_a_second_completion_grants_legend_and_keeps_champion():
    profile = _member()
    _az_run_completed(profile)
    _az_run_completed(profile)

    names = set(UserTitle.objects.filter(profile=profile).values_list('title__name', flat=True))
    assert names == {'A-Z Champion', 'A-Z Legend'}


def test_a_third_completion_grants_nothing_new():
    """Finishing another run is allowed and good; it just does not mint a new title.

    ASSERTS THE RETURN, not only the row count, and the first version of this test did the opposite --
    which made it pass against a mutation that granted the SECOND title again on the third run.
    `unique_together` was quietly doing the work the assertion claimed to do: `get_or_create` handed back
    the row the hunter already held, the count stayed at two, and the cutoff was pinned by nothing.
    """
    profile = _member()
    runs = [_az_run_completed(profile) for _ in range(3)]

    assert UserTitle.objects.filter(profile=profile).count() == 2
    assert rewards.grant_completion_title(runs[2]) is None, 'the third run earns no title at all'

    # And each title records the run that actually earned it, rather than the latest one to ask.
    champion = UserTitle.objects.get(profile=profile, title__name='A-Z Champion')
    legend = UserTitle.objects.get(profile=profile, title__name='A-Z Legend')
    assert champion.source_id == runs[0].pk
    assert legend.source_id == runs[1].pk


def test_the_two_challenge_types_award_different_titles():
    """Pinned because one shared name for both types would be the kind of mistake that reads as correct
    on the page a hunter looks at and wrong on their profile."""
    profile = _member()
    _az_run_completed(profile)
    challenge, _jobs = _jobs_run_with_completed_squares(profile, count=Job.objects.count())

    assert challenge.is_complete is True
    names = set(UserTitle.objects.filter(profile=profile).values_list('title__name', flat=True))
    assert names == {'A-Z Champion', 'Job Challenge Champion'}


def test_granting_a_title_twice_does_not_duplicate_it():
    """`unique_together` already forbids the row; this pins that the code path handles it rather than
    raising, because `on_run_completed` can legitimately be reached twice by a repair."""
    profile = _member()
    challenge = _az_run_completed(profile)

    rewards.grant_completion_title(challenge)

    assert UserTitle.objects.filter(profile=profile, title__name='A-Z Champion').count() == 1


def test_a_completed_a_z_run_grants_a_title_and_no_xp():
    """The two halves of the A-Z reward in one assertion, because the danger is doing half of it."""
    profile = _member()
    _az_run_completed(profile)

    assert UserTitle.objects.filter(profile=profile, source_type='challenge').exists()
    assert not ContractXPGrant.objects.filter(profile=profile, source='challenge').exists()
    assert not ProfileJobXP.objects.filter(profile=profile).exists()


def test_a_title_failure_does_not_cost_the_hunter_the_completion(monkeypatch):
    """Rule 4: both completion paths run inside a detection loop that logs and moves on, so a raise in
    the reward code would abandon the rest of that hunter's squares. The completion is the fact; the
    title is derived and can be backfilled."""
    def boom(_challenge):
        raise RuntimeError('title service on fire')

    monkeypatch.setattr(rewards, 'grant_completion_title', boom)
    profile = _member()

    challenge = _az_run_completed(profile)

    assert challenge.is_complete is True
    assert challenge.completed_count == challenge.total_slots
    assert not UserTitle.objects.filter(profile=profile).exists()


def test_a_missed_title_is_still_grantable_after_a_later_run_finishes(monkeypatch):
    """THE HALF OF RULE 4 THAT USED TO BE FALSE. The file promises a failed title can be backfilled
    because the completion is recorded -- but while the ordinal was a live count of completed runs, run
    1's backfill read 2 and re-granted the SECOND title, leaving the first unreachable by any code path.

    The ordinal is now a property of the run (completions no later than this one), so this passes.
    """
    def boom(_challenge):
        raise RuntimeError('title service on fire')

    profile = _member()
    monkeypatch.setattr(rewards, 'grant_completion_title', boom)
    run_one = _az_run_completed(profile)
    monkeypatch.undo()
    run_two = _az_run_completed(profile)

    assert rewards.completion_ordinal(run_one) == 1
    assert rewards.completion_ordinal(run_two) == 2

    recovered = rewards.grant_completion_title(run_one)

    assert recovered.title.name == 'A-Z Champion', 'the backfill grants the title THAT run earned'
    assert set(UserTitle.objects.filter(profile=profile).values_list('title__name', flat=True)) == {
        'A-Z Champion', 'A-Z Legend'}


def test_the_ordinal_of_an_unfinished_run_is_zero():
    """So a caller that asks too early grants nothing, rather than minting the first title for a run in
    progress."""
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)

    assert rewards.completion_ordinal(challenge) == 0
    assert rewards.grant_completion_title(challenge) is None


def test_completing_a_run_notifies_its_hunter(django_capture_on_commit_callbacks):
    """LOADS THE REAL FIXTURE, which is the point. The template row was missing from
    `initial_templates.json` for the whole first draft, so every completion in production would have
    logged a warning and sent nothing -- and the warning told the operator to loaddata a file that did not
    contain it. A test that created the template by hand would still pass in that world.

    `django_capture_on_commit_callbacks` because the send is deferred to `on_commit`, and pytest's
    transaction never commits, so nothing would fire otherwise -- which is also why this path had no
    coverage at all.
    """
    from django.core.management import call_command

    from notifications.models import Notification

    call_command('loaddata', 'notifications/fixtures/initial_templates.json', verbosity=0)
    profile = _member()

    with django_capture_on_commit_callbacks(execute=True):
        challenge = _az_run_completed(profile)

    note = Notification.objects.get(recipient=profile.user, notification_type='challenge_completed')
    assert challenge.name in note.title
    assert str(challenge.pk) in note.action_url, 'the run is reachable from the notification'


def test_a_failing_notification_service_is_contained_by_the_sender(monkeypatch,
                                                                   django_capture_on_commit_callbacks):
    """THE INNER GUARD: `_notify_completion` wraps its own send, because a notification is the least
    important thing in the chain and the only one depending on another app's template data.

    This is NOT the test for `robust=True`, and cannot be. Being caught here means the exception never
    reaches `on_commit` at all -- which is exactly why the next test raises from the callback instead.
    One test claiming both guards is how the weaker one goes unpinned.

    `monkeypatch`, NOT assign-then-`del`. The first version did the latter and broke a test two files
    away: assigning to a class attribute REPLACES the entry in the class `__dict__`, so `del` removes the
    original with it and `NotificationService.create_notification` is gone for the rest of the session.
    It passed alone and failed in the suite, which is the tell.
    """
    from notifications.services.notification_service import NotificationService

    def boom(*_args, **_kwargs):
        raise RuntimeError('notifications on fire')

    profile = _member()
    monkeypatch.setattr(NotificationService, 'create_notification', staticmethod(boom))

    with django_capture_on_commit_callbacks(execute=True):
        challenge = _az_run_completed(profile)

    assert challenge.is_complete is True
    assert UserTitle.objects.filter(profile=profile).exists(), 'and the title still landed'


def test_the_completion_callback_is_registered_robust(monkeypatch,
                                                      django_capture_on_commit_callbacks):
    """THE OUTER GUARD, and the only observable difference `robust=True` makes.

    The callback runs AFTER the commit, on the caller's thread: inside `token_keeper`'s per-profile
    guard for the sync hook, and inside NOTHING for the nightly sweep, where an escaping exception
    aborts the whole command for every hunter. Without `robust`, anything raised outside
    `_notify_completion`'s own try -- its imports, the re-read, the `getattr` -- propagates out of the
    atomic block's exit.

    Raises from the callback itself, which is the one region that inner try does not cover.
    """
    def boom(_challenge_id):
        raise RuntimeError('the callback itself is on fire')

    monkeypatch.setattr(rewards, '_notify_completion', boom)
    profile = _member()

    with django_capture_on_commit_callbacks(execute=True):
        challenge = _az_run_completed(profile)

    assert challenge.is_complete is True
    assert UserTitle.objects.filter(profile=profile).exists()


# ── the names themselves ─────────────────────────────────────────────────────────────────────────

def test_the_four_title_names_are_distinct_from_each_other():
    """`UserTitle.unique_together` is `(profile, title)` with NO `source_type`, so a name shared with
    another system's title makes `get_or_create` hand back THAT row and attaches a hunter to a title they
    did not earn. `badge_adapters.grant_series_title` carries the scar.

    This can only check what the code declares -- a collision with a name that exists only in PROD data
    is not visible from here, and was checked against prod separately before the names were fixed.
    """
    names = [name for per_type in rewards.TITLE_NAMES.values() for name in per_type.values()]
    assert len(names) == len(set(names)) == 4


def test_every_challenge_type_declares_its_reward_shape():
    """A type with no entry grants nothing silently, which would read as "rewards are broken" rather
    than as a missing dict key.

    NOT EVERY TYPE HAS ORDINAL TITLES, which is what this used to assume. It looped every type demanding
    a first and a second title, and the Plat Calendar deliberately has neither -- it pays a ladder of
    day-count markers plus one ultimate per view, because a 365-day run has no meaningful "first
    completion / second completion" shape (finishing one needs roughly 2,150 platinums).

    SO THE GUARD CHECKS A PARTITION, which is strictly stronger than the loop it replaces. Every type
    must be declared EITHER in `TITLE_NAMES` with both ordinals OR in `TYPES_WITHOUT_ORDINAL_TITLES`.
    Silence is the failure mode the original docstring named, and a type absent from both is now the one
    thing this cannot miss -- where simply skipping the Calendar would have left a fourth type free to
    grant nothing unnoticed.
    """
    from challenges.models import CHALLENGE_TYPES

    ordinal_types = set(rewards.TITLE_NAMES)
    declared_without = set(rewards.TYPES_WITHOUT_ORDINAL_TITLES)

    assert ordinal_types | declared_without == set(CHALLENGE_TYPES), (
        'these challenge types declare no reward shape at all, so they would grant nothing silently: %s'
        % sorted(set(CHALLENGE_TYPES) - ordinal_types - declared_without))
    assert not (ordinal_types & declared_without), (
        'a type cannot both have ordinal titles and be declared as having none: %s'
        % sorted(ordinal_types & declared_without))

    for challenge_type in ordinal_types:
        assert rewards.title_for(challenge_type, 1), challenge_type
        assert rewards.title_for(challenge_type, 2), challenge_type
    for challenge_type in declared_without:
        assert rewards.title_for(challenge_type, 1) is None, (
            '%s is declared as having no ordinal titles but `title_for` returns one' % challenge_type)

    assert rewards.title_for('az', 3) is None


def test_a_name_already_held_from_another_source_is_reported_not_celebrated(caplog):
    """THE COLLISION, exercised rather than asserted away. The first version of this test created no
    fixtures and compared two empty sets, so it passed on `set() & set()` while its own docstring claimed
    it was testing the suite's data -- and a comment in `rewards.py` cited it as proof the names were safe.

    `UserTitle.unique_together` carries no `source_type`, so a hunter who already holds a title of this
    name from another system makes `get_or_create` hand that row back, and every caller reads a successful
    grant. The row is not ours to re-point, so the only honest behaviour is to leave it and say so loudly.
    """
    profile = _member()
    stolen = Title.objects.create(name='A-Z Champion')
    UserTitle.objects.create(profile=profile, title=stolen, source_type='badge_series', source_id=1)

    challenge = _az_run_completed(profile)

    held = UserTitle.objects.get(profile=profile, title=stolen)
    assert held.source_type == 'badge_series', 'somebody else\'s row must not be re-pointed'
    assert held.source_id == 1
    assert UserTitle.objects.filter(profile=profile, title=stolen).count() == 1
    assert any('collides with another system' in r.getMessage() for r in caplog.records), \
        'the collision must be logged, not swallowed'
    assert challenge.is_complete is True, 'and the completion still stands'


def test_the_four_names_are_free_in_the_test_database():
    """A weaker claim than the deleted version pretended to make, and an honest one: nothing the suite
    creates owns these names. Whether PROD data does is a question only prod can answer, and was asked
    there before the names were fixed."""
    ours = {name for per_type in rewards.TITLE_NAMES.values() for name in per_type.values()}
    assert not Title.objects.filter(name__in=ours).exists()
