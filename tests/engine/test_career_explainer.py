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


# --- the modal + its gates on /career/ ---

def test_career_auto_opens_the_howto_on_first_visit(linked_client):
    """The modal renders for everyone (the edhint reopens it) but data-auto -- the auto-open
    arm -- renders only while the server flag is unset."""
    body = linked_client.get('/career/', **CF).content.decode()

    tag = body.split('id="career-howto"')[1].split('>')[0]
    assert 'data-auto' in tag
    assert '25 jobs, five disciplines' in body
    assert 'Contracts' in body and 'Pursuer' in body
    # The collage strip: three real-object cells (job icons, the cover fan, the ring).
    assert 'cxp__flow' in body
    assert 'pp-forge__fan' in body, 'the contract cell must reuse the badge fan classes'
    assert 'cxp__ring' in body


def test_career_never_auto_opens_once_flagged(linked_client):
    """With the flag set the modal stays reachable (edhint) but must not auto-pop."""
    user = linked_client.profile.user
    user.ui_flags = {'career_explainer': True}
    user.save(update_fields=['ui_flags'])

    body = linked_client.get('/career/', **CF).content.decode()

    assert 'id="career-howto"' in body, 'the teaching must stay reachable after dismissal'
    # Scoped to the modal's own tag: 'data-auto' is a substring of unrelated attributes.
    tag = body.split('id="career-howto"')[1].split('>')[0]
    assert 'data-auto' not in tag


def test_the_summary_card_carries_the_reopen_hint(linked_client):
    """Career has no long-form how-it-works page; the modal IS the teaching, so the summary
    card's edhint keeps it reachable forever (the badge howto's one-shot rationale inverts)."""
    body = linked_client.get('/career/', **CF).content.decode()

    assert 'data-career-open' in body
    assert 'How does my Career work?' in body


def test_the_explainer_never_grows_its_own_dev_panel():
    """The bug that shipped: .ccx-dev is fixed to left/bottom 12px, so a second panel stacks
    on top of the ceremony player and eats its clicks. Dev affordances join career.html's
    existing panel; this partial must stay panel-free."""
    from pathlib import Path

    from django.conf import settings

    text = (Path(settings.BASE_DIR) / 'templates' / 'trophies' / 'partials' / 'career' /
            '_career_explainer.html').read_text(encoding='utf-8')
    assert 'ccx-dev' not in text
