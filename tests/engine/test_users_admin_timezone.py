"""`timezone_confirmed_at` in the Django admin.

The field answers one question -- has this hunter ever been asked, and did they answer -- and nothing in
the admin could answer it. `user_timezone` is non-null with a UTC default, so its VALUE cannot separate a
London user who never touched it from one who deliberately chose UTC. That distinction is the entire
reason the stamp exists, and it was invisible to anyone doing support.
"""
import pytest
from django.contrib import admin as django_admin
from django.urls import reverse
from django.utils import timezone

from tests.factories import UserFactory
from users.admin import CustomUserAdmin
from users.models import CustomUser

pytestmark = pytest.mark.django_db


def test_the_stamp_is_editable_beside_the_zone_it_qualifies():
    """Editable on purpose: clearing it re-arms the recap's first-run prompt for that hunter, which is
    the one support action anyone would want from this field."""
    fields = [f for _, opts in CustomUserAdmin.fieldsets for f in opts['fields']]
    assert 'timezone_confirmed_at' in fields, 'the stamp cannot be seen or cleared from the admin'
    assert 'timezone_confirmed_at' not in getattr(CustomUserAdmin, 'readonly_fields', ()), (
        'the stamp is readonly, so the prompt cannot be re-armed for a user who asks'
    )

    personal = next(opts for name, opts in CustomUserAdmin.fieldsets if name == 'Personal Info')
    assert personal['fields'].index('user_timezone') + 1 == personal['fields'].index(
        'timezone_confirmed_at'), 'the stamp is filed away from the zone it qualifies'


def test_the_list_answers_confirmed_or_not_rather_than_showing_a_raw_stamp():
    assert 'timezone_confirmed' in CustomUserAdmin.list_display
    assert 'timezone_confirmed_at' not in CustomUserAdmin.list_display, (
        'a datetime column here is noise; the question is whether they answered at all'
    )
    assert CustomUserAdmin.timezone_confirmed.boolean is True


def test_never_answered_is_a_filterable_population():
    """The population the recap's prompt targets. Not derivable from the timezone value."""
    filters = [f for f in CustomUserAdmin.list_filter if isinstance(f, tuple)]
    assert ('timezone_confirmed_at', django_admin.EmptyFieldListFilter) in filters


def test_the_column_reads_the_stamp():
    never = UserFactory()
    answered = UserFactory()
    answered.timezone_confirmed_at = timezone.now()
    answered.save(update_fields=['timezone_confirmed_at'])

    col = CustomUserAdmin(CustomUser, django_admin.site).timezone_confirmed
    assert col(never) is False
    assert col(answered) is True


def test_the_changelist_renders_with_the_new_column_and_filter(client):
    """Cheap, and it is the half a unit test cannot see: a bad `list_filter` entry or a display method
    that does not resolve only fails when the page is actually built."""
    staff = UserFactory(is_staff=True, is_superuser=True)
    client.force_login(staff)

    resp = client.get(reverse('admin:users_customuser_changelist'))

    assert resp.status_code == 200
    assert b'TZ confirmed' in resp.content, 'the column did not render'
