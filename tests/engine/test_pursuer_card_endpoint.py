"""Tests for the Pursuer Card refresh endpoint (api/pursuer_card_views.py PursuerCardRefreshView).

The forge's live path GETs this after a sync to swap in fresh card HTML. These pin the request
contract: the auth gate, a profile gets rendered card HTML, and no linked profile degrades to 204.
"""
import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from tests.factories import ProfileFactory, UserFactory

pytestmark = pytest.mark.django_db

URL = reverse('api:pursuer-card')


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def test_profile_gets_rendered_card_html():
    profile = ProfileFactory()

    resp = _client(profile.user).get(URL)

    assert resp.status_code == 200
    assert b'pursuer-card' in resp.content


def test_no_profile_degrades_to_204():
    resp = _client(UserFactory()).get(URL)  # a user with no linked Profile

    assert resp.status_code == 204
    assert resp.content == b''


def test_anonymous_is_rejected():
    resp = APIClient().get(URL)

    assert resp.status_code in (401, 403)  # IsAuthenticated guards the endpoint
