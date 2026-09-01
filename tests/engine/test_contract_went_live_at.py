"""`Contract.went_live_at`: the timestamp any "what's new" surface has to key off.

`created_at` cannot answer it. The candidate pipeline STAGES a contract (`is_live=False`) and
staff publish it later, possibly weeks later, so created_at records when it was drafted rather
than when the community could first see it. Stamped once and never reset, so un-publishing and
re-publishing a contract does not re-announce it.
"""
import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory
from django.utils import timezone

from trophies.admin import ContractAdmin
from trophies.models import Contract

pytestmark = pytest.mark.django_db


def _admin_request():
    request = RequestFactory().post('/')
    request.session = {}
    request._messages = FallbackStorage(request)
    return request


def test_staging_a_contract_leaves_it_unstamped():
    """A staged contract has been drafted, not published: nothing should treat it as new."""
    c = Contract.objects.create(name='Staged', slug='staged', igdb_id=880001, is_live=False)
    assert c.went_live_at is None


def test_publishing_stamps_it():
    c = Contract.objects.create(name='Later', slug='later', igdb_id=880002, is_live=False)
    c.is_live = True
    c.save()

    c.refresh_from_db()
    assert c.went_live_at is not None


def test_created_live_is_stamped_immediately():
    c = Contract.objects.create(name='Born Live', slug='born-live', igdb_id=880003, is_live=True)
    assert c.went_live_at is not None


def test_the_stamp_survives_unpublish_and_republish():
    """THE point of the field: it records the FIRST publish. A contract pulled back for a fix and
    re-published must not resurface in a 'new contracts' announcement."""
    c = Contract.objects.create(name='Bounced', slug='bounced', igdb_id=880004, is_live=True)
    first = c.went_live_at

    c.is_live = False
    c.save()
    c.is_live = True
    c.save()

    c.refresh_from_db()
    assert c.went_live_at == first


def test_update_fields_save_still_stamps():
    """A targeted save(update_fields=['is_live']) -- what a publish view would do -- must not drop
    the new column from the UPDATE."""
    c = Contract.objects.create(name='Targeted', slug='targeted', igdb_id=880005, is_live=False)
    c.is_live = True
    c.save(update_fields=['is_live'])

    c.refresh_from_db()
    assert c.went_live_at is not None


def test_bulk_admin_action_stamps_and_preserves():
    """`make_live` uses queryset.update(), which bypasses save() -- it has to stamp the column
    itself, and must NOT reset a contract that was already live once."""
    fresh = Contract.objects.create(name='Fresh', slug='fresh', igdb_id=880006, is_live=False)
    old = Contract.objects.create(name='Old', slug='old', igdb_id=880007, is_live=True)
    original = old.went_live_at
    old.is_live = False
    old.save()

    admin = ContractAdmin(Contract, AdminSite())
    admin.make_live(_admin_request(), Contract.objects.filter(pk__in=[fresh.pk, old.pk]))

    fresh.refresh_from_db()
    old.refresh_from_db()
    assert fresh.is_live and fresh.went_live_at is not None
    assert old.is_live and old.went_live_at == original, 'a republish must keep the first date'


def test_it_is_distinct_from_created_at():
    """The whole reason the field exists: a staged contract published later has a went_live_at
    well after its created_at, so 'newest by created_at' would order and date it wrongly."""
    c = Contract.objects.create(name='Drafted', slug='drafted', igdb_id=880008, is_live=False)
    Contract.objects.filter(pk=c.pk).update(
        created_at=timezone.now() - timezone.timedelta(days=30))
    c.refresh_from_db()
    c.is_live = True
    c.save()

    c.refresh_from_db()
    assert (c.went_live_at - c.created_at).days >= 29
