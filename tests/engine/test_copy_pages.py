"""The four copy pages (privacy, terms, about, contact) -- truth pins after the 2026-08 review.

These pages had NO content tests, which is how the privacy policy spent months describing an
analytics cookie that had been deleted and the terms licensed "guides and comments" (one system
deleted, one read-only) while missing the content people actually submit. The pins here are the
claims most likely to rot again: what deletes, what emails send, which cookies exist, which
processors handle money, and which features are real.
"""
from pathlib import Path

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[2]
PAGES = ['privacy', 'terms', 'about', 'contact']


@pytest.mark.parametrize('name', PAGES)
def test_the_page_renders(client, name):
    assert client.get(reverse(name)).status_code == 200


@pytest.mark.parametrize('name', PAGES)
def test_no_em_dashes_in_the_copy(name):
    src = (ROOT / 'templates' / 'pages' / f'{name}.html').read_text(encoding='utf-8')

    assert '—' not in src, 'em dashes are banned in user-facing copy'


def test_privacy_tells_the_truth_about_cookies_and_analytics(client):
    body = client.get(reverse('privacy')).content.decode()

    assert 'Analytics Cookies' not in body, 'the analytics system (and its cookie) was deleted 2026-08'
    assert 'sessionid' in body and 'csrftoken' in body, 'name the cookies that actually exist'
    assert 'Cloudflare Web Analytics' in body, 'the aggregate, cookieless measurement we DO use'


def test_privacy_describes_self_serve_deletion_and_its_semantics(client):
    body = client.get(reverse('privacy')).content.decode()

    assert 'delete your account yourself' in body
    assert 'remains public' in body, 'the profile-survives semantics are the policy-worthy fact'
    assert 'quick takes are erased' in body


def test_privacy_names_both_payment_processors_and_current_ugc(client):
    body = client.get(reverse('privacy')).content.decode()

    assert 'PayPal' in body
    assert 'roadmap contributions' in body
    assert 'guides' not in body.lower(), 'the guides system is deleted; the policy must not name it'


def test_privacy_promises_no_marketing_email(client):
    assert 'no marketing email' in client.get(reverse('privacy')).content.decode()


def test_terms_stopped_pointing_at_the_nonexistent_refund_policy(client):
    body = client.get(reverse('terms')).content.decode()

    assert 'refund policy' not in body, 'that page never existed'
    assert 'paid time is always honoured' in body, 'the actual, repeatedly-made promise'
    assert 'PayPal' in body


def test_terms_covers_deletion_and_the_current_content_licence(client):
    body = client.get(reverse('terms')).content.decode()

    assert 'delete your account yourself' in body
    assert 'quick takes, roadmap contributions' in body
    assert 'not affiliated with' in body, 'the Sony disclaimer stays'


def test_about_markets_real_features_not_deleted_ones(client):
    body = client.get(reverse('about')).content.decode()

    assert 'Community Guides' not in body, 'guides are deleted; this marketed a 302'
    assert 'Roadmaps' in body
    assert 'Badges &amp; Career' in body
    assert 'Monthly Recap' in body


def test_contact_links_the_public_roadmap(client):
    assert reverse('support_roadmap') in client.get(reverse('contact')).content.decode()


@pytest.mark.parametrize('name', ['privacy', 'terms'])
def test_the_legal_pages_are_dated_august_2026(client, name):
    assert 'August 2026' in client.get(reverse(name)).content.decode()