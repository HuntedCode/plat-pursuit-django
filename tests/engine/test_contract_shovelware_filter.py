"""The Contract admin's "holds shovelware" filter (contracts_service.holds_shovelware_q): which
contracts, live or staged, a shovelware-flagged game can satisfy. Membership follows the ENGINE:
home members at the raw igdb_id (anchored + trusted), plus the concepts of ANY satisfier bundle."""
import pytest
from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory
from django.utils import timezone

from tests.factories import ConceptFactory, GameFactory, IGDBMatchFactory
from trophies.admin import ContractAdmin, HoldsShovelwareFilter
from trophies.models import Contract, ContractBundle

pytestmark = pytest.mark.django_db


def _filtered(value):
    """Contract names the filter returns for `value` ('yes' / 'no' / None for unfiltered)."""
    params = {'shovelware': [value]} if value else {}
    f = HoldsShovelwareFilter(RequestFactory().get('/'), params, Contract,
                              ContractAdmin(Contract, AdminSite()))
    return set(f.queryset(None, Contract.objects.all()).values_list('name', flat=True))


def _member(igdb_id, shovelware='clean', *, anchored=True):
    concept = ConceptFactory(anchor_migration_completed_at=timezone.now() if anchored else None)
    IGDBMatchFactory(concept=concept, igdb_id=igdb_id)
    GameFactory(concept=concept, shovelware_status=shovelware)
    return concept


def _contract(name, igdb_id=None, *, is_live=True):
    return Contract.objects.create(name=name, slug=name.lower().replace(' ', '-'),
                                   igdb_id=igdb_id, is_live=is_live)


def _bundle(contract, *concepts):
    bundle = ContractBundle.objects.create(contract=contract, label='Satisfier')
    bundle.concepts.add(*concepts)


@pytest.fixture
def catalogue():
    _contract('Live Flagged', 98001)
    _member(98001, 'auto_flagged')
    _contract('Staged Flagged', 98002, is_live=False)
    _member(98002, 'manually_flagged')
    _contract('Clean', 98003)
    _member(98003)
    _contract('Cleared', 98004)
    _member(98004, 'manually_cleared')
    # One flagged version among clean siblings still holds shovelware.
    _contract('Mixed Siblings', 98005)
    _member(98005)
    _member(98005, 'auto_flagged')
    # An un-anchored concept is not a member, so its flag does not count.
    _contract('Unanchored Flag', 98006)
    _member(98006)
    _member(98006, 'auto_flagged', anchored=False)
    # Bundle-only contracts (igdb_id NULL): the "No" side must still see the clean one.
    _bundle(_contract('Bundle Flagged'), ConceptFactory(), _member(98101, 'auto_flagged'))
    _bundle(_contract('Bundle Clean'), _member(98102))
    # A normal contract whose satisfier bundle holds a flagged game: the engine credits it.
    home = _contract('Home Clean Bundle Flagged', 98007)
    _member(98007)
    _bundle(home, _member(98103, 'auto_flagged'))


def test_yes_lists_every_contract_a_flagged_game_can_satisfy(catalogue):
    assert _filtered('yes') == {
        'Live Flagged', 'Staged Flagged', 'Mixed Siblings', 'Bundle Flagged',
        'Home Clean Bundle Flagged',
    }


def test_no_is_the_exact_complement(catalogue):
    """Includes the bundle-only clean contract: a NULL igdb_id must not vanish from the negation."""
    assert _filtered('no') == {'Clean', 'Cleared', 'Unanchored Flag', 'Bundle Clean'}
    assert _filtered('yes') | _filtered('no') == _filtered(None)


def test_unfiltered_returns_everything(catalogue):
    assert len(_filtered(None)) == Contract.objects.count() == 9


def test_filter_is_registered_on_the_contract_admin():
    assert HoldsShovelwareFilter in ContractAdmin.list_filter
