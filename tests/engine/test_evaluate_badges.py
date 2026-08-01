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
    assert UserGroupBadge.objects.filter(profile=profile, group_badge=gb, status='earned').exists()


def test_series_flag_evaluates_a_dormant_badge():
    gb, game = _series_badge('dorm', is_live=False)   # dormant -> excluded from the default live run
    profile = ProfileFactory(psn_username='hunter03')
    _complete(profile, game)
    call_command('evaluate_badges', 'hunter03')        # default: live only -> nothing happens
    assert not UserGroupBadge.objects.filter(profile=profile).exists()
    out = StringIO()
    call_command('evaluate_badges', 'hunter03', '--series', 'dorm', '--dry-run', stdout=out)
    assert 'award' in out.getvalue().lower()           # --series reaches the dormant badge


def test_unknown_series_reports_error():
    ProfileFactory(psn_username='hunter04')
    err = StringIO()
    call_command('evaluate_badges', 'hunter04', '--series', 'nope', stderr=err)
    assert 'No group badges' in err.getvalue()


def test_username_not_found():
    err = StringIO()
    call_command('evaluate_badges', 'ghost', stderr=err)
    assert 'No profile found' in err.getvalue()


def test_all_flag_evaluates_every_profile():
    gb, game = _series_badge('gow')
    p1 = ProfileFactory(psn_username='hunterA')
    _complete(p1, game)
    p2 = ProfileFactory(psn_username='hunterB')
    _complete(p2, game)
    call_command('evaluate_badges', '--all')
    assert UserGroupBadge.objects.filter(group_badge=gb, status='earned').count() == 2


def test_series_alone_processes_only_players():
    gb, game = _series_badge('gow')                    # live
    player = ProfileFactory(psn_username='player1')
    _complete(player, game)
    ProfileFactory(psn_username='bystand')             # never touched the series
    call_command('evaluate_badges', '--series', 'gow')
    assert UserGroupBadge.objects.filter(profile=player, group_badge=gb, status='earned').exists()
    assert not UserGroupBadge.objects.filter(profile__psn_username='bystand').exists()   # not a player -> skipped


def test_batch_ranks_by_completion_date_not_alphabetical():
    gb, game = _series_badge('gow')
    # 'zoe' finished FIRST (2019); 'amy' finished LATER (2023). Alphabetically 'amy' sorts before 'zoe'.
    zoe = ProfileFactory(psn_username='zoe')
    _complete(zoe, game, when=_dt(2019))
    amy = ProfileFactory(psn_username='amy')
    _complete(amy, game, when=_dt(2023))
    call_command('evaluate_badges', '--series', 'gow')
    # Rank by who finished first, NOT username order.
    assert UserGroupBadge.objects.get(profile=zoe, group_badge=gb).earn_rank == 1
    assert UserGroupBadge.objects.get(profile=amy, group_badge=gb).earn_rank == 2


def _batch_query_count(n, slug):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext
    from trophies.models import Profile
    from trophies.services.badge_apply import evaluate_and_apply_batch
    gb, game = _series_badge(slug)
    for i in range(n):
        _complete(ProfileFactory(psn_username=f'{slug}{i}'), game, when=_dt(2019 + i))
    profiles = list(Profile.objects.filter(psn_username__startswith=slug))
    with CaptureQueriesContext(connection) as ctx:
        evaluate_and_apply_batch(profiles, [gb])
    return len(ctx.captured_queries)


def test_batch_catalog_fetched_once_flat_per_profile():
    # The per-profile marginal query cost must be small + CONSTANT (the two completion reads + one current-state
    # read + the award writes), NOT the ~6 catalog-prefetch queries. A per-profile slope near the re-fetch cost
    # would mean build_catalog is running per profile -- the exact regression this guards.
    q1 = _batch_query_count(1, 'qa')
    q4 = _batch_query_count(4, 'qb')
    per_profile = (q4 - q1) / 3
    assert per_profile <= 8, f"~{per_profile:.1f} queries/profile (N1={q1}, N4={q4}) -- catalog likely re-fetched"
