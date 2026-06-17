"""Tests for the Project acceptance endpoint (api/contract_views.py AcceptContractView).

The HTTP layer over contract_service.accept_contract / accept_contracts (the XP-grant math
itself is covered in test_contract_xp.py). These pin the request contract: the linked-profile
gate, single vs accept-all, the bad-input branches, idempotency, and the response shape.
"""
import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from trophies.models import (
    Contract, ContractMembership, ProfileJobXP, Job, Trophy,
)
from trophies.services import contract_service
from trophies.util_modules.constants import CONTRACT_XP_TOTAL
from tests.factories import (
    ConceptFactory, EarnedTrophyFactory, GameFactory, ProfileFactory, ProfileGameFactory,
)

pytestmark = pytest.mark.django_db

URL = reverse('api:project-accept')


def _linked_profile():
    return ProfileFactory(is_linked=True)


def _client(profile):
    client = APIClient()
    client.force_authenticate(user=profile.user)
    return client


def _claimable_contract(profile, slug, job_slugs=('gunslinger',)):
    """A live Contract whose platinum member is earned + already detected (reached),
    so it is claimable but not yet accepted."""
    contract = Contract.objects.create(name=slug, slug=slug, is_live=True)
    contract.jobs.set(Job.objects.filter(slug__in=job_slugs))
    concept = ConceptFactory()
    game = GameFactory(concept=concept)
    plat = Trophy.objects.create(game=game, trophy_id=1, trophy_type='platinum', trophy_name='Plat')
    ContractMembership.objects.create(contract=contract, concept=concept)
    EarnedTrophyFactory(profile=profile, trophy=plat, earned=True)
    ProfileGameFactory(profile=profile, game=game, progress=100, has_plat=True)
    contract_service.mark_contract_reached(profile, contract)
    return contract


def test_accept_single_banks_xp_and_returns_shape():
    profile = _linked_profile()
    contract = _claimable_contract(profile, 'proj-one')

    resp = _client(profile).post(URL, {'slug': 'proj-one'}, format='json')

    assert resp.status_code == 200
    assert resp.data['granted'] == CONTRACT_XP_TOTAL
    assert resp.data['accepted'] == ['proj-one']
    assert resp.data['claimable_count'] == 0
    assert ProfileJobXP.objects.get(profile=profile, job__slug='gunslinger').total_xp == CONTRACT_XP_TOTAL


def test_accept_all_banks_every_claimable():
    profile = _linked_profile()
    _claimable_contract(profile, 'proj-a', ('gunslinger',))
    _claimable_contract(profile, 'proj-b', ('mage',))

    resp = _client(profile).post(URL, {'all': True}, format='json')

    assert resp.status_code == 200
    assert resp.data['granted'] == 2 * CONTRACT_XP_TOTAL
    assert sorted(resp.data['accepted']) == ['proj-a', 'proj-b']
    assert resp.data['claimable_count'] == 0


def test_unlinked_profile_is_forbidden():
    profile = ProfileFactory(is_linked=False)
    _claimable_contract(profile, 'proj-locked')

    resp = _client(profile).post(URL, {'slug': 'proj-locked'}, format='json')

    assert resp.status_code == 403
    assert not ProfileJobXP.objects.filter(profile=profile).exists()  # no XP banked


def test_unknown_slug_is_404():
    profile = _linked_profile()

    resp = _client(profile).post(URL, {'slug': 'does-not-exist'}, format='json')

    assert resp.status_code == 404


def test_non_live_contract_is_404():
    profile = _linked_profile()
    Contract.objects.create(name='Dormant', slug='dormant', is_live=False)

    resp = _client(profile).post(URL, {'slug': 'dormant'}, format='json')

    assert resp.status_code == 404


def test_missing_slug_and_all_is_400():
    profile = _linked_profile()

    resp = _client(profile).post(URL, {}, format='json')

    assert resp.status_code == 400


def test_re_accept_grants_zero_and_reports_nothing_accepted():
    profile = _linked_profile()
    _claimable_contract(profile, 'proj-idem')
    _client(profile).post(URL, {'slug': 'proj-idem'}, format='json')  # first accept banks T

    resp = _client(profile).post(URL, {'slug': 'proj-idem'}, format='json')

    assert resp.status_code == 200
    assert resp.data['granted'] == 0
    assert resp.data['accepted'] == []  # nothing newly accepted


def test_anonymous_request_is_rejected():
    _claimable_contract(_linked_profile(), 'proj-anon')

    resp = APIClient().post(URL, {'slug': 'proj-anon'}, format='json')

    assert resp.status_code in (401, 403)  # IsAuthenticated guards the endpoint
