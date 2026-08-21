"""Regression pins for the 2026-08 payments audit.

Each test here maps to a verified audit finding on the supporter-ladder lane. The two headline
classes: the storefront POST trusting the placeholder flag in live mode (accepting checkouts the
GET refused to render), and both webhooks re-running side effects on provider redeliveries.
"""
from unittest.mock import patch, MagicMock

import pytest
from django.core.cache import cache
from django.db import IntegrityError
from django.urls import reverse

from users.models import SubscriptionPeriod
from users.services.subscription_service import SubscriptionService
from tests.factories import ProfileFactory, UserFactory

pytestmark = pytest.mark.django_db


def _subscriber(tier='patron', provider='stripe'):
    profile = ProfileFactory()
    user = profile.user
    if tier:
        SubscriptionService.activate_subscription(user, tier, provider)
        user.refresh_from_db()
    return user, profile


# ----------------------------------------------------------- the storefront POST live guard ----

def test_the_post_refuses_placeholders_in_live_mode_like_the_get_does(client, settings):
    """A stale SUPPORT_TIERS_ARE_PLACEHOLDERS=True on a live deploy renders the unavailable state
    on GET -- and used to accept the POST anyway, sending an unconfigured tier into checkout."""
    settings.STRIPE_MODE = 'live'
    user = UserFactory()
    client.force_login(user)

    with patch('users.views.SubscriptionService.has_active_subscription',
               return_value=(False, None)), \
            patch('users.views.SubscriptionService.create_checkout_session') as checkout:
        response = client.post(reverse('support_hub'),
                               {'tier': 'patron', 'provider': 'stripe', 'sup-cycle': 'monthly'})

    assert not checkout.called, 'live mode accepted a checkout for an unconfigured tier'
    assert response.status_code == 302


def test_the_availability_check_is_per_provider(client, settings):
    """A PayPal purchase must be admitted on PAYPAL configuration, not Stripe's: with only the
    Stripe price configured, the PayPal POST must be refused (and vice versa the Stripe POST
    goes through)."""
    settings.STRIPE_MODE = 'live'  # live disables the placeholder shortcut entirely
    user = UserFactory()
    client.force_login(user)
    stripe_only = {'live': {'patron': {'monthly': 'price_live_x', 'yearly': 'price_live_y'}}}

    with patch('users.views.SubscriptionService.has_active_subscription',
               return_value=(False, None)), \
            patch.dict('users.constants.STRIPE_LADDER_PRICES', stripe_only), \
            patch('users.services.paypal_service.PayPalService.create_subscription') as paypal_create:
        response = client.post(reverse('support_hub'),
                               {'tier': 'patron', 'provider': 'paypal', 'sup-cycle': 'monthly'})

    assert not paypal_create.called, 'a PayPal checkout was admitted on Stripe configuration'
    assert response.status_code == 302


def test_a_checkout_exception_is_a_message_not_a_500(client):
    """ValueError (tier unconfigured) and Price.DoesNotExist (id unsynced in djstripe) both live
    on the Stripe path; with placeholders live in test mode this used to 500."""
    user = UserFactory()
    client.force_login(user)

    with patch('users.views.SubscriptionService.has_active_subscription',
               return_value=(False, None)), \
            patch('users.views.SubscriptionService.create_checkout_session',
                  side_effect=ValueError('Ladder tier not configured: patron/monthly')):
        response = client.post(reverse('support_hub'),
                               {'tier': 'patron', 'provider': 'stripe', 'sup-cycle': 'monthly'})

    assert response.status_code == 302
    assert response.url == reverse('support_hub')


def test_paypal_availability_reads_the_ladder_map_not_the_legacy_one(client, settings):
    """The button sells ladder levels; its availability must track PAYPAL_LADDER_PLANS. The old
    check consulted legacy PAYPAL_PLANS, so live mode showed the button on the strength of
    grandfathered plan ids alone."""
    settings.PAYPAL_CLIENT_ID = 'client-x'
    ladder = {'sandbox': {'patron': {'monthly': 'P-XX', 'yearly': 'P-YY'}}}

    with patch.dict('users.views.PAYPAL_LADDER_PLANS', {'sandbox': {}}, clear=False):
        empty = client.get(reverse('support_hub')).context['paypal_available']
    with patch.dict('users.views.PAYPAL_LADDER_PLANS', ladder, clear=False):
        filled = client.get(reverse('support_hub')).context['paypal_available']

    assert empty is False, 'the button showed with zero ladder plans configured'
    assert filled is True


# --------------------------------------------------------------------- webhook redeliveries ----

def _stripe_event(event_id='evt_replay_1'):
    event = MagicMock()
    event.id = event_id
    event.type = 'customer.subscription.updated'
    event.data.object = {'customer': 'cus_x'}
    return event


def test_a_redelivered_stripe_event_is_processed_once(client):
    """Stripe delivers at-least-once; the djstripe Event row is the durable replay record. The
    handler (and its welcome email / notification) must not run on the redelivery."""
    event = _stripe_event()

    with patch('users.views.stripe.Webhook.construct_event', return_value=event), \
            patch('users.views.DJStripeEvent') as dj_event, \
            patch('users.views.SubscriptionService.handle_webhook_event') as handler:
        dj_event.objects.filter.return_value.exists.side_effect = [False, True]
        first = client.post(reverse('stripe_webhook'), data=b'{}',
                            content_type='application/json', HTTP_STRIPE_SIGNATURE='sig')
        second = client.post(reverse('stripe_webhook'), data=b'{}',
                             content_type='application/json', HTTP_STRIPE_SIGNATURE='sig')

    assert first.status_code == second.status_code == 200
    assert handler.call_count == 1, 'the replay re-ran the subscription handler'


def test_a_handler_exception_no_longer_500s_the_stripe_webhook(client):
    """With the replay guard, a retry would be skipped anyway -- so a 500 buys nothing. Logged,
    200, same at-most-once semantics as the PayPal handler."""
    event = _stripe_event('evt_crash_1')

    with patch('users.views.stripe.Webhook.construct_event', return_value=event), \
            patch('users.views.DJStripeEvent') as dj_event, \
            patch('users.views.SubscriptionService.handle_webhook_event',
                  side_effect=RuntimeError('boom')):
        dj_event.objects.filter.return_value.exists.return_value = False
        response = client.post(reverse('stripe_webhook'), data=b'{}',
                               content_type='application/json', HTTP_STRIPE_SIGNATURE='sig')

    assert response.status_code == 200


def test_a_redelivered_paypal_event_is_processed_once(client):
    """The dedup is an atomic cache.add (set-if-absent), not the old get-then-set pair two
    concurrent redeliveries could both slip through."""
    cache.clear()
    body = b'{"event_type": "BILLING.SUBSCRIPTION.ACTIVATED", "resource": {}}'

    with patch('users.services.paypal_service.PayPalService.verify_webhook_signature',
               return_value=True), \
            patch('users.services.paypal_service.PayPalService.handle_webhook_event') as handler:
        first = client.post(reverse('paypal_webhook'), data=body,
                            content_type='application/json',
                            HTTP_PAYPAL_TRANSMISSION_ID='tx-123')
        second = client.post(reverse('paypal_webhook'), data=body,
                             content_type='application/json',
                             HTTP_PAYPAL_TRANSMISSION_ID='tx-123')

    assert first.status_code == second.status_code == 200
    assert handler.call_count == 1, 'the replay re-ran the PayPal handler'


# ------------------------------------------------------------------ cross-provider clobbers ----

def test_a_stale_stripe_event_cannot_end_a_paypal_subscribers_premium():
    """stripe_customer_id is kept forever. A late customer.subscription.deleted for a long-dead
    Stripe sub used to reach the deactivate fall-throughs for a user now paying via PayPal --
    nulling their tier, closing their period, stripping their Discord role."""
    user, profile = _subscriber(tier='patron', provider='paypal')
    user.paypal_subscription_id = 'I-LIVEPAYPAL'
    user.stripe_customer_id = 'cus_long_dead'
    user.save(update_fields=['paypal_subscription_id', 'stripe_customer_id'])

    with patch('users.services.subscription_service.Subscription') as sub_model:
        sub_model.objects.filter.return_value.exists.return_value = False
        sub_model.objects.filter.return_value.first.return_value = None
        result = SubscriptionService.update_user_subscription(
            user, 'customer.subscription.deleted')

    user.refresh_from_db()
    profile.refresh_from_db()
    assert result is True
    assert user.premium_tier == 'patron', 'the stale Stripe event ended a PayPal subscription'
    assert profile.user_is_premium is True
    assert SubscriptionPeriod.objects.filter(user=user, ended_at__isnull=True).exists()


def test_verification_link_respects_ladder_tiers():
    """link_profile_to_user carried a hardcoded three-tier legacy list: a paying `patron` who
    linked their PSN profile silently lost the premium denorm (sync cadence, role reconciliation,
    wall eligibility)."""
    from trophies.services.verification_service import VerificationService

    # A user WITHOUT a profile: linking is the moment they get one (OneToOne).
    user = UserFactory()
    SubscriptionService.activate_subscription(user, 'cornerstone', 'stripe')
    user.refresh_from_db()
    unlinked = ProfileFactory(user=None, is_linked=False)

    with patch('core.services.email_service.send_welcome_email', create=True):
        VerificationService.link_profile_to_user(unlinked, user)

    unlinked.refresh_from_db()
    assert unlinked.user_is_premium is True, 'a ladder tier did not survive PSN linking'


def test_is_premium_checks_the_feature_tier_list_on_the_paypal_branch():
    """Bare truthiness would report premium for a paid-but-non-feature tier (the retired
    'ad_free' was one); the branch must ask is_tier_premium like everything else."""
    user, _ = _subscriber(tier=None)
    user.subscription_provider = 'paypal'
    user.paypal_subscription_id = 'I-X'
    user.premium_tier = 'ad_free_retired'
    user.save(update_fields=['subscription_provider', 'paypal_subscription_id', 'premium_tier'])

    assert user.is_premium() is False


def test_a_concurrent_period_insert_does_not_explode_reconcile():
    """Two activation webhooks racing past the exists() check: the loser hits the
    one_open_period_per_user constraint. That means a period is open -- the desired outcome --
    so it must not 500 the webhook."""
    user, _ = _subscriber(tier='patron')
    SubscriptionPeriod.objects.filter(user=user).delete()

    with patch.object(SubscriptionPeriod.objects, 'create',
                      side_effect=IntegrityError('one_open_period_per_user')):
        result = SubscriptionService.reconcile_premium(user, provider_hint='stripe')

    assert result is True
