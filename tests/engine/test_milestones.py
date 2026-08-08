"""Tests for the milestones app: the six metrics, the seed catalogue, the recompute sweep + tier awarding,
the materialized progress read-model, rarity denorm + drift correction, and Discord role reconciliation
(highest-only). See docs/design/milestones-revamp.md."""
import itertools
from datetime import timedelta

import pytest
from django.core.management import call_command

from milestones import services
from milestones.metrics import metric_value
from milestones.models import EarnedMilestoneTier, Milestone, MilestoneTier, UserMilestone
from tests.factories import ProfileFactory, ProfileGameFactory

pytestmark = pytest.mark.django_db

_job_seq = itertools.count(1)


def _plats(profile, n):
    for _ in range(n):
        ProfileGameFactory(profile=profile, has_plat=True)


def _fresh(profile):
    from trophies.models import Profile
    return Profile.objects.get(pk=profile.pk)


# ── Metrics ───────────────────────────────────────────────────────────────────────────────────────────────

def test_metric_lifetime_platinums():
    p = ProfileFactory()
    _plats(p, 3)
    ProfileGameFactory(profile=p, has_plat=False)   # not a platinum
    assert metric_value('lifetime_platinums', p) == 3


def test_metric_denorm_fields():
    p = ProfileFactory(total_trophies=1234, total_completes=7)
    assert metric_value('lifetime_trophies', p) == 1234
    assert metric_value('full_completions', p) == 7


def test_metric_total_badges_earned():
    from trophies.models import ProfileGamification
    p = ProfileFactory()
    assert metric_value('total_badges_earned', _fresh(p)) == 0   # no gamification row yet
    ProfileGamification.objects.create(profile=p, total_badges_earned=42)
    assert metric_value('total_badges_earned', _fresh(p)) == 42


def test_metric_pursuer_level():
    from trophies.models import Job, ProfileJobXP
    p = ProfileFactory()
    j1 = Job.objects.create(slug=f'job-{next(_job_seq)}', name='J1', discipline='combat')
    j2 = Job.objects.create(slug=f'job-{next(_job_seq)}', name='J2', discipline='mind')
    ProfileJobXP.objects.create(profile=p, job=j1, level=5)
    ProfileJobXP.objects.create(profile=p, job=j2, level=10)
    assert metric_value('pursuer_level', p) == 15


def test_metric_playtime_hours():
    p = ProfileFactory()
    ProfileGameFactory(profile=p, play_duration=timedelta(hours=3))
    ProfileGameFactory(profile=p, play_duration=timedelta(hours=2, minutes=30))
    assert metric_value('playtime_hours', p) == 5   # 5.5h floored


def test_unknown_metric_returns_zero():
    assert metric_value('does_not_exist', ProfileFactory()) == 0


# ── Seed catalogue ────────────────────────────────────────────────────────────────────────────────────────

def test_seed_catalogue_idempotent():
    call_command('seed_milestones')
    assert Milestone.objects.count() == 6
    assert all(m.tiers.count() == 10 for m in Milestone.objects.all())

    call_command('seed_milestones')   # re-run
    assert Milestone.objects.count() == 6
    assert MilestoneTier.objects.count() == 60


def test_seed_preserves_earned_count_on_rerun():
    call_command('seed_milestones')
    t = MilestoneTier.objects.get(milestone__slug='platinum-hunter', index=1)
    MilestoneTier.objects.filter(pk=t.pk).update(earned_count=5)
    call_command('seed_milestones')
    t.refresh_from_db()
    assert t.earned_count == 5   # upsert of thresholds must not reset the rarity counter


# ── Recompute sweep + awarding + progress ─────────────────────────────────────────────────────────────────

def test_recompute_awards_tiers_and_writes_progress():
    call_command('seed_milestones')
    p = ProfileFactory()
    _plats(p, 12)

    services.recompute_milestones(p, reconcile_discord=False)

    ph = Milestone.objects.get(slug='platinum-hunter')
    assert EarnedMilestoneTier.objects.filter(profile=p, tier__milestone=ph).count() == 3   # 1,5,10 <= 12
    um = UserMilestone.objects.get(profile=p, milestone=ph)
    assert um.current_value == 12
    assert um.highest_tier_index == 3
    # Every other metric is 0 for a fresh profile -> below its first threshold -> nothing else awarded.
    assert EarnedMilestoneTier.objects.filter(profile=p).count() == 3


def test_recompute_is_idempotent():
    call_command('seed_milestones')
    p = ProfileFactory()
    _plats(p, 12)
    services.recompute_milestones(p, reconcile_discord=False)
    services.recompute_milestones(p, reconcile_discord=False)

    ph = Milestone.objects.get(slug='platinum-hunter')
    assert EarnedMilestoneTier.objects.filter(profile=p, tier__milestone=ph).count() == 3
    t1 = MilestoneTier.objects.get(milestone=ph, index=1)
    assert t1.earned_count == 1   # not double-bumped on the second sweep


def test_recompute_awards_higher_tiers_on_growth():
    call_command('seed_milestones')
    p = ProfileFactory()
    _plats(p, 4)
    services.recompute_milestones(p, reconcile_discord=False)
    ph = Milestone.objects.get(slug='platinum-hunter')
    assert UserMilestone.objects.get(profile=p, milestone=ph).highest_tier_index == 1   # only tier 1 (>=1)

    _plats(p, 6)   # now 10 total -> tiers 1,5,10
    services.recompute_milestones(p, reconcile_discord=False)
    assert EarnedMilestoneTier.objects.filter(profile=p, tier__milestone=ph).count() == 3
    assert UserMilestone.objects.get(profile=p, milestone=ph).highest_tier_index == 3


# ── Rarity (denorm + drift correction) ────────────────────────────────────────────────────────────────────

def test_rarity_earned_count_bumps_across_profiles():
    call_command('seed_milestones')
    for _ in range(2):
        p = ProfileFactory()
        _plats(p, 1)
        services.recompute_milestones(p, reconcile_discord=False)

    t1 = MilestoneTier.objects.get(milestone__slug='platinum-hunter', index=1)
    assert t1.earned_count == 2


def test_recompute_tier_earned_counts_corrects_drift():
    call_command('seed_milestones')
    p = ProfileFactory()
    _plats(p, 1)
    services.recompute_milestones(p, reconcile_discord=False)
    t1 = MilestoneTier.objects.get(milestone__slug='platinum-hunter', index=1)
    MilestoneTier.objects.filter(pk=t1.pk).update(earned_count=99)   # corrupt

    fixed = services.recompute_tier_earned_counts()

    t1.refresh_from_db()
    assert t1.earned_count == 1
    assert fixed >= 1


# ── Unknown metric is skipped, not fatal ──────────────────────────────────────────────────────────────────

def test_recompute_skips_unknown_metric():
    m = Milestone.objects.create(slug='bogus', name='Bogus', metric='nope')
    MilestoneTier.objects.create(milestone=m, index=1, threshold=1)
    p = ProfileFactory()

    services.recompute_milestones(p, reconcile_discord=False)   # must not raise

    assert not EarnedMilestoneTier.objects.filter(profile=p).exists()
    assert not UserMilestone.objects.filter(profile=p, milestone=m).exists()


# ── Discord role reconciliation (highest-only) ────────────────────────────────────────────────────────────

def _role_ladder():
    """Seed + put Discord roles on Platinum Hunter tiers 2 (=5 plats) and 3 (=10 plats)."""
    call_command('seed_milestones')
    ph = Milestone.objects.get(slug='platinum-hunter')
    MilestoneTier.objects.filter(milestone=ph, index=2).update(discord_role_id=111)
    MilestoneTier.objects.filter(milestone=ph, index=3).update(discord_role_id=222)
    return ph


def test_desired_roles_highest_only():
    _role_ladder()
    p = ProfileFactory()
    _plats(p, 12)   # earns tiers 1,2,3
    services.recompute_milestones(p, reconcile_discord=False)

    assert services.desired_milestone_roles(p) == {222}     # only the highest role-bearing rung
    assert services.managed_milestone_roles() == {111, 222}


def test_reconcile_adds_highest_removes_stale(monkeypatch, django_capture_on_commit_callbacks):
    _role_ladder()
    p = ProfileFactory(is_discord_verified=True, discord_id=999001)
    _plats(p, 12)
    services.recompute_milestones(p, reconcile_discord=False)

    added, removed = [], []
    monkeypatch.setattr('trophies.services.badge_service.notify_bot_role_earned',
                        lambda prof, rid: added.append(rid))
    monkeypatch.setattr('trophies.services.badge_service.notify_bot_role_removed',
                        lambda prof, rid: removed.append(rid))

    with django_capture_on_commit_callbacks(execute=True):
        services.reconcile_discord_roles(p)

    assert set(added) == {222}       # holds only the highest bracket
    assert set(removed) == {111}     # the superseded lower bracket is removed


def test_reconcile_noop_when_not_discord_verified(monkeypatch, django_capture_on_commit_callbacks):
    _role_ladder()
    p = ProfileFactory(is_discord_verified=False)   # not verified
    _plats(p, 12)
    services.recompute_milestones(p, reconcile_discord=False)

    called = []
    monkeypatch.setattr('trophies.services.badge_service.notify_bot_role_earned',
                        lambda prof, rid: called.append(rid))
    monkeypatch.setattr('trophies.services.badge_service.notify_bot_role_removed',
                        lambda prof, rid: called.append(rid))

    with django_capture_on_commit_callbacks(execute=True):
        services.reconcile_discord_roles(p)

    assert called == []
