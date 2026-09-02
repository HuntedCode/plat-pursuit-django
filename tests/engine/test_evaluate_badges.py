"""Integration tests for the evaluate_badges management command (the manual trigger + verifier)."""
import datetime as dt
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from trophies.models import UserGroupBadge, ProfileGame, TrophyGroup, ProfileTrophyGroup
from tests.factories import (
    ProfileFactory, ConceptFactory, GameFactory, StageFactory,
    PlatformGroupFactory, BadgeSeriesFactory, GroupBadgeFactory,
    BadgeFactory, UserBadgeFactory,
)

pytestmark = pytest.mark.django_db


def _ultra():
    return PlatformGroupFactory(key='ultra-hd', name='Ultra HD', platforms=['PS4', 'PS5'], exclude_delisted=True)


def _dt(year):
    return timezone.make_aware(dt.datetime(year, 1, 1))


def _complete(profile, game, when=None):
    ProfileGame.objects.update_or_create(profile=profile, game=game, defaults={'progress': 50})
    tg, _ = TrophyGroup.objects.get_or_create(game=game, trophy_group_id='default', defaults={'trophy_group_name': 'Base'})
    ProfileTrophyGroup.objects.update_or_create(
        profile=profile, trophy_group=tg, defaults={'progress': 100, 'last_trophy_at': when or timezone.now()},
    )


def _series_badge(slug='gow', is_live=True):
    series = BadgeSeriesFactory(series_slug=slug, name='God of War')
    stage = StageFactory(series_slug=slug, stage_number=1)
    concept = ConceptFactory()
    stage.concepts.add(concept)
    game = GameFactory(concept=concept, title_platform=['PS5'])
    gb = GroupBadgeFactory(series=series, platform_group=_ultra(), is_live=is_live)
    return gb, game


def test_dry_run_previews_without_writing():
    gb, game = _series_badge('gow')
    profile = ProfileFactory(psn_username='hunter01')
    _complete(profile, game)
    out = StringIO()
    call_command('evaluate_badges', 'hunter01', '--dry-run', stdout=out)
    assert 'award' in out.getvalue().lower()
    assert not UserGroupBadge.objects.filter(profile=profile).exists()   # preview wrote nothing


def test_write_applies_the_earn():
    gb, game = _series_badge('gow')
    profile = ProfileFactory(psn_username='hunter02')
    _complete(profile, game)
    call_command('evaluate_badges', 'hunter02')
    assert UserGroupBadge.objects.filter(profile=profile, group_badge=gb).exists()


def test_a_dormant_badge_can_be_PREVIEWED_but_never_written():
    """Releasing a badge is what makes it evaluable, and `--series` used to be a way around that.

    A write run through a dormant edition did two things nothing undoes: it counted that edition's XP in
    the holder's Badge Points (`_live_standings` gates the profile-wide sum at SERIES level, so one live
    sibling carries the whole series through), and it created a `UserGroupBadge` the nightly
    `evaluate_badges --all` can never revoke, because that run is live-scoped and never revisits it.

    Previewing one is still legitimate -- it is how a curator checks a badge before launching it -- so the
    capability survives as `--include-dormant`, welded to `--dry-run`.
    """
    _, game = _series_badge('dorm', is_live=False)
    profile = ProfileFactory(psn_username='hunter03')
    _complete(profile, game)

    # 1. The default run does not reach it.
    call_command('evaluate_badges', 'hunter03')
    assert not UserGroupBadge.objects.filter(profile=profile).exists()

    # 2. Nor does a plain --series run, which is the change: it now says so instead of writing.
    err = StringIO()
    call_command('evaluate_badges', 'hunter03', '--series', 'dorm', stderr=err)
    assert 'No LIVE group badges' in err.getvalue()
    assert not UserGroupBadge.objects.filter(profile=profile).exists(), (
        'a dormant edition was awarded on a write run'
    )

    # 3. --include-dormant WITHOUT --dry-run is refused rather than honoured.
    err = StringIO()
    call_command('evaluate_badges', 'hunter03', '--series', 'dorm', '--include-dormant', stderr=err)
    assert 'requires --series and --dry-run' in err.getvalue()
    assert not UserGroupBadge.objects.filter(profile=profile).exists()

    # 4. The preview still works, which is the whole point of keeping the flag.
    out = StringIO()
    call_command('evaluate_badges', 'hunter03', '--series', 'dorm',
                 '--include-dormant', '--dry-run', stdout=out)
    assert 'award' in out.getvalue().lower()
    assert not UserGroupBadge.objects.filter(profile=profile).exists(), 'a dry run wrote'


def test_unknown_series_reports_error():
    ProfileFactory(psn_username='hunter04')
    err = StringIO()
    call_command('evaluate_badges', 'hunter04', '--series', 'nope', stderr=err)
    assert 'No group badges' in err.getvalue()


def test_username_not_found():
    err = StringIO()
    call_command('evaluate_badges', 'ghost', stderr=err)
    assert 'No profile found' in err.getvalue()


def test_all_flag_evaluates_every_profile_INCLUDING_UNLINKED_ONES():
    """`--all` is `Profile.objects.exclude(psn_username='')` -- every SCRAPED profile, not every linked
    one. That distinction is ~300,000 rows against ~50,000, and two other places depend on it being true:
    the command's own help text, and `badge_leaderboards._linked`, whose justification for gating the
    boards at READ is that standings for unlinked profiles "are real rows, not a hypothetical".

    The unlinked profile is the whole point of this test. It used to be implicit -- `ProfileFactory` left
    `is_linked` at the model default of False, so every profile here was unlinked by accident. When the
    factory was corrected to match its own docstring, this test kept passing while silently losing the
    only thing it proved: narrowing `--all` to `filter(is_linked=True)` left it green.
    """
    gb, game = _series_badge('gow')
    linked = ProfileFactory(psn_username='hunterA')
    _complete(linked, game)
    scraped = ProfileFactory(psn_username='hunterC', is_linked=False)
    _complete(scraped, game)

    call_command('evaluate_badges', '--all')

    assert UserGroupBadge.objects.filter(group_badge=gb, profile=linked).exists()
    assert UserGroupBadge.objects.filter(group_badge=gb, profile=scraped).exists(), (
        '--all skipped an unlinked profile; it is documented to walk every profile with a PSN username'
    )


def test_series_alone_processes_only_players():
    gb, game = _series_badge('gow')                    # live
    player = ProfileFactory(psn_username='player1')
    _complete(player, game)
    ProfileFactory(psn_username='bystand')             # never touched the series
    call_command('evaluate_badges', '--series', 'gow')
    assert UserGroupBadge.objects.filter(profile=player, group_badge=gb).exists()
    assert not UserGroupBadge.objects.filter(profile__psn_username='bystand').exists()   # not a player -> skipped


def test_batch_earned_at_reflects_completion_date():
    gb, game = _series_badge('gow')
    # 'zoe' finished FIRST (2019); 'amy' finished LATER (2023). earned_at (the derived-rank sort key) must
    # reflect completion date, NOT username order.
    zoe = ProfileFactory(psn_username='zoe')
    _complete(zoe, game, when=_dt(2019))
    amy = ProfileFactory(psn_username='amy')
    _complete(amy, game, when=_dt(2023))
    call_command('evaluate_badges', '--series', 'gow')
    assert (UserGroupBadge.objects.get(profile=zoe, group_badge=gb).earned_at
            < UserGroupBadge.objects.get(profile=amy, group_badge=gb).earned_at)


def test_batch_builds_catalog_once_regardless_of_profile_count(monkeypatch):
    # The catalog-hoist invariant: build_catalog runs ONCE per batch, not per profile (the O(N x catalog)
    # regression the split fixed). A direct spy is robust to the per-profile apply/recompute query cost.
    import trophies.services.badge_apply as ba
    from trophies.models import Profile
    calls = {'n': 0}
    original = ba.build_catalog
    monkeypatch.setattr(ba, 'build_catalog', lambda gbs: (calls.__setitem__('n', calls['n'] + 1), original(gbs))[1])

    gb, game = _series_badge('spy')
    for i in range(4):
        _complete(ProfileFactory(psn_username=f'spy{i}'), game, when=_dt(2019 + i))
    profiles = list(Profile.objects.filter(psn_username__startswith='spy'))
    ba.evaluate_and_apply_batch(profiles, [gb])
    assert calls['n'] == 1


def test_compare_legacy_reports_kept_lost_gained():
    gb, game = _series_badge('gow')
    # kept: held a legacy tier AND completes under the new engine.
    kept = ProfileFactory(psn_username='kept1')
    UserBadgeFactory(profile=kept, badge=BadgeFactory(series_slug='gow', tier=2))
    _complete(kept, game)
    # lost: held a legacy tier but does NOT complete under the new engine (played, not finished).
    lost = ProfileFactory(psn_username='lost1')
    UserBadgeFactory(profile=lost, badge=BadgeFactory(series_slug='gow', tier=1))
    ProfileGame.objects.create(profile=lost, game=game, progress=40)
    # gained: no legacy badge, but completes under the new engine.
    gained = ProfileFactory(psn_username='gain1')
    _complete(gained, game)

    out = StringIO()
    call_command('evaluate_badges', '--series', 'gow', '--compare-legacy', stdout=out)
    text = out.getvalue()
    assert 'old 2' in text and 'kept 1' in text and 'lost 1' in text and 'gained 1' in text
    assert 'lost1' in text                       # the lost profile is sampled with its username + old tier
    assert not UserGroupBadge.objects.exists()   # read-only: writes nothing
