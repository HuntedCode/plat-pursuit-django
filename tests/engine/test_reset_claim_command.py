"""The reset_claim management command: rewinds a dev profile's Contract claim state so the real
claim flow can be re-tested. DEV-only (guarded on settings.DEBUG)."""
import itertools

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from django.utils import timezone

from trophies.models import (
    Contract, ContractXPGrant, EarnedContract, Job, ProfileJobXP,
    ProgressionMilestone, Trophy,
)
from trophies.services import contract_service
from tests.factories import (
    ConceptFactory, EarnedTrophyFactory, GameFactory, IGDBMatchFactory, ProfileFactory,
    ProfileGameFactory,
)

pytestmark = pytest.mark.django_db

_igdb_seq = itertools.count(20001)


def _claimed_contract(profile, slug, job_slugs, *, xp_override=None):
    """A live Contract that's been reached AND accepted (XP banked), so there's state to rewind."""
    igdb_id = next(_igdb_seq)
    concept = ConceptFactory(anchor_migration_completed_at=timezone.now())
    IGDBMatchFactory(concept=concept, igdb_id=igdb_id)
    game = GameFactory(concept=concept)
    plat = Trophy.objects.create(game=game, trophy_id=1, trophy_type='platinum', trophy_name='Plat')
    EarnedTrophyFactory(profile=profile, trophy=plat, earned=True)
    ProfileGameFactory(profile=profile, game=game, progress=100, has_plat=True)
    c = Contract.objects.create(name=slug, slug=slug, is_live=True, igdb_id=igdb_id,
                                xp_total_override=xp_override)
    c.jobs.set(Job.objects.filter(slug__in=job_slugs))
    contract_service.mark_contract_reached(profile, c)
    contract_service.accept_contract(profile, c)
    return c


@override_settings(DEBUG=True)
def test_full_reset_makes_everything_claimable_again():
    profile = ProfileFactory(psn_username='dev-reset')
    _claimed_contract(profile, 'r-1', ['gunslinger'], xp_override=90000)
    _claimed_contract(profile, 'r-2', ['mage'], xp_override=6000)
    assert ProfileJobXP.objects.filter(profile=profile, total_xp__gt=0).exists()
    assert ProgressionMilestone.objects.filter(profile=profile).exists()
    assert contract_service.claimable_contracts(profile).count() == 0   # both accepted

    call_command('reset_claim', '--user', 'dev-reset')

    # XP ledger + cache + milestones wiped; both Contracts claimable again (reached stamps kept).
    assert not ContractXPGrant.objects.filter(profile=profile).exists()
    assert not ProgressionMilestone.objects.filter(profile=profile).exists()
    assert not ProfileJobXP.objects.filter(profile=profile, total_xp__gt=0).exists()
    assert contract_service.claimable_contracts(profile).count() == 2
    # And a fresh claim re-fires from scratch (first_claim True again).
    result = contract_service.claim(profile, all_claimable=True)
    assert result['first_claim'] is True and result['xp'] > 0


@override_settings(DEBUG=True)
def test_single_contract_reset_only_touches_that_one():
    profile = ProfileFactory(psn_username='dev-one')
    _claimed_contract(profile, 'keep', ['gunslinger'], xp_override=6000)
    _claimed_contract(profile, 'drop', ['mage'], xp_override=6000)

    call_command('reset_claim', '--user', 'dev-one', '--contract', 'drop')

    claimable = {ec.contract.slug for ec in contract_service.claimable_contracts(profile)}
    assert claimable == {'drop'}                                        # only 'drop' un-accepted
    assert ContractXPGrant.objects.filter(profile=profile, earned_contract__contract__slug='keep').exists()
    assert not ContractXPGrant.objects.filter(profile=profile, earned_contract__contract__slug='drop').exists()


@override_settings(DEBUG=False)
def test_refuses_to_run_outside_debug():
    ProfileFactory(psn_username='prod-user')
    with pytest.raises(CommandError):
        call_command('reset_claim', '--user', 'prod-user')


@override_settings(DEBUG=True)
def test_unknown_user_errors():
    with pytest.raises(CommandError):
        call_command('reset_claim', '--user', 'nobody')
