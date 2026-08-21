"""`SubscriptionService.reconcile_premium` -- the one premium truth-writer.

Activation and deactivation both converge on this function instead of each carrying its own copy
of "what makes this user premium". These tests pin the CURRENT subscription semantics through the
refactor: green here proves the convergence changed nothing.

Direct coverage matters here because none existed: the old activate/deactivate logic shipped with
only indirect coverage, which is how its unconditional denorm-flip -- the bug reconcile kills --
survived from the beginning.
"""
from datetime import timedelta

import pytest
from django.utils import timezone

from users.models import SubscriptionPeriod
from users.services.subscription_service import SubscriptionService
from tests.factories import ProfileFactory

pytestmark = pytest.mark.django_db


def _subscriber(tier='patron'):
    profile = ProfileFactory()
    return profile.user, profile


def test_activation_flips_the_denorm_and_opens_a_period():
    """The two writes the whole site depends on: `user_is_premium` (what every surface reads) and
    the open SubscriptionPeriod (what milestone tenure sums)."""
    user, profile = _subscriber()

    SubscriptionService.activate_subscription(user, 'patron', 'stripe')

    profile.refresh_from_db()
    assert profile.user_is_premium is True
    assert profile.sync_tier == 'preferred'
    assert SubscriptionPeriod.objects.filter(user=user, ended_at__isnull=True,
                                             provider='stripe').count() == 1


def test_deactivation_clears_the_denorm_and_closes_the_period():
    user, profile = _subscriber()
    SubscriptionService.activate_subscription(user, 'patron', 'stripe')

    SubscriptionService.deactivate_subscription(user, 'stripe')

    profile.refresh_from_db()
    user.refresh_from_db()
    assert profile.user_is_premium is False
    assert profile.sync_tier == 'basic'
    assert user.premium_tier is None
    assert not SubscriptionPeriod.objects.filter(user=user, ended_at__isnull=True).exists()


def test_payment_recovery_reopens_the_recent_period_rather_than_minting_a_new_one():
    """The 14-day reopen keeps milestone tenure honest across Stripe's retry window: a failed
    payment that recovers should read as one continuous period, not two fragments."""
    user, profile = _subscriber()
    SubscriptionService.activate_subscription(user, 'patron', 'stripe')
    period = SubscriptionPeriod.objects.get(user=user)
    period.ended_at = timezone.now() - timedelta(days=3)
    period.save(update_fields=['ended_at'])

    SubscriptionService.activate_subscription(user, 'patron', 'stripe',
                                              event_type='customer.subscription.updated')

    assert SubscriptionPeriod.objects.filter(user=user).count() == 1, 'a second period was minted'
    period.refresh_from_db()
    assert period.ended_at is None, 'the recent period was not reopened'


def test_an_old_closed_period_gets_a_fresh_start():
    """Past the retry window a resubscribe is a new stint, and stitching it onto a months-old
    period would inflate tenure."""
    user, profile = _subscriber()
    SubscriptionService.activate_subscription(user, 'patron', 'stripe')
    period = SubscriptionPeriod.objects.get(user=user)
    period.ended_at = timezone.now() - timedelta(days=60)
    period.save(update_fields=['ended_at'])

    SubscriptionService.activate_subscription(user, 'patron', 'stripe')

    assert SubscriptionPeriod.objects.filter(user=user).count() == 2


def test_reconcile_with_no_hint_and_premium_true_touches_no_period():
    """The rule that makes multi-source premium safe: when SOME source is still alive, a no-hint
    reconcile (a different source just died) must leave the survivor's period alone."""
    user, profile = _subscriber()
    SubscriptionService.activate_subscription(user, 'patron', 'stripe')

    result = SubscriptionService.reconcile_premium(user)   # no hint; tier still set

    assert result is True
    assert SubscriptionPeriod.objects.filter(user=user, ended_at__isnull=True).count() == 1
