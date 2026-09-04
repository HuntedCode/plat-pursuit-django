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


@pytest.mark.parametrize('flags', [
    {'hide_hiddens': True},
    {'hide_zeros': True},
    {'hide_hiddens': True, 'hide_zeros': True},
])
def test_the_display_filters_do_not_look_like_drift(flags):
    """The failure that made this command worthless. Its three library totals are filter-respecting,
    and the two writers do not even use the SAME filters: `update_profile_games` honours hide_hiddens
    alone, `update_profile_trophy_counts` honours hide_hiddens AND hide_zeros. Reconciling against an
    unfiltered count reported DRIFT on a healthy profile with either toggle on -- and since any drift
    exits non-zero, the tool answered "no" for a hunter whose sync had landed perfectly.

    The fixture is deliberately the awkward one: a hidden game AND a zero-trophy game, so every
    filter combination actually excludes something.
    """
    from trophies.services.profile_stats_service import (
        update_profile_games, update_profile_trophy_counts,
    )

    profile = ProfileFactory(**flags)
    ProfileGameFactory(profile=profile, game=GameFactory(), progress=100,
                       earned_trophies_count=12, user_hidden=False)
    ProfileGameFactory(profile=profile, game=GameFactory(), progress=40,
                       earned_trophies_count=3, user_hidden=True)
    # The zero-trophy game carries UNEARNED trophies on purpose. Without that, excluding it changes
    # no aggregate the verifier checks, and the hide_zeros half of this test cannot fail whatever the
    # fixture numbers are -- removing rows worth 0 from a Sum of that column is a no-op by algebra.
    ProfileGameFactory(profile=profile, game=GameFactory(), progress=0,
                       earned_trophies_count=0, unearned_trophies_count=40, user_hidden=False)

    # Exactly what sync_complete does, so the stored values are correct BY CONSTRUCTION.
    update_profile_games(profile)
    update_profile_trophy_counts(profile)
    profile.refresh_from_db()

    out = _run(profile, verbose=True)

    assert 'DRIFT' not in out, f'reported drift on a healthy profile with {flags}'


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


def test_it_writes_nothing():
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


def test_it_writes_nothing_while_checking_ORPHANED_credit():
    """The read-only pin above uses a profile with no EarnedContract rows, so the orphan pass --
    including its second query for contracts the live sweep did not cover -- never executed under
    the no-writes wrapper. The guard did not actually cover the code it was extended with.

    Contract credit is the one thing this command inspects that has a WRITE-capable repair sitting
    next to it (`reconcile_contracts`), so "reports but never fixes" matters most precisely here.
    """
    from django.db import connection
    from django.utils import timezone

    from trophies.models import Contract, IGDBMatch, Job
    from trophies.services import contract_service
    from tests.factories import ConceptFactory, IGDBMatchFactory

    profile = ProfileFactory()
    contract = Contract.objects.create(name='Orphaned', slug='vps-orphan', is_live=True,
                                       igdb_id=770001)
    contract.jobs.set(Job.objects.filter(slug='gunslinger'))
    concept = ConceptFactory(anchor_migration_completed_at=timezone.now())
    IGDBMatchFactory(concept=concept, igdb_id=contract.igdb_id)
    game = GameFactory(concept=concept)
    plat = TrophyFactory(game=game, trophy_type='platinum')
    EarnedTrophyFactory(profile=profile, trophy=plat, earned=True)
    ProfileGameFactory(profile=profile, game=game, progress=100, has_plat=True)
    contract_service.mark_contract_reached(profile, contract)
    contract_service.accept_contract(profile, contract)

    # Orphan the credit AND unpublish the contract, so BOTH orphan passes have work to do.
    match = IGDBMatch.objects.get(concept=concept)
    match.igdb_id = 770002
    match.save(update_fields=['igdb_id'])
    Contract.objects.filter(pk=contract.pk).update(is_live=False)

    with connection.execute_wrapper(_no_writes):
        with pytest.raises(CommandError):        # it must REPORT the orphan (non-zero exit)
            _run(profile)
