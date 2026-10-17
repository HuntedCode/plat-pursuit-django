"""`delete_challenge`: the narrow door for permanently deleting ONE run (owner, 2026-10-17). The admin stays
read-only; hiding is not a way out (Start resumes a hidden run). A dry run unless `--confirm`, and a run that paid
job XP or granted a title is refused unless `--force`, which keeps those rewards."""
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from challenges.models import (CHALLENGE_TYPE_AZ, CHALLENGE_TYPE_JOBS, COMPLETED_VIA_LIVE, Challenge,
                               ChallengeSlot)
from challenges.services import challenge_service as svc
from challenges.services import rewards
from tests.factories import ProfileFactory, UserFactory
from trophies.models import ContractXPGrant, Job, Title, UserTitle

pytestmark = pytest.mark.django_db


def _hunter(psn='Tester'):
    return ProfileFactory(user=UserFactory(), psn_username=psn, user_is_premium=True, is_linked=True)


def _run(cmd_args, **kw):
    out = StringIO()
    call_command('delete_challenge', *cmd_args, stdout=out, **kw)
    return out.getvalue()


def _pay(run):
    """A finished square on a Job Coverage run, and the job XP it paid."""
    slot = run.slots.first()
    ChallengeSlot.objects.filter(pk=slot.pk).update(
        is_completed=True, contract_name='Astro Bot', contract_slug='astro-bot',
        completed_at=timezone.now(), completed_via=COMPLETED_VIA_LIVE, xp_redeemed_at=timezone.now())
    return ContractXPGrant.objects.create(profile=run.profile, job=Job.objects.get(slug=slot.key), amount=500,
                                          source=rewards.XP_SOURCE, source_id=slot.pk)


def test_a_dry_run_reports_and_deletes_nothing():
    run = svc.start(_hunter(), CHALLENGE_TYPE_AZ)
    out = _run([str(run.pk)])
    assert 'Run %d' % run.pk in out and 'owned by tester' in out.lower() and '26 squares: 0 filled, 0 finished.' in out
    assert 'Dry run: nothing was deleted.' in out
    assert Challenge.objects.filter(pk=run.pk).exists()


def test_confirm_deletes_the_run_and_frees_start_for_a_fresh_one():
    profile = _hunter()
    run = svc.start(profile, CHALLENGE_TYPE_AZ)
    _run([str(run.pk)], confirm=True)
    assert not Challenge.objects.filter(pk=run.pk).exists()
    assert not ChallengeSlot.objects.filter(challenge_id=run.pk).exists()
    # The point of it: Start now deals a FRESH run, where hiding would have resumed the old one.
    fresh = svc.start(profile, CHALLENGE_TYPE_AZ)
    assert fresh.pk != run.pk


def test_a_hidden_run_can_be_deleted_too():
    profile = _hunter()
    run = svc.start(profile, CHALLENGE_TYPE_AZ)
    svc.hide(run, profile)
    out = _run(['--user', 'tester', '--type', CHALLENGE_TYPE_AZ], confirm=True)   # the name match ignores case
    assert 'hidden' in out and not Challenge.objects.filter(pk=run.pk).exists()


def test_finding_by_owner_and_type_refuses_to_guess_between_runs():
    profile = _hunter()
    first = svc.start(profile, CHALLENGE_TYPE_AZ)
    Challenge.objects.filter(pk=first.pk).update(is_complete=True, completed_at=timezone.now())
    second = svc.start(profile, CHALLENGE_TYPE_AZ)
    with pytest.raises(CommandError) as err:
        _run(['--user', 'Tester', '--type', CHALLENGE_TYPE_AZ], confirm=True)
    assert str(first.pk) in str(err.value) and str(second.pk) in str(err.value)
    assert Challenge.objects.filter(pk__in=[first.pk, second.pk]).count() == 2


@pytest.mark.parametrize('args, message', [
    ([], 'Give a run id, or --user with --type.'),
    (['999999'], 'No run with id 999999.'),
    (['--user', 'Nobody', '--type', CHALLENGE_TYPE_AZ], "No profile with psn_username 'Nobody'."),
])
def test_bad_targets_are_refused_plainly(args, message):
    with pytest.raises(CommandError, match=message.replace('(', r'\(').replace(')', r'\)')):
        _run(args)


def test_an_id_and_an_owner_together_are_refused():
    run = svc.start(_hunter(), CHALLENGE_TYPE_AZ)
    with pytest.raises(CommandError, match='not both'):
        _run([str(run.pk), '--user', 'Tester', '--type', CHALLENGE_TYPE_AZ])


def test_a_run_that_paid_job_xp_is_refused_without_force():
    run = svc.start(_hunter(), CHALLENGE_TYPE_JOBS)
    _pay(run)
    with pytest.raises(CommandError, match='--force'):
        _run([str(run.pk)], confirm=True)
    assert Challenge.objects.filter(pk=run.pk).exists()


def test_force_deletes_a_paid_run_and_keeps_its_xp():
    run = svc.start(_hunter(), CHALLENGE_TYPE_JOBS)
    grant = _pay(run)
    out = _run([str(run.pk)], confirm=True, force=True)
    assert not Challenge.objects.filter(pk=run.pk).exists()
    # The ledger is append-only: a pay can be offset, never removed, and deleting a run is not un-earning it.
    assert ContractXPGrant.objects.filter(pk=grant.pk).exists()
    assert 'Kept: 1 job XP grant(s) worth 500 XP and 0 title(s); 0 owed square(s) lost.' in out


def test_a_run_that_granted_a_title_is_refused_without_force_and_keeps_it_with():
    profile = _hunter()
    run = svc.start(profile, CHALLENGE_TYPE_AZ)
    title = UserTitle.objects.create(profile=profile, title=Title.objects.create(name='A-Z Test'),
                                     source_type=rewards.TITLE_SOURCE, source_id=run.pk)
    with pytest.raises(CommandError, match='--force'):
        _run([str(run.pk)], confirm=True)
    _run([str(run.pk)], confirm=True, force=True)
    assert UserTitle.objects.filter(pk=title.pk).exists()


def test_rewards_are_matched_by_source_not_just_by_id():
    # A badge title or a quest grant that merely shares the run's (or a slot's) id is not this run's reward.
    profile = _hunter()
    run = svc.start(profile, CHALLENGE_TYPE_AZ)
    UserTitle.objects.create(profile=profile, title=Title.objects.create(name='Some Badge Title'),
                             source_type='badge', source_id=run.pk)
    out = _run([str(run.pk)])
    assert 'Paid: 0 job XP grant(s) worth 0 XP; 0 title(s).' in out


def test_the_xp_nav_marker_is_dropped_once_the_delete_commits(monkeypatch, django_capture_on_commit_callbacks):
    profile = _hunter()
    run = svc.start(profile, CHALLENGE_TYPE_AZ)
    forgotten = []
    monkeypatch.setattr(rewards, '_forget_xp_marker', lambda p: forgotten.append(p.pk))
    with django_capture_on_commit_callbacks(execute=True):
        _run([str(run.pk)], confirm=True)
    assert forgotten == [profile.pk]


def test_the_admin_stays_read_only():
    from django.contrib.admin.sites import site
    from challenges.admin import ChallengeAdmin
    assert ChallengeAdmin(Challenge, site).has_delete_permission(request=None) is False



def test_owed_job_xp_counts_as_a_reward_so_it_is_never_lost_by_accident():
    run = svc.start(_hunter(), CHALLENGE_TYPE_JOBS)
    slot = run.slots.first()
    # Finished, NOT claimed: no grant exists yet, only the square says XP is owed.
    ChallengeSlot.objects.filter(pk=slot.pk).update(
        is_completed=True, contract_name='Astro Bot', contract_slug='astro-bot',
        completed_at=timezone.now(), completed_via=COMPLETED_VIA_LIVE)
    with pytest.raises(CommandError, match='owed XP is lost'):
        _run([str(run.pk)], confirm=True)
    dry = _run([str(run.pk)])   # a dry run reports, including that the real one needs --force
    assert 'Owed: 1 finished square(s)' in dry and 'deleting it needs --force' in dry
    out = _run([str(run.pk)], confirm=True, force=True)
    assert '1 owed square(s) lost' in out and not Challenge.objects.filter(pk=run.pk).exists()


def test_a_finished_run_warns_about_later_completion_titles():
    profile = _hunter()
    run = svc.start(profile, CHALLENGE_TYPE_AZ)
    Challenge.objects.filter(pk=run.pk).update(is_complete=True, completed_at=timezone.now())
    assert "next finish of this type count as this one did" in _run([str(run.pk)])


def test_a_reward_landing_between_the_report_and_the_delete_stops_it(monkeypatch):
    from challenges.management.commands import delete_challenge
    run = svc.start(_hunter(), CHALLENGE_TYPE_AZ)
    clean = {'squares': 26, 'filled': 0, 'completed': 0, 'days': 0, 'grants': 0, 'xp': 0, 'titles': 0, 'owed': 0}
    reports = iter([clean, {**clean, 'titles': 1}])
    monkeypatch.setattr(delete_challenge.Command, '_report', staticmethod(lambda challenge: next(reports)))
    with pytest.raises(CommandError, match='while the report was printing'):
        _run([str(run.pk)], confirm=True)
    assert Challenge.objects.filter(pk=run.pk).exists()
