"""`/beta-access/`: kept for the next members-first beta, and honest while none is running (owner, 2026-10-18)."""
import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


def test_the_page_says_no_beta_is_running(client):
    resp = client.get(reverse('beta_access_required'))
    body = resp.content.decode()
    assert resp.status_code == 200
    assert 'No betas running right now' in body
    assert 'currently in beta testing' not in body, 'the page still claims a feature is in beta'
    assert f'href="{reverse("support_hub")}"' in body and f'href="{reverse("home")}"' in body


def test_the_page_stays_out_of_search(client):
    body = client.get(reverse('beta_access_required')).content.decode()
    assert '<meta name="robots" content="noindex, follow">' in body
