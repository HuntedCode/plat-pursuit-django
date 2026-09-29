"""`seed_challenge_demo`: the command that makes the reward surfaces LOOKABLE-AT.

WHY A DEV SEEDER GETS TESTS AT ALL. It writes into the append-only job-XP ledger, and its `--reset` deletes
rows from that ledger — so the two things worth pinning are that it cannot destroy real XP and that reset
actually reverses what it did. A seeder that leaves orphaned grants behind inflates a hunter's levels
permanently and there is nothing in the UI that would ever show it.

It also has to produce the states it advertises. A seeder whose "finished run" is not finished wastes the
browser pass it exists to serve, and nothing else in the suite would notice.
"""
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from django.utils import timezone

from challenges.management.commands.seed_challenge_demo import DEMO_TAG, MIXED_CLAIMED, MIXED_FILLED
from challenges.models import CHALLENGE_TYPE_AZ, CHALLENGE_TYPE_JOBS, Challenge
from challenges.services import rewards
from tests.factories import ConceptFactory, GameFactory, IGDBMatchFactory, ProfileFactory, UserFactory
from trophies.models import Contract, ContractXPGrant, Job, ProfileJobXP
from trophies.util_modules.constants import CHALLENGE_SLOT_JOB_XP

pytestmark = pytest.mark.django_db

_SEQ = {'n': 0}


@pytest.fixture
def catalogue():
    """A live contract per job, plus a handful of letter-initial ones, so the seeder has a pool.

    The dev database has a real catalogue; the test one does not, and the command's whole job is to pick
    REAL contracts out of whatever is there. So the fixture is the catalogue.
    """
    made = []
    for job in Job.objects.order_by('slug'):
        made.append(_contract('Alpha %s' % job.name, jobs=[job]))
    for letter in 'ABCDEFGHIJ':
        made.append(_contract('%s Game For Letters' % letter))
    return made


def _contract(name, *, jobs=()):
    _SEQ['n'] += 1
    c = Contract.objects.create(name=name, slug='%s-%d' % (name.lower().replace(' ', '-'), _SEQ['n']),
                                is_live=True, igdb_id=930_000 + _SEQ['n'])
    if jobs:
        c.jobs.set(jobs)
    concept = ConceptFactory(anchor_migration_completed_at=timezone.now())
    IGDBMatchFactory(concept=concept, igdb_id=c.igdb_id, status='accepted')
    GameFactory(concept=concept)
    return c


def _hunter():
    return ProfileFactory(user=UserFactory(), user_is_premium=True, is_linked=True)


def _seed(profile, **kwargs):
    call_command('seed_challenge_demo', user=profile.psn_username, verbosity=0, **kwargs)


def _demo_runs(profile):
    return Challenge.objects.filter(profile=profile, name__contains=DEMO_TAG)


# ── it produces the states it advertises ─────────────────────────────────────────────────────────

@override_settings(DEBUG=True)
def test_it_seeds_a_finished_jobs_run_with_nothing_claimed(catalogue):
    """THE SCENARIO THE BROWSER PASS NEEDS MOST: the ledger at its longest, Claim-all live, and the
    layout-jump question. A seeder whose "finished" run is not finished wastes the pass."""
    profile = _hunter()

    _seed(profile)

    finished = _demo_runs(profile).filter(challenge_type=CHALLENGE_TYPE_JOBS, is_complete=True).first()
    assert finished is not None
    assert finished.completed_count == finished.total_slots
    summary = rewards.summary(finished)
    assert summary['claimable_count'] == finished.total_slots, 'every square must still be claimable'
    assert summary['paid_xp'] == 0


@override_settings(DEBUG=True)
def test_it_seeds_a_mixed_ledger_with_both_row_states(catalogue):
    """Paid and claimable rows on one panel, which is the only way to see whether the two read as two
    states rather than two elevations."""
    profile = _hunter()

    _seed(profile)

    mixed = _demo_runs(profile).filter(challenge_type=CHALLENGE_TYPE_JOBS, is_complete=False).first()
    assert mixed is not None
    rows = rewards.summary(mixed)['rows']
    assert len(rows) == MIXED_FILLED
    assert [r['is_paid'] for r in rows].count(True) == MIXED_CLAIMED
    assert [r['claimable'] for r in rows].count(True) == MIXED_FILLED - MIXED_CLAIMED


@override_settings(DEBUG=True)
def test_it_seeds_an_a_z_run_that_draws_no_panel(catalogue):
    profile = _hunter()

    _seed(profile)

    az = _demo_runs(profile).filter(challenge_type=CHALLENGE_TYPE_AZ).first()
    assert az is not None
    assert az.filled_count > 0, 'an empty board shows nothing worth looking at'
    assert rewards.summary(az)['per_square'] == 0, 'A-Z pays no job XP, so it has no panel'
    assert rewards.summary(az)['title_name'] == 'A-Z Champion'


@override_settings(DEBUG=True)
def test_the_finished_run_is_created_before_the_second_one(catalogue):
    """ORDER IS LOAD-BEARING. Only one run per type can be active, so the finished run has to be built
    first -- otherwise `start` hands the first run back instead of creating a second, and the seeder
    silently produces one jobs run instead of two."""
    profile = _hunter()

    _seed(profile)

    jobs_runs = _demo_runs(profile).filter(challenge_type=CHALLENGE_TYPE_JOBS)
    assert jobs_runs.count() == 2
    assert jobs_runs.filter(is_complete=True).count() == 1
    assert jobs_runs.filter(is_complete=False).count() == 1


# ── what it must not touch ───────────────────────────────────────────────────────────────────────

@override_settings(DEBUG=True)
def test_it_invents_no_completion_history(catalogue):
    """NO `EarnedContract` ROWS. A square is completed by `mark_slot_completed` directly, so the command
    never fabricates trophies or contract credit for a real account -- which is what stops a demo run
    leaking into Career, the boards, or a real claim."""
    from trophies.models import EarnedContract

    profile = _hunter()

    _seed(profile)

    assert not EarnedContract.objects.filter(profile=profile).exists()


@override_settings(DEBUG=True)
def test_reset_removes_its_grants_and_leaves_real_xp_alone(catalogue):
    """THE ONE THAT MATTERS. The ledger is append-only and `recompute_profile_job_xp` sums every row, so a
    reset that deleted the slots but not the grants would leave rows nothing can identify -- inflating the
    hunter's levels permanently, with no surface that would ever show it."""
    profile = _hunter()
    real_job = Job.objects.order_by('slug').first()
    # A REAL grant, of the kind a contract claim writes. It must survive the reset untouched.
    ContractXPGrant.objects.create(profile=profile, job=real_job, amount=1234, source='contract')

    _seed(profile)
    assert ContractXPGrant.objects.filter(profile=profile, source='challenge').count() == MIXED_CLAIMED

    _seed(profile, reset=True)

    assert ContractXPGrant.objects.filter(profile=profile, source='contract', amount=1234).count() == 1
    # The reset removed the FIRST seed's grants; the re-seed paid a fresh set of the same size.
    assert ContractXPGrant.objects.filter(profile=profile, source='challenge').count() == MIXED_CLAIMED
    assert _demo_runs(profile).filter(challenge_type=CHALLENGE_TYPE_JOBS).count() == 2, 'no run pile-up'


@override_settings(DEBUG=True)
def test_reset_rebuilds_the_xp_cache_from_what_survives(catalogue):
    """`ProfileJobXP` is a denormalised sum of the ledger, so a reset that deletes grants without
    rebuilding it leaves the cache claiming XP no row supports."""
    from django.db.models import Sum

    profile = _hunter()
    _seed(profile)

    call_command('seed_challenge_demo', user=profile.psn_username, reset=True, verbosity=0)

    for cache in ProfileJobXP.objects.filter(profile=profile):
        ledger = (ContractXPGrant.objects.filter(profile=profile, job=cache.job)
                  .aggregate(t=Sum('amount'))['t'] or 0)
        assert cache.total_xp == ledger


@override_settings(DEBUG=True)
def test_a_hunters_own_run_is_never_adopted_or_deleted(catalogue):
    """THE WORST BUG THE FIRST DRAFT HAD, and the reason this test exists. `start` RESUMES a hidden run and
    hands back an already-active one, so a bare call adopted the hunter's OWN run -- and `_tag` then wrote
    the demo marker into its name. The marker is the reset scope, so the next `--reset` would have deleted
    real progress while the command reported success.

    The seeder now only tags a run it CREATED, and says out loud which scenario it skipped.
    """
    from challenges.services import challenge_service as svc

    profile = _hunter()
    real = svc.start(profile, CHALLENGE_TYPE_AZ)
    assert DEMO_TAG not in real.name

    _seed(profile)

    real.refresh_from_db()
    assert DEMO_TAG not in real.name, "the hunter's own run was adopted"
    assert not _demo_runs(profile).filter(challenge_type=CHALLENGE_TYPE_AZ).exists()

    _seed(profile, reset=True)

    assert Challenge.objects.filter(pk=real.pk).exists(), "reset deleted the hunter's own run"
    real.refresh_from_db()
    assert DEMO_TAG not in real.name


# ── --wipe, the way out of the deadlock ──────────────────────────────────────────────────────────

@override_settings(DEBUG=True)
def test_wipe_clears_the_profiles_own_runs_so_seeding_can_proceed(catalogue):
    """THE DEADLOCK THIS EXISTS FOR. The seeder refuses to adopt a run it did not create, `--reset` is scoped
    to the demo tag, and `ChallengeAdmin` has `has_delete_permission -> False` -- so a dev profile that had
    ever pressed Start could not be seeded and had no way to clear itself."""
    from challenges.services import challenge_service as svc

    profile = _hunter()
    own = svc.start(profile, CHALLENGE_TYPE_AZ)

    _seed(profile)
    assert not _demo_runs(profile).filter(challenge_type=CHALLENGE_TYPE_AZ).exists(), 'blocked, as designed'

    _seed(profile, wipe=True)

    assert not Challenge.objects.filter(pk=own.pk).exists()
    assert _demo_runs(profile).filter(challenge_type=CHALLENGE_TYPE_AZ).exists()


@override_settings(DEBUG=True)
def test_wipe_clears_a_HIDDEN_run_too(catalogue):
    """THE CASE THE OWNER HIT. Hiding a run feels like clearing it and does not: `start` RESUMES a hidden run
    rather than dealing a fresh one, so the one-active-per-type slot is still occupied as far as seeding is
    concerned -- and a hidden run is invisible on the page, so there is nothing to hide again or finish.

    (`Challenge.objects` deliberately does not filter soft-deleted rows, which is what lets one query reach
    it at all -- a default manager that hid them would have made this impossible to write.)
    """
    from challenges.services import challenge_service as svc

    profile = _hunter()
    own = svc.start(profile, CHALLENGE_TYPE_JOBS)
    svc.hide(own, profile)
    own.refresh_from_db()
    assert own.is_deleted is True

    _seed(profile, wipe=True)

    assert not Challenge.objects.filter(pk=own.pk).exists()
    assert _demo_runs(profile).filter(challenge_type=CHALLENGE_TYPE_JOBS).count() == 2


@override_settings(DEBUG=True)
def test_wipe_takes_the_challenge_grants_with_it(catalogue):
    """Same grants-before-slots order as `--reset`, for the same reason: a challenge grant's `source_id` IS
    the slot id, so once the slots are gone the rows cannot be identified again and would sit in the ledger
    forever, counted by every recompute."""
    from django.db.models import Sum

    profile = _hunter()
    _seed(profile)
    assert ContractXPGrant.objects.filter(profile=profile, source='challenge').exists()

    _seed(profile, wipe=True)

    # The wipe removed the first seed's grants; the re-seed paid a fresh set.
    assert ContractXPGrant.objects.filter(profile=profile, source='challenge').count() == MIXED_CLAIMED
    for cache in ProfileJobXP.objects.filter(profile=profile):
        ledger = (ContractXPGrant.objects.filter(profile=profile, job=cache.job)
                  .aggregate(t=Sum('amount'))['t'] or 0)
        assert cache.total_xp == ledger


@override_settings(DEBUG=True)
def test_wipe_leaves_other_profiles_alone(catalogue):
    """It is scoped to one profile. Pinned because "remove every run" is one missing filter away from
    removing every run on the site."""
    from challenges.services import challenge_service as svc

    mine, theirs = _hunter(), _hunter()
    other_run = svc.start(theirs, CHALLENGE_TYPE_AZ)

    _seed(mine, wipe=True)

    assert Challenge.objects.filter(pk=other_run.pk).exists()


@override_settings(DEBUG=True)
def test_wipe_leaves_non_challenge_xp_alone(catalogue):
    """It deletes grants by `source='challenge'` and slot id, so a contract's XP is untouchable by it."""
    profile = _hunter()
    job = Job.objects.order_by('slug').first()
    ContractXPGrant.objects.create(profile=profile, job=job, amount=4321, source='contract')

    _seed(profile, wipe=True)

    assert ContractXPGrant.objects.filter(profile=profile, source='contract', amount=4321).exists()


@override_settings(DEBUG=True)
def test_reset_does_not_imply_wipe(catalogue):
    """They are different promises. `--reset` re-seeds; `--wipe` destroys a profile's runs, which on a dev
    box is scratch data and anywhere else is somebody's progress."""
    from challenges.services import challenge_service as svc

    profile = _hunter()
    own = svc.start(profile, CHALLENGE_TYPE_AZ)

    _seed(profile, reset=True)

    assert Challenge.objects.filter(pk=own.pk).exists()


@override_settings(DEBUG=False)
def test_wipe_is_behind_the_debug_guard(catalogue):
    """The most destructive flag on the most dev-only command still answers to the same gate."""
    from challenges.services import challenge_service as svc

    profile = _hunter()
    own = svc.start(profile, CHALLENGE_TYPE_AZ)

    with pytest.raises(CommandError, match='dev database'):
        _seed(profile, wipe=True)

    assert Challenge.objects.filter(pk=own.pk).exists()


# ── the notification template, which this command is most likely to surface ──────────────────────

@override_settings(DEBUG=True)
def test_it_says_how_to_install_the_notification_template_when_it_is_missing(catalogue, capsys):
    """Completing a run fires the completion notification, and this command completes runs -- so on a database
    without the fixture loaded it emits "challenge_completed template missing" from three layers down. The
    sender degrades on purpose, which is what makes that log line easy to read as a bug in the feature.

    The test DB has no fixtures loaded, so this is the default state here.
    """
    from notifications.models import NotificationTemplate

    assert not NotificationTemplate.objects.filter(name='challenge_completed').exists()
    profile = _hunter()

    call_command('seed_challenge_demo', user=profile.psn_username)

    out = capsys.readouterr().out
    assert 'loaddata notifications/fixtures/initial_templates.json' in out
    assert 'Nothing is broken' in out


@override_settings(DEBUG=True)
def test_it_stays_quiet_once_the_template_is_there(catalogue, capsys):
    """A hint that fires when there is nothing to fix trains people to ignore it."""
    profile = _hunter()
    call_command('loaddata', 'notifications/fixtures/initial_templates.json', verbosity=0)

    call_command('seed_challenge_demo', user=profile.psn_username)

    assert 'loaddata notifications' not in capsys.readouterr().out


# ── the guards ───────────────────────────────────────────────────────────────────────────────────

@override_settings(DEBUG=False)
def test_it_refuses_to_run_outside_debug_without_force(catalogue):
    """It pays real XP into an append-only ledger. A seeder that runs anywhere is a seeder that runs in
    production once."""
    profile = _hunter()

    with pytest.raises(CommandError, match='dev database'):
        _seed(profile)

    assert not _demo_runs(profile).exists()


@override_settings(DEBUG=False)
def test_force_overrides_the_debug_guard(catalogue):
    profile = _hunter()

    _seed(profile, force=True)

    assert _demo_runs(profile).exists()


@override_settings(DEBUG=True)
def test_an_unlinked_profile_is_refused_before_anything_is_written(catalogue):
    """Every challenge write refuses an unlinked profile, so without this the command would fail one call
    in with the runs half built."""
    profile = ProfileFactory(user=UserFactory(), user_is_premium=True, is_linked=False)

    with pytest.raises(CommandError, match='linked PSN'):
        _seed(profile)

    assert not _demo_runs(profile).exists()


@override_settings(DEBUG=True)
def test_an_unknown_user_is_a_clean_refusal():
    with pytest.raises(CommandError, match='No profile'):
        call_command('seed_challenge_demo', user='nobody-by-that-name', verbosity=0)


@override_settings(DEBUG=True)
def test_list_writes_nothing(catalogue):
    """`--list` is the "where were those URLs again" path, and it must not re-seed."""
    profile = _hunter()
    _seed(profile)
    before = set(_demo_runs(profile).values_list('pk', flat=True))

    call_command('seed_challenge_demo', user=profile.psn_username, list=True, verbosity=0)

    assert set(_demo_runs(profile).values_list('pk', flat=True)) == before


@override_settings(DEBUG=True)
def test_it_survives_a_catalogue_too_thin_to_fill_every_square():
    """NO FIXTURE HERE, deliberately: with no live contracts at all the seeder must still produce runs and
    say so, rather than raising. A dev database part way through a catalogue import is the normal case."""
    profile = _hunter()

    _seed(profile)

    # TWO, not three, and honestly so: with nothing to fill the first jobs run cannot COMPLETE, so nothing
    # frees the one-active-per-type slot and the mixed scenario is skipped with a warning rather than
    # silently tagging the same run twice (which is what the first draft did).
    assert _demo_runs(profile).count() == 2
    assert _demo_runs(profile).filter(filled_count=0).exists()
    assert _demo_runs(profile).filter(challenge_type=CHALLENGE_TYPE_JOBS).count() == 1


@override_settings(DEBUG=True)
def test_the_seeded_claim_pays_the_real_figure(catalogue):
    """It goes through `rewards.redeem_slot`, not a hand-written grant, so a paid row in the demo is paid
    the way a real one is -- which is the only reason looking at it proves anything."""
    profile = _hunter()

    _seed(profile)

    grants = ContractXPGrant.objects.filter(profile=profile, source='challenge')
    assert grants.count() == MIXED_CLAIMED
    assert {g.amount for g in grants} == {CHALLENGE_SLOT_JOB_XP}
    assert all(g.source_id is not None for g in grants), 'the slot id is half the idempotency guard'
