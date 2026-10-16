"""The Stripe webhook, driven by a real signed event instead of mocks.

The other webhook tests patch `construct_event` and dj-stripe's `Event`, so they hand the view plain
dicts and MagicMocks. That hid the stripe-python 15 break: a StripeObject stopped being a dict, and
dj-stripe's `Event.process` reads `request` with an `isinstance(dict)` check. Given the SDK's Event,
it wrote the whole request object as text into the 50-character `request_id`, and every webhook
that carried one 500'd.

What runs for real here: signature verification, dj-stripe creating its Event row, our view's
routing, and (in the handler cases) our own handler code on the SDK object. What is stubbed, because
each is a call to Stripe's API: dj-stripe's account lookup, and dj-stripe's per-type handlers (they
re-fetch the object from the API; the test-mode checkout replay covers them). Outgoing email and
notifications are recorded rather than sent.
"""
import json
from unittest.mock import patch

import pytest
import stripe
from django.urls import reverse
from djstripe.models import Event as DJStripeEvent

from tests.factories import UserFactory
from users.services.subscription_service import SubscriptionService

pytestmark = pytest.mark.django_db

SECRET = 'whsec_test_real_event'


def _event(event_id, request=None, event_type='billing_portal.session.created', obj=None):
    """A Stripe event payload in the shape the live API sends (trimmed to what the path reads)."""
    return {
        'id': event_id,
        'object': 'event',
        'api_version': '2025-12-15.clover',
        'created': 1792112577,
        'livemode': False,
        'pending_webhooks': 1,
        'request': request,
        'type': event_type,
        'data': {'object': obj or {'id': 'bps_test_1', 'object': 'billing_portal.session', 'customer': 'cus_test_1'}},
    }


def _deliver(client, payload):
    body = json.dumps(payload)
    header = stripe.WebhookSignature.generate_signature_header(body, SECRET)
    return client.post(
        reverse('stripe_webhook'), data=body, content_type='application/json', HTTP_STRIPE_SIGNATURE=header,
    )


@pytest.fixture
def stripe_offline(settings):
    """Real verification and Event processing, with dj-stripe's two API-calling steps stubbed."""
    settings.DJSTRIPE_WEBHOOK_SECRET = SECRET
    with patch('djstripe.models.Account.get_or_retrieve_for_api_key', return_value=None), \
            patch.object(DJStripeEvent, 'invoke_webhook_handlers'):
        yield


@pytest.fixture
def handled(stripe_offline, monkeypatch):
    """Records what the view hands our subscription handler, in place of running it."""
    calls = []
    monkeypatch.setattr(
        SubscriptionService, 'handle_webhook_event',
        staticmethod(lambda event_type, obj: calls.append((event_type, obj))),
    )
    return calls


# --- dj-stripe's Event row: the 500 itself ---------------------------------------------------------

@pytest.mark.parametrize('request_field, request_id, idempotency_key', [
    ({'id': None, 'idempotency_key': 'a3f1c2d4-5e6f-4a1b-9c8d-7e6f5a4b3c2d'}, '', 'a3f1c2d4-5e6f-4a1b-9c8d-7e6f5a4b3c2d'),
    ({'id': 'req_oZECfWSeI9t0gd', 'idempotency_key': None}, 'req_oZECfWSeI9t0gd', ''),
    (None, '', ''),
], ids=['automatic-event', 'api-request', 'no-request'])
def test_a_signed_event_is_stored_and_handled(client, handled, request_field, request_id, idempotency_key):
    response = _deliver(client, _event('evt_test_real_1', request_field))

    assert response.status_code == 200
    stored = DJStripeEvent.objects.get(id='evt_test_real_1')
    assert (stored.request_id, stored.idempotency_key) == (request_id, idempotency_key)
    assert [event_type for event_type, _ in handled] == ['billing_portal.session.created']


def test_a_redelivered_event_is_not_handled_twice(client, handled):
    payload = _event('evt_test_real_3')

    assert _deliver(client, payload).status_code == 200
    assert _deliver(client, payload).status_code == 200
    assert len(handled) == 1


# --- our handlers on the SDK's objects ------------------------------------------------------------

def test_the_handler_can_still_read_live_stripe_objects_like_dicts(client, handled):
    """Our handlers and two management commands call `.get()` on the SDK's objects. stripe-python 15
    removed it; dj-stripe 2.11 patches it back on (djstripe/_stripe_compat.py). If a dj-stripe
    upgrade drops that patch, this fails here instead of in the payment handlers."""
    _deliver(client, _event('evt_test_real_2'))

    (_, obj), = handled
    assert isinstance(obj, stripe.StripeObject), 'the handler no longer receives the SDK object; revisit this test'
    assert obj.get('customer') == 'cus_test_1'
    assert obj.get('missing', 'default') == 'default'


def test_a_failed_payment_runs_our_real_handler_on_the_sdk_object(client, stripe_offline, monkeypatch):
    """The whole subscription path, unstubbed down to the outgoing notification and email:
    handle_webhook_event finds the user and handle_payment_failed reads the invoice's fields."""
    user = UserFactory(stripe_customer_id='cus_test_failed')
    sent = []
    monkeypatch.setattr(SubscriptionService, '_send_payment_failed_notification',
                        staticmethod(lambda *args, **kwargs: sent.append(('notification', args, kwargs))))
    monkeypatch.setattr(SubscriptionService, '_send_payment_failed_email',
                        staticmethod(lambda *args, **kwargs: sent.append(('email', args, kwargs))))
    invoice = {
        'id': 'in_test_1', 'object': 'invoice', 'customer': 'cus_test_failed',
        'attempt_count': 1, 'next_payment_attempt': 1792500000,
    }

    response = _deliver(client, _event('evt_test_real_4', event_type='invoice.payment_failed', obj=invoice))

    assert response.status_code == 200
    kinds = [kind for kind, _, _ in sent]
    assert 'notification' in kinds, 'the payment-failed notification was never reached'
    assert 'email' in kinds, 'the first failed attempt no longer emails'
    assert all(args[0] == user for _, args, _ in sent)


def test_a_donation_checkout_reaches_the_donation_service_as_a_plain_dict(client, stripe_offline, monkeypatch):
    """Under stripe 15 the view's donation branch always takes its non-dict arm (`getattr` for the
    metadata, `to_dict()` for the hand-off), which nothing else tests."""
    from fundraiser.services.donation_service import DonationService

    received = []
    real = DonationService.handle_stripe_payment_completed
    monkeypatch.setattr(DonationService, 'handle_stripe_payment_completed',
                        staticmethod(lambda data: (received.append(data), real(data))[1]))
    session = {
        'id': 'cs_test_1', 'object': 'checkout.session', 'customer': None, 'payment_status': 'paid',
        'metadata': {'type': 'fundraiser_donation', 'donation_id': '999999'},
    }

    response = _deliver(client, _event('evt_test_real_5', event_type='checkout.session.completed', obj=session))

    assert response.status_code == 200
    (data,) = received
    assert type(data) is dict and type(data['metadata']) is dict
    assert data['metadata']['donation_id'] == '999999'
