"""`rescale_pursuer_levels`: the one-off move of stored Pursuer Levels onto the levels-gained definition.

Pursuer Level used to sum every job's HELD level (a newcomer was 25); since 2026-10 it counts levels GAINED
(a newcomer is 1). The live reads changed with the code. The two stored copies, the Career XP board's
standing and the Pursuer rank milestone log, are moved by this command, run once at deploy.
"""
from io import StringIO

import pytest
from django.core.management import call_command

from tests.factories import ProfileFactory
from trophies.models import Job, ProfileCareerStanding, ProfileJobXP, ProgressionMilestone
from trophies.services.contract_service import recompute_career_standing
from trophies.util_modules.leveling import PURSUER_RANKS

pytestmark = pytest.mark.django_db


def _job(n=[0]):
    n[0] += 1
    return Job.objects.create(slug=f'rescale-job-{n[0]}', name=f'Rescale Job {n[0]}', discipline='combat')


def _old_scale_standing(profile, levels):
    """A hunter with job rows at `levels`, whose standing was written under the OLD rule (held levels,
    every untouched job counted at 1)."""
    for level in levels:
        ProfileJobXP.objects.create(profile=profile, job=_job(), total_xp=max(level - 1, 0) * 3000, level=level)
    held = sum(max(level, 1) for level in levels) + (Job.objects.count() - len(levels))
    ProfileCareerStanding.objects.update_or_create(
        profile=profile, defaults={'pursuer_level': held, 'total_xp': 1, 'is_linked': True})


def _run(*args):
    out = StringIO()
    call_command('rescale_pursuer_levels', *args, stdout=out)
    return out.getvalue()


def _correct(profile):
    """What the live recompute stores for this hunter, read without disturbing the row under test."""
    stored = ProfileCareerStanding.objects.get(profile=profile).pursuer_level
    recompute_career_standing(profile)
    correct = ProfileCareerStanding.objects.get(profile=profile).pursuer_level
    ProfileCareerStanding.objects.filter(profile=profile).update(pursuer_level=stored)
    return correct


def test_it_stores_what_the_live_recompute_stores_for_every_kind_of_hunter():
    """Differential: the command's SQL against `recompute_career_standing`, the writer every claim uses.
    A level-0 row is the `revoke_contract` case (created at the model default before its recompute)."""
    hunters = {
        'nothing claimed': [],
        'one job': [7],
        'several jobs': [3, 5, 40],
        'a level-0 row': [4, 0],
    }
    profiles = {}
    for name, levels in hunters.items():
        profiles[name] = ProfileFactory(is_linked=True)
        _old_scale_standing(profiles[name], levels)
    expected = {name: _correct(p) for name, p in profiles.items()}
    assert expected == {'nothing claimed': 1, 'one job': 7, 'several jobs': 1 + 2 + 4 + 39, 'a level-0 row': 4}

    _run()

    got = {name: ProfileCareerStanding.objects.get(profile=p).pursuer_level for name, p in profiles.items()}
    assert got == expected


def test_a_second_run_changes_nothing():
    profile = ProfileFactory(is_linked=True)
    _old_scale_standing(profile, [6, 2])

    assert 'Moved 1 career standing(s)' in _run()
    assert 'Moved 0 career standing(s) and 0 Pursuer rank' in _run()
    assert ProfileCareerStanding.objects.get(profile=profile).pursuer_level == 1 + 5 + 1


def test_a_dry_run_reports_and_writes_nothing():
    profile = ProfileFactory(is_linked=True)
    _old_scale_standing(profile, [6])
    before = ProfileCareerStanding.objects.get(profile=profile).pursuer_level

    assert 'Would move 1 career standing(s)' in _run('--dry-run')
    assert ProfileCareerStanding.objects.get(profile=profile).pursuer_level == before


def test_small_chunks_still_reach_every_row():
    profiles = [ProfileFactory(is_linked=True) for _ in range(5)]
    for p in profiles:
        _old_scale_standing(p, [3])

    assert 'Moved 5 career standing(s)' in _run('--chunk-size', '2')
    assert {ProfileCareerStanding.objects.get(profile=p).pursuer_level for p in profiles} == {3}


def test_rank_milestones_take_the_new_thresholds_by_key():
    """`level_at` records the threshold the rank was reached at, so it takes the rank's new threshold,
    looked up by key; a rerun changes nothing."""
    profile = ProfileFactory(is_linked=True)
    for key, name, old in (('recruit', 'Recruit', 35), ('warden', 'Warden', 305)):
        ProgressionMilestone.objects.create(
            profile=profile, kind=ProgressionMilestone.PURSUER_RANK, key=key, name=name, level_at=old)
    job_row = ProgressionMilestone.objects.create(
        profile=profile, kind=ProgressionMilestone.JOB_TIER, key='adept', name='Adept', level_at=25,
        job=Job.objects.first())

    assert 'and 2 Pursuer rank milestone(s)' in _run()
    assert 'and 0 Pursuer rank milestone(s)' in _run()

    thresholds = {key: lvl for lvl, key, _n, _d in PURSUER_RANKS}
    ranks = dict(ProgressionMilestone.objects.filter(
        profile=profile, kind=ProgressionMilestone.PURSUER_RANK).values_list('key', 'level_at'))
    assert ranks == {'recruit': thresholds['recruit'], 'warden': thresholds['warden']} == {'recruit': 10, 'warden': 280}
    job_row.refresh_from_db()
    assert job_row.level_at == 25, 'a job tier milestone is on the job-level scale, which did not change'


def test_reseeding_moves_the_pursuer_ascent_rungs_down_25_and_keeps_earned_history():
    """Seeded from the catalogue, so re-running `seed_milestones` at deploy is what moves them: in place,
    matched by rung index, so an earned rung keeps its earners. Starts from the OLD rungs, as prod will.
    The first rung must still clear the starting level, or every new hunter would earn it for nothing."""
    from milestones.models import EarnedMilestoneTier, Milestone, MilestoneTier

    old = [40, 75, 150, 250, 400, 600, 850, 1200, 1600, 2000]
    ascent = Milestone.objects.update_or_create(
        slug='pursuer-ascent', defaults={'name': 'Pursuer Ascent', 'metric': 'pursuer_level'})[0]
    ascent.tiers.all().delete()
    for i, threshold in enumerate(old, start=1):
        MilestoneTier.objects.create(milestone=ascent, index=i, threshold=threshold, earned_count=1 if i == 1 else 0)
    first = ascent.tiers.get(index=1)
    earned = EarnedMilestoneTier.objects.create(profile=ProfileFactory(), tier=first)

    call_command('seed_milestones', stdout=StringIO())

    rungs = list(ascent.tiers.order_by('index').values_list('threshold', flat=True))
    assert rungs == [r - 25 for r in old]
    assert rungs[0] > 1
    first.refresh_from_db()
    assert first.threshold == 15 and first.earned_count == 1
    assert EarnedMilestoneTier.objects.filter(pk=earned.pk, tier=first).exists(), 'an earned rung lost its earner'


def test_each_chunk_locks_its_rows_before_it_updates_them():
    """The live-claim race (the command's docstring): an UPDATE that waits on a claim's lock would compute
    from job XP read before the claim and overwrite the claim's correct level. Locking in a statement of
    its own first means the UPDATE starts after the claim commits and reads its job XP fresh. Pinned on the
    SQL order, since a two-connection test would need `transaction=True`, which the suite keeps out (see
    docs/guides/testing.md); the race itself was exercised once with two real connections by hand."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    for _ in range(3):
        _old_scale_standing(ProfileFactory(is_linked=True), [4])

    with CaptureQueriesContext(connection) as ctx:
        _run('--chunk-size', '2')

    sql = [q['sql'] for q in ctx.captured_queries]
    locks = [i for i, s in enumerate(sql) if 'FOR UPDATE' in s and 'careerstanding' in s.lower()]
    updates = [i for i, s in enumerate(sql) if s.startswith('UPDATE') and 'pursuer_level' in s]
    assert len(locks) == len(updates) == 2, (locks, updates)   # 3 rows in chunks of 2
    assert all(lock < update for lock, update in zip(locks, updates)), 'a chunk updated before it locked'
