"""The fundraiser PAGE -- the first tests that actually render it.

The gap these close: the BadgeSeries repoint left `DonationService` unimported in the view and
NOBODY noticed, because every existing fundraiser test exercised services or context processors.
The public page 500'd on every badge-artwork campaign render. The live-campaign render test
below IS that regression test.
"""
from datetime import timedelta
from itertools import count
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from fundraiser.models import Donation, DonationBadgeClaim, Fundraiser
from tests.factories import (
    BadgeSeriesFactory, GroupBadgeFactory, PlatformGroupFactory, ProfileFactory, UserFactory,
)

pytestmark = pytest.mark.django_db

_seq = count(1)


@pytest.fixture(autouse=True)
def _clear_fundraiser_caches():
    for k in ('fundraiser:active_banner', 'fundraiser:live'):
        cache.delete(k)
    yield
    for k in ('fundraiser:active_banner', 'fundraiser:live'):
        cache.delete(k)


def _campaign(**over):
    now = timezone.now()
    n = next(_seq)
    defaults = dict(
        name='Badge Artwork Drive', slug=f'art-drive-{n}', description='Fund the art.',
        campaign_type='badge_artwork', start_date=now - timedelta(days=1), end_date=None,
    )
    defaults.update(over)
    return Fundraiser.objects.create(**defaults)


def _series_with_edition(slug):
    series = BadgeSeriesFactory(series_slug=slug, name=slug.title())
    GroupBadgeFactory(series=series, platform_group=PlatformGroupFactory(key=f'{slug}-grp'),
                      is_live=True)
    return series


# ------------------------------------------------------------------- the page renders ----

def test_a_live_campaign_page_renders(client):
    """THE NameError regression: rendering a live badge-artwork campaign executes
    series_needing_artwork twice in the view; before the import fix this was a 500."""
    campaign = _campaign()
    response = client.get(reverse('fundraiser', kwargs={'slug': campaign.slug}))
    assert response.status_code == 200
    assert campaign.name in response.content.decode()


def test_an_ended_campaign_celebrates(client):
    campaign = _campaign(start_date=timezone.now() - timedelta(days=30),
                         end_date=timezone.now() - timedelta(days=1))
    response = client.get(reverse('fundraiser', kwargs={'slug': campaign.slug}))
    body = response.content.decode()
    assert response.status_code == 200
    assert 'Mission Accomplished' in body
    assert 'donation-form' not in body, 'an ended campaign must not take money'


def test_upcoming_is_staff_preview_only(client):
    campaign = _campaign(start_date=timezone.now() + timedelta(days=3))

    # Non-staff bounce home (the info message rides the redirect; whether home renders it is
    # home's concern, not this page's).
    response = client.get(reverse('fundraiser', kwargs={'slug': campaign.slug}))
    assert response.status_code == 302
    assert response['Location'] == '/'

    staff = UserFactory(is_staff=True)
    client.force_login(staff)
    response = client.get(reverse('fundraiser', kwargs={'slug': campaign.slug}))
    assert response.status_code == 200
    assert 'Staff Preview' in response.content.decode() or 'preview' in response.content.decode().lower()


def test_a_completed_claim_renders_its_medallion(client):
    """The empty-gallery bug: the template read `claim.badge_layers` (never set) and included
    the legacy partial; the view builds `claim.medallion` for components/badge_medallion.html."""
    campaign = _campaign()
    series = _series_with_edition('painted')
    donation = Donation.objects.create(
        fundraiser=campaign, amount=10, provider='stripe',
        provider_transaction_id=f'tx-{next(_seq)}', status='completed',
        badge_picks_earned=1, badge_picks_used=1,
    )
    DonationBadgeClaim.objects.create(
        donation=donation, profile=ProfileFactory(), series=series,
        series_slug=series.series_slug, series_name=series.name, status='completed',
    )
    body = client.get(reverse('fundraiser', kwargs={'slug': campaign.slug})).content.decode()
    assert 'pp-med' in body, 'the completed claim gallery lost its medallions again'


# ------------------------------------------------------------------------ the landing ----

def test_the_landing_resolves_the_latest_campaign(client):
    live = _campaign()
    response = client.get(reverse('support_fundraiser'))
    assert response.status_code == 302
    assert response['Location'] == reverse('fundraiser', kwargs={'slug': live.slug})


def test_the_landing_resolves_an_ended_campaign_when_nothing_is_live(client):
    ended = _campaign(start_date=timezone.now() - timedelta(days=30),
                      end_date=timezone.now() - timedelta(days=2))
    response = client.get(reverse('support_fundraiser'))
    assert response.status_code == 302
    assert response['Location'] == reverse('fundraiser', kwargs={'slug': ended.slug})


def test_the_landing_without_any_campaign_is_a_quiet_card(client):
    response = client.get(reverse('support_fundraiser'))
    assert response.status_code == 200
    assert 'Nothing running right now' in response.content.decode()


# ----------------------------------------------------------------- the frozen contracts ----

def test_the_payment_urls_never_move():
    """These exact paths are baked into every processor session's success/cancel URLs and every
    sent email. Failing this test means an in-flight checkout will land on a 404."""
    assert reverse('fundraiser', kwargs={'slug': 's'}) == '/fundraiser/s/'
    assert reverse('fundraiser_success', kwargs={'slug': 's'}) == '/fundraiser/s/success/'


def test_the_checkout_builds_the_frozen_paths(client):
    campaign = _campaign(minimum_donation=5)
    user = UserFactory()
    ProfileFactory(user=user)
    client.force_login(user)

    with patch('fundraiser.services.donation_service.DonationService.create_stripe_checkout',
               return_value='https://checkout.stripe.com/x') as checkout:
        response = client.post(
            f'/api/v1/fundraiser/{campaign.slug}/donate/',
            {'amount': '10', 'provider': 'stripe'},
            content_type='application/json',
        )
    assert response.status_code == 200, response.content
    kwargs = checkout.call_args.kwargs
    assert kwargs['success_url'].endswith(f'/fundraiser/{campaign.slug}/success/')
    assert kwargs['cancel_url'].endswith(f'/fundraiser/{campaign.slug}/')


def test_email_links_still_carry_the_slug_path():
    """After reverse()-ification the outbound copy is byte-identical -- the win is a loud break
    if the route ever changes."""
    from django.urls import reverse as _r
    assert _r('fundraiser', kwargs={'slug': 'drive'}) == '/fundraiser/drive/'
    import inspect
    from fundraiser.services import donation_service
    source = inspect.getsource(donation_service)
    assert '/fundraiser/{donation' not in source, 'a literal path survived the reverse()-ification'
