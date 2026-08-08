"""Tests for the milestones app: the six metrics, the seed catalogue, the recompute sweep + tier awarding,
the materialized progress read-model, rarity denorm + drift correction, and Discord role reconciliation
(highest-only). See docs/design/milestones-revamp.md."""
import itertools
from datetime import timedelta

import pytest
from django.core.management import call_command
from django.test import override_settings

from milestones import services
from milestones.metrics import metric_value
from milestones.models import EarnedMilestoneTier, Milestone, MilestoneTier, UserMilestone
from milestones.page import build_milestones_context
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
    # Counts held group badges (the new subsystem's UserGroupBadge), NOT the legacy tier count.
    from trophies.models import UserGroupBadge
    from tests.factories import GroupBadgeFactory
    p = ProfileFactory()
    assert metric_value('total_badges_earned', p) == 0
    UserGroupBadge.objects.create(profile=p, group_badge=GroupBadgeFactory())
    UserGroupBadge.objects.create(profile=p, group_badge=GroupBadgeFactory())
    assert metric_value('total_badges_earned', p) == 2


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
    assert Milestone.objects.count() == 8
    assert all(m.tiers.count() == 10 for m in Milestone.objects.all())

    call_command('seed_milestones')   # re-run
    assert Milestone.objects.count() == 8
    assert MilestoneTier.objects.count() == 80


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
    assert fixed == 1   # exactly the one corrupted row corrected


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


@pytest.mark.parametrize('verified,discord_id', [(False, 999301), (True, None)])
def test_reconcile_noop_when_not_linked(monkeypatch, django_capture_on_commit_callbacks, verified, discord_id):
    """Both gates: unverified, and verified-but-no-discord_id, must no-op."""
    _role_ladder()
    p = ProfileFactory(is_discord_verified=verified, discord_id=discord_id)
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


# ── H1: --profile grants ALREADY-earned roles (no new crossing needed) ────────────────────────────────────

def test_profile_command_reconciles_already_earned_roles(monkeypatch, django_capture_on_commit_callbacks):
    """After a role-bearing tier is already earned (e.g. a prior backfill), a `--profile` run must still grant
    the role -- reconcile fires unconditionally when reconcile_discord=True, not only on a fresh crossing."""
    _role_ladder()   # roles on tiers 2 (111) and 3 (222)
    p = ProfileFactory(is_discord_verified=True, discord_id=999401)
    _plats(p, 12)
    services.recompute_milestones(p, reconcile_discord=False)   # earn tiers WITHOUT reconciling (backfill)

    added = []
    monkeypatch.setattr('trophies.services.badge_service.notify_bot_role_earned',
                        lambda prof, rid: added.append(rid))
    monkeypatch.setattr('trophies.services.badge_service.notify_bot_role_removed', lambda prof, rid: None)

    with django_capture_on_commit_callbacks(execute=True):
        call_command('recompute_milestones', '--profile', p.psn_username)

    assert 222 in added   # highest already-earned role granted despite zero new crossings


def test_reconcile_empty_desired_removes_all_managed(monkeypatch, django_capture_on_commit_callbacks):
    """A verified hunter who has earned no role-bearing rung has every managed role stripped."""
    _role_ladder()
    p = ProfileFactory(is_discord_verified=True, discord_id=999402)   # 0 plats -> earns no role tier
    services.recompute_milestones(p, reconcile_discord=False)

    added, removed = [], []
    monkeypatch.setattr('trophies.services.badge_service.notify_bot_role_earned',
                        lambda prof, rid: added.append(rid))
    monkeypatch.setattr('trophies.services.badge_service.notify_bot_role_removed',
                        lambda prof, rid: removed.append(rid))

    with django_capture_on_commit_callbacks(execute=True):
        services.reconcile_discord_roles(p)

    assert added == []
    assert set(removed) == {111, 222}


# ── Retirement (is_active=False) ──────────────────────────────────────────────────────────────────────────

def test_retired_milestone_skipped_but_history_preserved():
    _role_ladder()
    p = ProfileFactory()
    _plats(p, 12)
    services.recompute_milestones(p, reconcile_discord=False)
    ph = Milestone.objects.get(slug='platinum-hunter')

    Milestone.objects.filter(pk=ph.pk).update(is_active=False)
    services.recompute_milestones(p, reconcile_discord=False)   # retired -> skipped, no crash

    assert EarnedMilestoneTier.objects.filter(profile=p, tier__milestone=ph).count() == 3   # history kept


def test_retired_milestone_role_is_removable():
    """A retired milestone's role stays in the MANAGED universe (so reconcile can strip it) but leaves DESIRED."""
    _role_ladder()
    p = ProfileFactory(is_discord_verified=True, discord_id=999403)
    _plats(p, 12)
    services.recompute_milestones(p, reconcile_discord=False)
    assert services.desired_milestone_roles(p) == {222}

    Milestone.objects.filter(slug='platinum-hunter').update(is_active=False)

    assert services.desired_milestone_roles(p) == set()        # inactive excluded from desired
    assert {111, 222} <= services.managed_milestone_roles()    # but still managed -> strippable


# ── M3: highest_tier_index ratchets (never under-reports an earned rung) ───────────────────────────────────

def test_highest_tier_index_ratchets_after_upward_reseed():
    call_command('seed_milestones')
    p = ProfileFactory()
    _plats(p, 12)   # earns Platinum Hunter tiers 1,5,10 -> index 3
    services.recompute_milestones(p, reconcile_discord=False)
    ph = Milestone.objects.get(slug='platinum-hunter')
    assert UserMilestone.objects.get(profile=p, milestone=ph).highest_tier_index == 3

    # Raise tier 3's threshold ABOVE the hunter's current value.
    MilestoneTier.objects.filter(milestone=ph, index=3).update(threshold=100)
    services.recompute_milestones(p, reconcile_discord=False)

    um = UserMilestone.objects.get(profile=p, milestone=ph)
    assert um.current_value == 12
    assert um.highest_tier_index == 3   # ratchets: the earned rung is preserved, not walked back to 2
    assert EarnedMilestoneTier.objects.filter(profile=p, tier__milestone=ph).count() == 3


# ── More metric edge cases ────────────────────────────────────────────────────────────────────────────────

def test_metric_playtime_handles_null_durations():
    p = ProfileFactory()
    ProfileGameFactory(profile=p, play_duration=None)
    ProfileGameFactory(profile=p, play_duration=timedelta(hours=4))
    assert metric_value('playtime_hours', p) == 4
    assert metric_value('playtime_hours', ProfileFactory()) == 0   # no games at all


def test_metric_pursuer_level_zero_jobs():
    assert metric_value('pursuer_level', ProfileFactory()) == 0


def test_recompute_milestone_with_no_tiers():
    m = Milestone.objects.create(slug='tierless', name='Tierless', metric='lifetime_platinums')
    p = ProfileFactory()
    _plats(p, 5)
    services.recompute_milestones(p, reconcile_discord=False)   # no crash
    um = UserMilestone.objects.get(profile=p, milestone=m)
    assert um.current_value == 5 and um.highest_tier_index == 0


def test_seed_preserves_discord_role_id_on_rerun():
    call_command('seed_milestones')
    t = MilestoneTier.objects.get(milestone__slug='platinum-hunter', index=2)
    MilestoneTier.objects.filter(pk=t.pk).update(discord_role_id=555)
    call_command('seed_milestones')
    t.refresh_from_db()
    assert t.discord_role_id == 555


# ── recompute_milestones management command ───────────────────────────────────────────────────────────────

def test_recompute_command_mass_path():
    call_command('seed_milestones')
    p = ProfileFactory()                          # registered member
    _plats(p, 12)
    scout = ProfileFactory(user=None)             # synced/scout, no site account -> must be skipped
    _plats(scout, 12)

    call_command('recompute_milestones')

    assert EarnedMilestoneTier.objects.filter(profile=p, tier__milestone__slug='platinum-hunter').count() == 3
    assert not EarnedMilestoneTier.objects.filter(profile=scout).exists()


def test_recompute_command_unknown_profile_errors():
    from django.core.management.base import CommandError
    with pytest.raises(CommandError):
        call_command('recompute_milestones', '--profile', 'no_such_hunter')


def test_recompute_on_sync_reconciles_only_on_role_bearing_crossing(monkeypatch):
    """The per-sync trigger reconciles Discord only when a tier WITH a role is newly crossed."""
    call_command('seed_milestones')
    p = ProfileFactory()
    calls = []
    monkeypatch.setattr(services, 'reconcile_discord_roles', lambda prof: calls.append(prof))

    # Cross rungs that carry no Discord role -> awarded, but NO reconcile.
    _plats(p, 12)                                  # Platinum Hunter tiers 1/5/10
    newly = services.recompute_on_sync(p)
    assert {t.threshold for t in newly} == {1, 5, 10}
    assert calls == []

    # Attach a role to the next rung (25), then cross it -> reconcile fires exactly once.
    ph = Milestone.objects.get(slug='platinum-hunter')
    t25 = ph.tiers.get(index=4)
    t25.discord_role_id = 999
    t25.save(update_fields=['discord_role_id'])
    _plats(p, 13)                                  # 12 -> 25 total, crosses the role-bearing rung
    newly = services.recompute_on_sync(p)
    assert [t.threshold for t in newly] == [25]
    assert calls == [p]


def test_recompute_reset_wipes_stale_and_reawards_cleanly():
    """--reset wipes earned/progress rows, then re-derives from the current metric with no stale rows and
    no double-counted earned_count."""
    call_command('seed_milestones')
    p = ProfileFactory()
    _plats(p, 12)   # Platinum Hunter: earns tiers 1/5/10, next = 25
    services.recompute_milestones(p, reconcile_discord=False)
    ph = Milestone.objects.get(slug='platinum-hunter')
    assert EarnedMilestoneTier.objects.filter(profile=p).count() == 3
    assert ph.tiers.get(index=1).earned_count == 1

    # A stale earned rung the profile no longer qualifies for (tier 6 = 100 plats, they only have 12).
    EarnedMilestoneTier.objects.create(profile=p, tier=ph.tiers.get(index=6))
    assert EarnedMilestoneTier.objects.filter(profile=p).count() == 4

    call_command('recompute_milestones', '--profile', p.psn_username, '--reset', '--yes')

    assert EarnedMilestoneTier.objects.filter(profile=p).count() == 3          # stale rung wiped, 3 re-awarded
    assert not EarnedMilestoneTier.objects.filter(profile=p, tier__index=6).exists()
    assert ph.tiers.get(index=1).earned_count == 1                             # corrected, not double-bumped
    assert ph.tiers.get(index=6).earned_count == 0
    assert UserMilestone.objects.filter(profile=p, milestone=ph).exists()      # progress read-model rebuilt


def test_recompute_reset_scoped_to_one_milestone():
    """--milestone narrows the wipe to a single ladder; other ladders are untouched."""
    call_command('seed_milestones')
    p = ProfileFactory(total_trophies=1234)   # Trophy Collector earns 100/500/1000
    _plats(p, 12)                             # Platinum Hunter earns 1/5/10
    services.recompute_milestones(p, reconcile_discord=False)

    ph = Milestone.objects.get(slug='platinum-hunter')
    tc = Milestone.objects.get(slug='trophy-collector')
    # A stale rung in each ladder the profile doesn't actually qualify for.
    EarnedMilestoneTier.objects.create(profile=p, tier=ph.tiers.get(index=9))
    EarnedMilestoneTier.objects.create(profile=p, tier=tc.tiers.get(index=9))

    call_command('recompute_milestones', '--profile', p.psn_username,
                 '--reset', '--milestone', 'trophy-collector', '--yes')

    # Trophy Collector was wiped + re-derived: its stale rung is gone.
    assert not EarnedMilestoneTier.objects.filter(profile=p, tier=tc.tiers.get(index=9)).exists()
    # Platinum Hunter was NOT in scope: its stale rung survives (proves the wipe was scoped).
    assert EarnedMilestoneTier.objects.filter(profile=p, tier=ph.tiers.get(index=9)).exists()


def test_recompute_milestone_scope_validates():
    from django.core.management.base import CommandError
    call_command('seed_milestones')
    with pytest.raises(CommandError):   # --milestone requires --reset
        call_command('recompute_milestones', '--milestone', 'platinum-hunter')
    with pytest.raises(CommandError):   # unknown slug
        call_command('recompute_milestones', '--reset', '--milestone', 'no-such-ladder', '--yes')


# ── Rarity denominator + page context (Phase 2 data layer) ────────────────────────────────────────────────

def test_refresh_total_hunters_counts_registered_members_only():
    ProfileFactory()               # has a site user
    ProfileFactory()               # has a site user
    ProfileFactory(user=None)      # synced/scout profile, no site account -> excluded from the denominator
    assert services.refresh_total_hunters() == 2


def test_tier_rarity_pct_math():
    assert services.tier_rarity_pct(1, denom=4) == 25.0
    assert services.tier_rarity_pct(3, denom=4) == 75.0
    assert services.tier_rarity_pct(5, denom=0) is None   # no denominator -> hide the line
    # Clamp: live earned_count can briefly exceed the nightly-cached denom -> never render >100%.
    assert services.tier_rarity_pct(6, denom=4) == 100.0


def test_build_context_for_linked_profile():
    call_command('seed_milestones')
    p = ProfileFactory()
    _plats(p, 12)   # Platinum Hunter: tiers 1/5/10 earned, next rung = 25
    services.recompute_milestones(p, reconcile_discord=False)

    ctx = build_milestones_context(p)
    assert ctx['ms_has_progress'] is True
    assert ctx['ms_total_milestones'] == 8

    ph = next(c for c in ctx['milestone_cards'] if c['slug'] == 'platinum-hunter')
    assert ph['value'] == 12
    assert ph['unit'] == 'platinums'   # the focal number's sub-label (metric -> noun)
    assert ph['action_url'] and ph['action_url'].endswith(f'/{p.psn_username}/')   # card deep-links to the profile
    assert ph['earned_count'] == 3

    # Spotlights: only Platinum Hunter has progress, so it's the closest; a rarest feat exists (earned tiers).
    assert ctx['ms_nearest']['slug'] == 'platinum-hunter'
    assert ctx['ms_nearest']['remaining'] == 13   # 25 (next rung) - 12
    assert ctx['ms_nearest']['action_url'] == ph['action_url']
    assert ctx['ms_rarest'] is not None and ctx['ms_rarest']['rarity_pct'] is not None
    assert ph['total_tiers'] == 10
    assert ph['maxed'] is False
    assert ph['next_tier']['threshold'] == 25
    assert [t['index'] for t in ph['tiers'] if t['earned']] == [1, 2, 3]
    assert [t['index'] for t in ph['tiers'] if t['is_next']] == [4]
    assert ph['progress_pct'] == 13   # (12-10)/(25-10) = 13%


def test_build_context_anonymous_has_no_progress():
    call_command('seed_milestones')
    ctx = build_milestones_context(None)

    assert ctx['ms_has_progress'] is False
    assert ctx['ms_earned_tiers'] == 0
    assert ctx['ms_nearest'] is None and ctx['ms_rarest'] is None   # no spotlights without a profile
    ph = next(c for c in ctx['milestone_cards'] if c['slug'] == 'platinum-hunter')
    assert ph['earned_count'] == 0
    assert ph['next_tier']['index'] == 1
    assert all(not t['earned'] for t in ph['tiers'])


def test_spotlights_selection():
    """_spotlights picks the closest-to-next non-maxed ladder + the rarest earned tier (pure selection)."""
    from milestones.page import _spotlights

    def card(slug, pct, rem, maxed, earned_rarities):
        nt = None if maxed else {'name': ''}
        return {
            'slug': slug, 'name': slug, 'icon': 'trophy', 'accent': '#111', 'unit': 'x',
            'value': 100 - rem, 'next_threshold': 100, 'progress_pct': pct, 'maxed': maxed,
            'next_tier': nt, 'action_url': f'/{slug}', 'action_label': slug,
            'tiers': [{'earned': True, 'rarity_pct': r, 'threshold': 10, 'name': ''} for r in earned_rarities],
        }

    cards = [
        card('a', pct=80, rem=20, maxed=False, earned_rarities=[30]),
        card('b', pct=20, rem=5, maxed=False, earned_rarities=[5]),     # rarest earned tier (5%)
        card('c', pct=100, rem=0, maxed=True, earned_rarities=[50]),    # maxed -> ineligible for nearest
    ]
    nearest, rarest = _spotlights(cards)
    assert nearest['slug'] == 'a'          # highest progress among non-maxed
    assert rarest['slug'] == 'b' and rarest['rarity_pct'] == 5

    # No earned tiers anywhere -> no rarest; all maxed -> no nearest.
    n2, r2 = _spotlights([card('z', pct=100, rem=0, maxed=True, earned_rarities=[])])
    assert n2 is None and r2 is None


def test_build_context_maxed_milestone():
    call_command('seed_milestones')
    p = ProfileFactory(total_trophies=60000)   # Trophy Collector final rung
    services.recompute_milestones(p, reconcile_discord=False)

    ctx = build_milestones_context(p)
    tc = next(c for c in ctx['milestone_cards'] if c['slug'] == 'trophy-collector')
    assert tc['maxed'] is True
    assert tc['next_tier'] is None
    assert tc['progress_pct'] == 100
    assert tc['earned_count'] == 10


# ── Page render (view + template) ─────────────────────────────────────────────────────────────────────────

def test_milestones_page_renders_for_linked_profile(client):
    from django.urls import reverse
    call_command('seed_milestones')
    services.refresh_total_hunters()
    p = ProfileFactory()
    _plats(p, 12)
    services.recompute_milestones(p, reconcile_discord=False)

    client.force_login(p.user)
    resp = client.get(reverse('milestones_list'))
    content = resp.content.decode()

    assert resp.status_code == 200
    assert 'Platinum Hunter' in content
    assert 'msc__ladder' in content            # the tier ladder rendered
    assert 'Milestones started' in content     # authed overview
    assert 'msc-spots' in content              # header spotlights (nearest + rarest)
    assert 'Closest milestone' in content
    assert 'msc--link' in content              # cards are actionable
    assert 'class="msc__action"' in content    # stretched-link overlay
    assert '{%' not in content and '{#' not in content


def test_milestones_page_renders_for_anonymous(client):
    from django.urls import reverse
    call_command('seed_milestones')
    resp = client.get(reverse('milestones_list'))
    content = resp.content.decode()

    assert resp.status_code == 200
    assert 'Platinum Hunter' in content
    assert 'Link your PSN account' in content   # anon preview nudge


def test_demo_context_covers_all_states():
    """The staff/DEBUG preview fabricates a spread across every visual state (writes nothing)."""
    from milestones.models import MilestoneTier
    from milestones.page import build_demo_context
    call_command('seed_milestones')

    ctx = build_demo_context(None)
    assert ctx['ms_preview'] is True and ctx['ms_has_progress'] is True
    assert len(ctx['milestone_cards']) == 8
    assert any(c['maxed'] for c in ctx['milestone_cards'])        # a maxed ladder (foil)
    assert any(not c['maxed'] for c in ctx['milestone_cards'])    # an in-progress ladder
    assert ctx['ms_nearest'] is not None
    assert ctx['ms_rarest'] is not None and 0 <= ctx['ms_rarest']['rarity_pct'] <= 100
    # The fabricated earned_count overrides are in-memory only -- nothing persisted.
    assert all(t.earned_count == 0 for t in MilestoneTier.objects.all())


@override_settings(DEBUG=False)
def test_preview_gated_to_staff(client):
    from django.urls import reverse
    call_command('seed_milestones')

    anon = client.get(reverse('milestones_list') + '?preview=1').content.decode()
    assert 'Preview mode' not in anon      # ignored for regular visitors

    p = ProfileFactory()
    p.user.is_staff = True
    p.user.save(update_fields=['is_staff'])
    client.force_login(p.user)
    staff = client.get(reverse('milestones_list') + '?preview=1').content.decode()
    assert 'Preview mode' in staff         # staff get the fabricated preview


def test_seed_sets_accent_and_context_and_page_pass_it(client):
    from django.urls import reverse
    call_command('seed_milestones')
    assert Milestone.objects.get(slug='completionist').accent == '#34d399'

    comp = next(c for c in build_milestones_context(None)['milestone_cards'] if c['slug'] == 'completionist')
    assert comp['accent'] == '#34d399'

    content = client.get(reverse('milestones_list')).content.decode()
    assert '--msc-accent: #34d399' in content   # rendered as the card's inline accent var


# ── Supporter metrics + grouping ──────────────────────────────────────────────────────────────────────────

def test_metric_community_months():
    from datetime import timedelta
    from django.utils import timezone
    p = ProfileFactory()
    p.user.date_joined = timezone.now() - timedelta(days=400)   # ~13 months since sign-up
    p.user.save(update_fields=['date_joined'])
    assert metric_value('community_months', p) == 13   # 400 // 30

    assert metric_value('community_months', ProfileFactory(user=None)) == 0   # no site account


def test_metric_premium_months():
    from datetime import timedelta
    from django.utils import timezone
    from users.models import SubscriptionPeriod
    p = ProfileFactory()
    now = timezone.now()
    SubscriptionPeriod.objects.create(user=p.user, started_at=now - timedelta(days=90),
                                      ended_at=now - timedelta(days=30), provider='stripe')   # 60 days
    SubscriptionPeriod.objects.create(user=p.user, started_at=now - timedelta(days=30),
                                      ended_at=None, provider='stripe')                        # 30 days, open
    assert metric_value('premium_months', p) == 3   # 90 // 30

    assert metric_value('premium_months', ProfileFactory()) == 0   # never subscribed


def test_page_groups_into_labelled_sections(client):
    from django.urls import reverse
    call_command('seed_milestones')
    content = client.get(reverse('milestones_list')).content.decode()

    assert 'Loyal Member' in content
    assert 'Premium Supporter' in content
    assert 'msc-section' in content            # group headers rendered
    assert '>Trophy Hunting<' in content       # the core group header
    assert '>Supporter<' in content            # the supporter group header
    # Core section leads (sort_order 10-60), Supporter follows (70-80).
    assert content.index('>Trophy Hunting<') < content.index('>Supporter<')
