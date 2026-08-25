"""`verify_profile_sync` -- the one command that answers "did the sync actually land".

The rebuild ran for months without being exercised against a real sync, because that question had no
single answer. Every denormalized value a rebuilt page renders is written by the sync path or the
nightly chain, and a broken writer shows up as a plausible zero rather than an error, so the failure
mode this guards is "the page looks fine and is wrong".

These pins are about the two properties that make it trustworthy: it must catch real drift, and it
must never write anything.
"""
import io

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from trophies.models import UserGroupBadge
from tests.factories import (
    BadgeSeriesFactory, EarnedTrophyFactory, GameFactory, GroupBadgeFactory, ProfileFactory,
    ProfileGameFactory, TrophyFactory,
)

pytestmark = pytest.mark.django_db


def _run(profile, **opts):
    out = io.StringIO()
    call_command('verify_profile_sync', profile.psn_username, stdout=out, stderr=out, **opts)
    return out.getvalue()


def _earn(profile, game, trophy_type):
    trophy = TrophyFactory(game=game, trophy_type=trophy_type)
    EarnedTrophyFactory(profile=profile, trophy=trophy, earned=True)


def test_a_consistent_profile_passes():
    profile = ProfileFactory()

    out = _run(profile, verbose=True)

    assert 'agree with ground truth' in out
    assert 'DRIFT' not in out


def test_it_catches_a_trophy_counter_that_drifted():
    """`total_bronzes` and friends are maintained by the EarnedTrophy signals during sync. Drift here
    means a signal did not fire, and the navbar has been showing the wrong number ever since."""
    profile = ProfileFactory()
    game = GameFactory()
    _earn(profile, game, 'bronze')
    _earn(profile, game, 'bronze')
    # Put the denorm back to a lie, as a missed signal would.
    type(profile).objects.filter(pk=profile.pk).update(total_bronzes=0)

    with pytest.raises(CommandError):
        _run(profile)


def test_it_catches_library_totals_that_no_cron_reconciles():
    """`total_games` / `total_completes` are written ONLY by sync_complete and the settings POST --
    nothing nightly repairs them, so a missed write persists until the hunter syncs again."""
    profile = ProfileFactory()
    ProfileGameFactory(profile=profile, game=GameFactory(), progress=100,
                       earned_trophies_count=0)

    with pytest.raises(CommandError):
        _run(profile)

    out = io.StringIO()
    try:
        call_command('verify_profile_sync', profile.psn_username, stdout=out, stderr=out)
    except CommandError:
        pass
    assert 'total_games' in out.getvalue()


def test_it_catches_a_held_badge_with_no_standing_row():
    """The Collection reads `SeriesBadgeStanding` and never live-evaluates, so a hold without a
    standing is not a visible error -- the badge simply is not there."""
    profile = ProfileFactory()
    UserGroupBadge.objects.create(
        profile=profile, group_badge=GroupBadgeFactory(series=BadgeSeriesFactory()),
    )

    with pytest.raises(CommandError):
        _run(profile)


def test_it_writes_nothing(django_assert_num_queries):
    """A verifier that repairs what it measures cannot be trusted to measure. It also has to be safe
    to point at a live profile."""
    from django.db import connection

    profile = ProfileFactory()
    game = GameFactory()
    _earn(profile, game, 'gold')
    type(profile).objects.filter(pk=profile.pk).update(total_golds=0)

    before = dict(
        golds=profile.__class__.objects.values_list('total_golds', flat=True).get(pk=profile.pk),
    )

    with connection.execute_wrapper(_no_writes):
        with pytest.raises(CommandError):
            _run(profile)

    after = profile.__class__.objects.values_list('total_golds', flat=True).get(pk=profile.pk)
    assert after == before['golds'], 'the verifier repaired the drift it was meant to report'


def _no_writes(execute, sql, params, many, context):
    """Fail loudly on any statement that mutates. SAVEPOINT/RELEASE are the test transaction's own."""
    stripped = sql.lstrip().upper()
    for verb in ('INSERT ', 'UPDATE ', 'DELETE '):
        if stripped.startswith(verb):
            raise AssertionError(f'verify_profile_sync issued a write: {sql[:120]}')
    return execute(sql, params, many, context)
