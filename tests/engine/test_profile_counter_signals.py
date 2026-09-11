"""The `EarnedTrophy` -> `Profile` counter signals, and the invariant they have to hold.

Five columns move together: the four tier counters and `total_trophies_raw`, which is their sum. The last
one is not merely a convenience -- it is the Trophies board's MEMBERSHIP RULE and its TIEBREAK, so drift
there silently reorders a public board, and a hunter whose raw total reaches 0 disappears from it.

`recalc_profile_counters` rebuilds all five nightly from ground truth. These tests cover the incremental
path that runs in between, which had no tests at all before the raw column existed.
"""
import pytest

from trophies.models import EarnedTrophy, Profile
from tests.factories import EarnedTrophyFactory, GameFactory, ProfileFactory, TrophyFactory

pytestmark = pytest.mark.django_db


def _earn(profile, game, tier):
    return EarnedTrophyFactory(profile=profile, trophy=TrophyFactory(game=game, trophy_type=tier),
                               earned=True)


def _counters(profile):
    profile.refresh_from_db()
    return (profile.total_bronzes, profile.total_silvers, profile.total_golds, profile.total_plats,
            profile.total_trophies_raw)


def test_earning_moves_the_tier_and_the_raw_total_together():
    profile = ProfileFactory(is_linked=True)
    game = GameFactory()

    _earn(profile, game, 'bronze')
    _earn(profile, game, 'platinum')

    assert _counters(profile) == (1, 0, 0, 1, 2)


def test_unearning_moves_them_back():
    """A trophy can flip from earned to not: PSN restores a deleted game, or a resync corrects a bad
    write. The counters have to follow in both directions or they ratchet upward forever."""
    profile = ProfileFactory(is_linked=True)
    row = _earn(profile, GameFactory(), 'gold')
    assert _counters(profile) == (0, 0, 1, 0, 1)

    row.earned = False
    row._previous_earned = True
    row.save()

    assert _counters(profile) == (0, 0, 0, 0, 0)


def test_deleting_an_earned_row_moves_them_back():
    profile = ProfileFactory(is_linked=True)
    row = _earn(profile, GameFactory(), 'silver')
    assert _counters(profile) == (0, 1, 0, 0, 1)

    row.delete()

    assert _counters(profile) == (0, 0, 0, 0, 0)


def test_the_raw_total_is_always_the_sum_of_the_tiers():
    """The invariant, over a mixed sequence. Asserted as a RELATIONSHIP rather than against literals: the
    point is that the five figures agree, and literals would let them drift together."""
    profile = ProfileFactory(is_linked=True)
    game = GameFactory()
    rows = [_earn(profile, game, t) for t in ('bronze', 'bronze', 'silver', 'gold', 'platinum')]
    rows[0].delete()
    rows[3].delete()

    bronze, silver, gold, plat, raw = _counters(profile)
    assert raw == bronze + silver + gold + plat


def test_a_decrement_cannot_drive_the_raw_total_negative():
    """REGRESSION, and it was a 500 rather than a wrong number.

    `total_trophies_raw` is a PositiveIntegerField with a Postgres CHECK. The decrement is guarded by the
    caller's `{tier}__gt: 0` filter, which guards the TIER -- and the two can disagree: staff edit the
    tier counters in the admin, and migration 0333 leaves a window where the raw column sits at 0 while
    the tiers are already populated. Tier at 1, raw at 0, delete the trophy: the filter passes, raw goes
    to -1, and IntegrityError is raised inside `post_delete` -- inside whatever transaction sync is
    running.

    Floored instead. Drifting HIGH merely misplaces somebody until the nightly reconcile; raising on the
    sync path does not recover in the same cheap way.
    """
    profile = ProfileFactory(is_linked=True)
    row = _earn(profile, GameFactory(), 'bronze')
    # The desynced state, written the way the admin would: tier positive, raw behind it.
    Profile.objects.filter(pk=profile.pk).update(total_bronzes=1, total_trophies_raw=0)

    row.delete()          # must not raise

    profile.refresh_from_db()
    assert profile.total_trophies_raw == 0, 'the floored decrement did not hold at zero'
    assert not EarnedTrophy.objects.filter(pk=row.pk).exists()


def test_an_unearned_row_does_not_touch_the_counters():
    """`EarnedTrophy` rows exist for trophies a hunter has NOT earned -- that is how progress is tracked.
    Counting them would inflate every figure on the board."""
    profile = ProfileFactory(is_linked=True)
    EarnedTrophyFactory(profile=profile, trophy=TrophyFactory(game=GameFactory(), trophy_type='gold'),
                        earned=False)

    assert _counters(profile) == (0, 0, 0, 0, 0)


# ---------------------------------------------------------------- the nightly reconcile ------------------

def test_the_nightly_reconcile_repairs_all_five_counters():
    """The safety net under the incremental path, and the branch's central claim about why
    `total_trophies_raw` is trustworthy where `total_trophies` is not -- previously untested.

    `recalc_profile_counters` rebuilds from `EarnedTrophy` ground truth, so it repairs drift from any
    source: a bulk write that bypassed the signals, a handler that raised, a staff edit in the admin, or
    the floored decrement above leaving the raw total high.
    """
    from django.core.management import call_command

    profile = ProfileFactory(is_linked=True)
    game = GameFactory()
    for tier in ('bronze', 'bronze', 'gold', 'platinum'):
        _earn(profile, game, tier)

    Profile.objects.filter(pk=profile.pk).update(
        total_bronzes=99, total_silvers=99, total_golds=0, total_plats=0, total_trophies_raw=1234)

    call_command('recalc_profile_counters')

    assert _counters(profile) == (2, 0, 1, 1, 4), 'the reconcile did not rebuild every counter'


def test_the_reconcile_keeps_the_raw_total_equal_to_the_sum():
    """Asserted as the RELATIONSHIP, not against literals: the five figures have to agree, and literals
    would let them drift together into a new wrong answer."""
    from django.core.management import call_command

    profile = ProfileFactory(is_linked=True)
    game = GameFactory()
    for tier in ('silver', 'gold', 'gold', 'platinum'):
        _earn(profile, game, tier)
    Profile.objects.filter(pk=profile.pk).update(total_trophies_raw=0)

    call_command('recalc_profile_counters')

    bronze, silver, gold, plat, raw = _counters(profile)
    assert raw == bronze + silver + gold + plat == 4
