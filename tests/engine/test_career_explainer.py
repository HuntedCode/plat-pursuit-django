"""The Career first-visit explainer + the ui_flags one-shot education mechanism.

New-user onboarding (2026-08-24): a dismissible education card on /career/ remembered
server-side per user via CustomUser.ui_flags, written through the quick-settings API's
ui_flag branch. Plan: onboarding initiative; doc: docs/features/onboarding.md.
"""
import pytest
from django.urls import reverse

from tests.factories import ProfileFactory

pytestmark = pytest.mark.django_db

CF = {'HTTP_CF_RAY': '8f0000000000abcd-LHR'}


@pytest.fixture
def linked_client(client):
    profile = ProfileFactory(is_linked=True, sync_status='synced', total_trophies=10)
    client.force_login(profile.user)
    client.profile = profile
    return client


# --- the ui_flag endpoint branch ---

def test_ui_flag_endpoint_sets_the_flag(linked_client):
    resp = linked_client.post(
        reverse('api:user-quick-settings'),
        data={'setting': 'ui_flag', 'value': 'career_explainer'}, content_type='application/json')

    assert resp.status_code == 200
    linked_client.profile.user.refresh_from_db()
    assert linked_client.profile.user.ui_flags.get('career_explainer') is True


def test_ui_flag_endpoint_rejects_unknown_flags(linked_client):
    """The whitelist is the contract: a typo'd flag must fail loudly, not write junk keys."""
    resp = linked_client.post(
        reverse('api:user-quick-settings'),
        data={'setting': 'ui_flag', 'value': 'made_up_flag'}, content_type='application/json')

    assert resp.status_code == 400
    linked_client.profile.user.refresh_from_db()
    assert linked_client.profile.user.ui_flags == {}


def test_ui_flag_endpoint_rejects_non_string_values(linked_client):
    resp = linked_client.post(
        reverse('api:user-quick-settings'),
        data={'setting': 'ui_flag', 'value': True}, content_type='application/json')

    assert resp.status_code == 400


def test_ui_flag_endpoint_requires_auth(client):
    resp = client.post(
        reverse('api:user-quick-settings'),
        data={'setting': 'ui_flag', 'value': 'career_explainer'}, content_type='application/json')

    assert resp.status_code in (401, 403)


def test_ui_flag_write_preserves_other_flags(linked_client):
    """Pins the read-modify-write: a second surface's flag must never clobber the first's."""
    user = linked_client.profile.user
    user.ui_flags = {'some_future_flag': True}
    user.save(update_fields=['ui_flags'])

    linked_client.post(
        reverse('api:user-quick-settings'),
        data={'setting': 'ui_flag', 'value': 'career_explainer'}, content_type='application/json')

    user.refresh_from_db()
    assert user.ui_flags == {'some_future_flag': True, 'career_explainer': True}
