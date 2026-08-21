"""Gift purchase plumbing: checkout creation, webhook routing, and the collision that almost was.

THE R1 STORY, because it is why half these tests exist: donations and gifts both complete off
PayPal's PAYMENT.CAPTURE.COMPLETED, and donations put a BARE INTEGER in `custom_id`. A gift that
did the same would have its capture for id 5 complete *Donation 5* -- somebody's fundraiser
donation marked paid by somebody else's gift money. Gifts therefore prefix (`gift:{id}`), the gift
handler is prefix-matched so it can never claim a donation, and this file pins both directions.
"""
import json
from unittest.mock import patch, MagicMock

import pytest
from django.urls import reverse
from django.utils import timezone

from users.models import PremiumGrant
from users.services.gift_service import GiftService
from tests.factories import ProfileFactory, UserFactory

pytestmark = pytest.mark.django_db


def _pending(provider='paypal', tier='patron', months=1, txn=None):
    return PremiumGrant.objects.create(
        tier_slug=tier, months=months, amount=15, provider=provider,
        provider_transaction_id=txn or f'pending_test_{provider}_{tier}_{months}',
        purchaser=UserFactory(),
    )


# --------------------------------------------------------------------- the R1 collision pair ----

def test_a_gift_capture_completes_the_grant_and_not_the_donation_with_the_same_id():
    """`custom_id='gift:N'` completes grant N. If a Donation with primary key N exists, it must be
    untouched -- the two id spaces overlap on every integer."""
    from fundraiser.models import Donation, Fundraiser

    fundraiser = Fundraiser.objects.create(
        name='Drive', slug='drive', campaign_type='badge_artwork',
        start_date=timezone.now(),
    )
    grant = _pending()
    donation = Donation.objects.create(
        id=grant.id,   # force the collision this test exists for
        fundraiser=fundraiser, user=UserFactory(), amount=10,
        provider='paypal', provider_transaction_id='pending_donation_x',
        status='pending',
    )

    claimed = GiftService.handle_paypal_capture_completed({'custom_id': f'gift:{grant.id}'})

    assert claimed is True
    grant.refresh_from_db()
    donation.refresh_from_db()
    assert grant.status == 'issued', 'the gift did not complete'
    assert donation.status == 'pending', 'the gift capture completed a DONATION'


def test_a_bare_integer_capture_is_left_for_the_donation_handler():
    """The other direction: a donation's bare-int custom_id must never be claimed by the gift
    handler, or donations would silently stop completing."""
    grant = _pending()

    claimed = GiftService.handle_paypal_capture_completed({'custom_id': str(grant.id)})

    assert claimed is False, 'the gift handler claimed a donation capture'
    grant.refresh_from_db()
    assert grant.status == 'pending'


def test_the_nested_capture_shape_is_walked_too():
    """PayPal delivers custom_id at the top level on webhook captures but nested under
    purchase_units[].payments.captures[] on the capture-API response the success page feeds in."""
    grant = _pending()
    resource = {'purchase_units': [{'payments': {'captures': [
        {'custom_id': f'gift:{grant.id}'}
    ]}}]}

    assert GiftService.handle_paypal_capture_completed(resource) is True
    grant.refresh_from_db()
    assert grant.status == 'issued'


# ------------------------------------------------------------------------- webhook routing ----

def test_stripe_gift_metadata_routes_before_the_subscription_handler(client):
    """A mode='payment' checkout.session.completed reaching update_user_subscription would find no
    active subscription for the customer -- and its deactivate arm could kill a real one. The gift
    branch must swallow the event."""
    grant = _pending(provider='stripe')
    event = MagicMock()
    event.type = 'checkout.session.completed'
    event.data.object = {'metadata': {'type': 'premium_gift', 'grant_id': str(grant.id)},
                         'customer': 'cus_x'}

    with patch('users.views.stripe.Webhook.construct_event', return_value=event), \
            patch('users.views.DJStripeEvent.process'), \
            patch('users.views.SubscriptionService.handle_webhook_event') as subs:
        response = client.post(reverse('stripe_webhook'), data=b'{}',
                               content_type='application/json',
                               HTTP_STRIPE_SIGNATURE='sig')

    assert response.status_code == 200
    assert not subs.called, 'a gift payment reached the subscription handler'
    grant.refresh_from_db()
    assert grant.status == 'issued'


def test_completion_is_idempotent_across_redirect_and_webhook():
    """DEBUG completes inline on the success redirect AND the webhook fires: one code, one email."""
    grant = _pending(provider='stripe')
    session = {'metadata': {'type': 'premium_gift', 'grant_id': str(grant.id)}}

    with patch.object(GiftService, '_send_code_email') as email:
        GiftService.handle_stripe_payment_completed(session)
        GiftService.handle_stripe_payment_completed(session)

    grant.refresh_from_db()
    assert grant.status == 'issued'
    assert email.call_count == 1, 'the double-fire minted two emails'


def test_complete_grant_itself_is_idempotent():
    """The handlers filter on status='pending' too, but complete_grant is also reachable directly
    (the PayPal capture path) -- its own guard must hold without the outer filter."""
    grant = _pending(provider='stripe')

    with patch.object(GiftService, '_send_code_email') as email:
        GiftService.complete_grant(grant)
        first_code = grant.code
        GiftService.complete_grant(grant)

    grant.refresh_from_db()
    assert grant.code == first_code, 'the second completion re-minted the code'
    assert email.call_count == 1


def test_the_code_email_goes_to_the_purchaser():
    grant = _pending(provider='stripe')

    with patch('core.services.email_service.EmailService.send_html_email') as send, \
            patch('users.services.email_preference_service.EmailPreferenceService.should_send_email',
                  return_value=True):
        GiftService.complete_grant(grant)

    assert send.called
    kwargs = send.call_args.kwargs
    assert kwargs['to_emails'] == [grant.purchaser.email]
    assert grant.code in str(kwargs['context']['code'])
    assert 'redeem' in kwargs['context']['redeem_url']


# ------------------------------------------------------------------------ checkout creation ----

def test_a_gift_checkout_prices_from_the_ladder_constant():
    """Gifts use ad-hoc price_data (a one-time session cannot reference recurring prices), priced
    straight off SUPPORT_TIERS -- so they work before the SKU bootstrap has ever run, and the
    amount is the same one the subscription charges."""
    user = UserFactory()
    fake_session = MagicMock(id='cs_test_x', url='https://checkout.stripe.com/x')

    with patch('stripe.checkout.Session.create', return_value=fake_session) as create:
        url = GiftService.create_stripe_checkout(user, 'cornerstone', 12,
                                                 'https://x/success', 'https://x/cancel')

    assert url == 'https://checkout.stripe.com/x'
    kwargs = create.call_args.kwargs
    assert kwargs['mode'] == 'payment'
    assert kwargs['line_items'][0]['price_data']['unit_amount'] == 300 * 100
    assert kwargs['metadata']['type'] == 'premium_gift'
    grant = PremiumGrant.objects.get(provider_transaction_id='cs_test_x')
    assert grant.amount == 300


def test_a_paypal_gift_order_carries_the_prefixed_custom_id():
    user = UserFactory()
    fake = MagicMock()
    fake.json.return_value = {'id': 'ORDER123',
                              'links': [{'rel': 'approve', 'href': 'https://paypal.com/a'}]}
    fake.raise_for_status = MagicMock()

    with patch('requests.post', return_value=fake) as post, \
            patch('users.services.paypal_service.PayPalService._api_headers', return_value={}):
        url = GiftService.create_paypal_order(user, 'backer', 1, 'https://x/r', 'https://x/c')

    assert url == 'https://paypal.com/a'
    payload = post.call_args.kwargs['json']
    custom_id = payload['purchase_units'][0]['custom_id']
    assert custom_id.startswith('gift:'), 'THE R1 PREFIX IS GONE -- gift captures will complete donations'
    grant = PremiumGrant.objects.get(provider_transaction_id='ORDER123')
    assert custom_id == f'gift:{grant.id}'


def test_the_gift_tab_posts_into_the_gift_flow(client):
    """The storefront's cycle=gift POST reaches GiftService, with the duration mapped to months."""
    user = UserFactory()
    client.force_login(user)

    with patch('users.views.SubscriptionService.get_prices_from_stripe', return_value={}), \
            patch('users.views.SubscriptionService.has_active_subscription',
                  return_value=(False, None)), \
            patch('users.services.gift_service.GiftService.create_stripe_checkout',
                  return_value='https://checkout.stripe.com/gift') as checkout:
        response = client.post(reverse('support_hub'),
                               {'tier': 'sponsor', 'provider': 'stripe',
                                'sup-cycle': 'gift', 'gift-duration': 'yearly'})

    assert checkout.called, 'the gift tab never reached the gift service'
    args = checkout.call_args.args
    assert args[1] == 'sponsor'
    assert args[2] == 12
    assert response.status_code == 303


def test_pending_gift_rows_carry_unique_placeholder_ids():
    a = _pending(txn='pending_a')
    b = _pending(txn='pending_b')
    assert a.provider_transaction_id != b.provider_transaction_id
