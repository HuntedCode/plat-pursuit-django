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


# --- the render gate on /career/ ---

def test_career_shows_the_explainer_on_first_visit(linked_client):
    body = linked_client.get('/career/', **CF).content.decode()

    assert 'data-career-explainer' in body
    assert 'data-career-explainer hidden' not in body


def test_career_hides_the_explainer_once_flagged(linked_client):
    """Server-side gate: with the flag set the card must not render visibly. Tests run with
    DEBUG=False, so this is a full non-render (the DEBUG `hidden` replay target never ships)."""
    user = linked_client.profile.user
    user.ui_flags = {'career_explainer': True}
    user.save(update_fields=['ui_flags'])

    body = linked_client.get('/career/', **CF).content.decode()

    assert 'data-career-explainer' not in body


def test_the_explainer_is_not_a_second_page_header():
    """The hero and Career summary card both wear border-l-primary; the explainer must not."""
    from pathlib import Path

    from django.conf import settings

    text = (Path(settings.BASE_DIR) / 'templates' / 'trophies' / 'partials' / 'career' /
            '_career_explainer.html').read_text(encoding='utf-8')
    assert 'border-l-primary' not in text
    assert 'border-l-secondary' in text
