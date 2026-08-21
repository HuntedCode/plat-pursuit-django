"""`audit_subscription_status` and its customer-mismatch arm.

Born from a real 2026-08 prod incident: a customer who paid for a year in January showed as
[NO SUB] because their subscription lived under a DIFFERENT Stripe customer id than the user row
stored (Stripe mints duplicate customers freely and cannot merge them). One `--fix` away from
revoking a paying subscriber. The arm under test repoints instead of deactivating whenever a live
subscription exists under a djstripe customer linked to the user, and only ever revokes rows with
genuinely no live subscription anywhere.
"""
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.utils import timezone
from datetime import timedelta

from djstripe.models import Customer, Subscription

from tests.factories import ProfileFactory

pytestmark = pytest.mark.django_db


def _stripe_premium_user(customer_id='cus_stored'):
    profile = ProfileFactory()
    user = profile.user
    user.premium_tier = 'premium_yearly'
    user.subscription_provider = 'stripe'
    user.stripe_customer_id = customer_id
    user.save(update_fields=['premium_tier', 'subscription_provider', 'stripe_customer_id'])
    return user


def _djstripe_sub(user, customer_id, sub_id='sub_live', status='active', link_subscriber=True):
    """A minimal djstripe Customer + Subscription pair, the shape the audit reads."""
    customer = Customer.objects.create(
        id=customer_id,
        subscriber=user if link_subscriber else None,
    )
    return Subscription.objects.create(
        id=sub_id,
        customer=customer,
        stripe_data={'status': status},
    )


def _run(*args):
    out = StringIO()
    call_command('audit_subscription_status', *args, stdout=out)
    return out.getvalue()


# ------------------------------------------------------------------------ the mismatch arm ----

def test_a_paying_subscriber_under_a_sibling_customer_is_reported_as_mismatch_not_no_sub():
    """The prod incident, reproduced: stored pointer dead, live sub under a linked sibling
    customer. Report mode must say MISMATCH so nobody trusts the NO SUB label alone."""
    user = _stripe_premium_user(customer_id='cus_stale')
    _djstripe_sub(user, 'cus_actual', 'sub_january_yearly')

    output = _run()

    assert '[MISMATCH]' in output
    assert 'cus_actual' in output and 'cus_stale' in output
    assert 'customer mismatches: 1' in output


def test_fix_repoints_the_mismatch_and_never_deactivates(client):
    user = _stripe_premium_user(customer_id='cus_stale')
    _djstripe_sub(user, 'cus_actual', 'sub_january_yearly')

    with patch('users.services.subscription_service.SubscriptionService'
               '.update_user_subscription') as resync, \
            patch('users.services.subscription_service.SubscriptionService'
                  '.deactivate_subscription') as deactivate:
        output = _run('--fix')

    user.refresh_from_db()
    assert user.stripe_customer_id == 'cus_actual', 'the pointer was not repointed'
    assert user.premium_tier == 'premium_yearly', 'a paying subscriber lost their tier'
    assert resync.called
    assert not deactivate.called, 'THE incident: a mismatch row was deactivated'
    assert '[FIXED] Repointed' in output


def test_dry_run_names_the_action_per_row_and_changes_nothing():
    """The operator must see WHICH resolution each row gets before anything runs: REPOINT for the
    mismatch, DEACTIVATE for the true lapse -- in the same output."""
    paying = _stripe_premium_user(customer_id='cus_stale')
    _djstripe_sub(paying, 'cus_actual', 'sub_live_x')
    lapsed = _stripe_premium_user(customer_id='cus_gone')  # no subscription anywhere

    output = _run('--fix', '--dry-run')

    assert 'Would REPOINT' in output and 'Premium kept' in output
    assert 'Would DEACTIVATE' in output and 'No live subscription found anywhere' in output
    paying.refresh_from_db()
    lapsed.refresh_from_db()
    assert paying.stripe_customer_id == 'cus_stale', 'dry-run repointed'
    assert lapsed.premium_tier == 'premium_yearly', 'dry-run deactivated'


def test_an_unlinked_sibling_customer_does_not_count():
    """The link that makes repointing safe is djstripe's Customer.subscriber. A live sub under a
    customer NOT linked to this user proves nothing about them and must not be claimed."""
    user = _stripe_premium_user(customer_id='cus_stale')
    _djstripe_sub(user, 'cus_stranger', 'sub_not_theirs', link_subscriber=False)

    output = _run()

    assert '[MISMATCH]' not in output
    assert '[NO SUB]' in output


def test_a_canceled_sub_elsewhere_does_not_rescue_the_row():
    """Only live statuses (active/past_due/trialing) justify a repoint; a canceled sub under a
    sibling customer is still a lapsed subscriber."""
    user = _stripe_premium_user(customer_id='cus_stale')
    _djstripe_sub(user, 'cus_actual', 'sub_old', status='canceled')

    output = _run()

    assert '[MISMATCH]' not in output


def test_a_user_with_no_stored_customer_id_can_still_be_rescued():
    """The [NO CUSTOMER] arm routes through the same resolver: an empty pointer with a live
    linked sub is a repoint, not a revocation."""
    user = _stripe_premium_user(customer_id='cus_x')
    user.stripe_customer_id = None
    user.save(update_fields=['stripe_customer_id'])
    _djstripe_sub(user, 'cus_actual', 'sub_live_y')

    with patch('users.services.subscription_service.SubscriptionService'
               '.update_user_subscription'), \
            patch('users.services.subscription_service.SubscriptionService'
                  '.deactivate_subscription') as deactivate:
        output = _run('--fix')

    user.refresh_from_db()
    assert user.stripe_customer_id == 'cus_actual'
    assert not deactivate.called


# --------------------------------------------------------------- unchanged behaviour pinned ----

def test_a_true_lapse_is_still_deactivated_by_fix():
    user = _stripe_premium_user(customer_id='cus_gone')

    with patch('users.services.subscription_service.SubscriptionService'
               '.deactivate_subscription') as deactivate:
        _run('--fix')

    assert deactivate.called
    assert deactivate.call_args.args[1] == 'stripe'


def test_an_active_sub_under_the_stored_customer_is_ok_and_untouched():
    user = _stripe_premium_user(customer_id='cus_stored')
    _djstripe_sub(user, 'cus_stored', 'sub_fine')

    with patch('users.services.subscription_service.SubscriptionService'
               '.deactivate_subscription') as deactivate, \
            patch('users.services.subscription_service.SubscriptionService'
                  '.update_user_subscription') as resync:
        output = _run('--fix')

    assert '[OK]' in output
    assert not deactivate.called and not resync.called


def test_an_expired_paypal_row_is_still_deactivated():
    profile = ProfileFactory()
    user = profile.user
    user.premium_tier = 'premium_monthly'
    user.subscription_provider = 'paypal'
    user.paypal_subscription_id = 'I-OLD'
    user.paypal_cancel_at = timezone.now() - timedelta(days=30)
    user.save(update_fields=['premium_tier', 'subscription_provider',
                             'paypal_subscription_id', 'paypal_cancel_at'])

    with patch('users.services.subscription_service.SubscriptionService'
               '.deactivate_subscription') as deactivate:
        output = _run('--fix')

    assert '[EXPIRED]' in output
    assert deactivate.called
