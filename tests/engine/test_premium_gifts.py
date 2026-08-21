"""The gift grant primitive: codes, redemption, expiry, comps, and the multi-source rules.

The rules under test are the ones that make grants and subscriptions safe to coexist:

    premium is true while ANY source lives, and ends only when ALL of them are gone

Everything here flows through `SubscriptionService.reconcile_premium` (see
test_premium_reconcile.py for the refactor's own guards); these tests exercise it with REAL grants
where that file used a mocked probe.
"""
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone

from users.models import PremiumGrant, SubscriptionPeriod
from users.services.gift_service import GiftService
from users.services.subscription_service import SubscriptionService
from tests.factories import ProfileFactory

pytestmark = pytest.mark.django_db


def _holder():
    profile = ProfileFactory()
    return profile.user, profile


def _issued(tier='patron', months=1):
    return GiftService.mint_comp(tier, months, note='test fixture')


# ------------------------------------------------------------------------------- redemption ----

def test_redeeming_a_code_makes_the_holder_premium():
    """The three writes that matter: the denorm every surface reads, the sync-tier perk, and the
    provider='gift' period that milestone tenure sums."""
    user, profile = _holder()
    grant = _issued()

    GiftService.redeem(grant.code, user)

    profile.refresh_from_db()
    assert profile.user_is_premium is True
    assert profile.sync_tier == 'preferred'
    assert SubscriptionPeriod.objects.filter(user=user, ended_at__isnull=True,
                                             provider='gift').exists()
    grant.refresh_from_db()
    assert grant.status == 'redeemed'
    assert grant.expires_at > timezone.now() + timedelta(days=27)


def test_a_code_cannot_be_redeemed_twice():
    user, _ = _holder()
    other, _ = _holder()
    grant = _issued()
    GiftService.redeem(grant.code, user)

    with pytest.raises(ValueError, match='already been redeemed'):
        GiftService.redeem(grant.code, other)


def test_redemption_normalises_what_humans_type():
    """The email shows PP-XXXX-XXXX; people type lowercase with spaces around it."""
    user, profile = _holder()
    grant = _issued()

    GiftService.redeem(f'  {grant.code.lower()}  ', user)

    profile.refresh_from_db()
    assert profile.user_is_premium is True


def test_a_grant_does_not_write_premium_tier():
    """Deliberate: `premium_tier` records what somebody PAYS FOR and the credits key off it --
    credit follows the giver, not the recipient. The recipient gets every feature via the denorm."""
    user, profile = _holder()
    GiftService.redeem(_issued().code, user)

    user.refresh_from_db()
    assert user.premium_tier is None
    assert user.is_premium() is True, 'the is_premium fall-through branch is not wired'


def test_gift_tenure_counts_toward_the_premium_months_ladder():
    """`_premium_months` sums ALL SubscriptionPeriod rows regardless of provider, so gift time
    counts automatically -- pinned here so a provider filter never quietly appears there."""
    from milestones.metrics import _premium_months

    user, profile = _holder()
    GiftService.redeem(_issued(months=12).code, user)
    period = SubscriptionPeriod.objects.get(user=user, provider='gift')
    period.started_at = timezone.now() - timedelta(days=90)
    period.save(update_fields=['started_at'])

    assert _premium_months(profile) >= 2


# ------------------------------------------------------------------- the multi-source rules ----

def test_a_lapsing_subscription_does_not_clobber_a_live_gift():
    """THE headline rule, now with a real grant behind it. Subscription dies; the gift keeps the
    holder premium and keeps a period open."""
    user, profile = _holder()
    SubscriptionService.activate_subscription(user, 'patron', 'stripe')
    GiftService.redeem(_issued().code, user)

    SubscriptionService.deactivate_subscription(user, 'stripe')

    profile.refresh_from_db()
    user.refresh_from_db()
    assert user.premium_tier is None
    assert profile.user_is_premium is True, 'the lapsing subscription clobbered the gift'
    assert SubscriptionPeriod.objects.filter(user=user, ended_at__isnull=True).exists()


def test_a_gift_expiring_under_a_live_subscription_changes_nothing():
    user, profile = _holder()
    SubscriptionService.activate_subscription(user, 'patron', 'stripe')
    grant = GiftService.redeem(_issued().code, user)
    grant.expires_at = timezone.now() - timedelta(minutes=1)
    grant.save(update_fields=['expires_at'])

    counts = GiftService.expire_due_grants()

    profile.refresh_from_db()
    assert counts == {'expired': 1, 'still_premium': 1, 'voided_pending': 0}
    assert profile.user_is_premium is True
    assert profile.sync_tier == 'preferred'


def test_premium_ends_only_when_both_sources_are_gone():
    user, profile = _holder()
    SubscriptionService.activate_subscription(user, 'patron', 'stripe')
    grant = GiftService.redeem(_issued().code, user)

    SubscriptionService.deactivate_subscription(user, 'stripe')
    profile.refresh_from_db()
    assert profile.user_is_premium is True

    grant.refresh_from_db()
    grant.expires_at = timezone.now() - timedelta(minutes=1)
    grant.save(update_fields=['expires_at'])
    GiftService.expire_due_grants()

    profile.refresh_from_db()
    assert profile.user_is_premium is False, 'premium survived both sources dying'
    assert profile.sync_tier == 'basic'
    assert not SubscriptionPeriod.objects.filter(user=user, ended_at__isnull=True).exists()


def test_a_grant_holder_can_still_buy_a_real_subscription():
    """`has_active_subscription` is a BILLING guard, not a premium guard: a gift must not block
    the storefront's double-subscribe check, or recipients could never become paying members."""
    user, _ = _holder()
    GiftService.redeem(_issued().code, user)

    has_active, provider = SubscriptionService.has_active_subscription(user)
    assert has_active is False
    assert provider is None


# ------------------------------------------------------------------------------- the sweep ----

def test_the_sweep_is_idempotent():
    user, profile = _holder()
    grant = GiftService.redeem(_issued().code, user)
    grant.expires_at = timezone.now() - timedelta(minutes=1)
    grant.save(update_fields=['expires_at'])

    first = GiftService.expire_due_grants()
    second = GiftService.expire_due_grants()

    assert first['expired'] == 1
    assert second['expired'] == 0, 'the sweep expired the same grant twice'


def test_the_sweep_voids_week_old_abandoned_checkouts():
    stale = PremiumGrant.objects.create(
        tier_slug='patron', months=1, provider='stripe',
        provider_transaction_id='pending_stale123',
    )
    PremiumGrant.objects.filter(id=stale.id).update(
        created_at=timezone.now() - timedelta(days=8)
    )

    counts = GiftService.expire_due_grants()

    stale.refresh_from_db()
    assert stale.status == 'void'
    assert counts['voided_pending'] == 1


def test_dry_run_changes_nothing():
    user, profile = _holder()
    grant = GiftService.redeem(_issued().code, user)
    grant.expires_at = timezone.now() - timedelta(minutes=1)
    grant.save(update_fields=['expires_at'])

    GiftService.expire_due_grants(dry_run=True)

    grant.refresh_from_db()
    profile.refresh_from_db()
    assert grant.status == 'redeemed'
    assert profile.user_is_premium is True


# ------------------------------------------------------------------------------------ comps ----

def test_comp_codes_mint_without_payment_and_redeem_identically():
    user, profile = _holder()
    grant = GiftService.mint_comp('cornerstone', 12, note='event prize')

    assert grant.status == 'issued'
    assert grant.amount == 0
    assert grant.provider == 'comp'
    assert grant.code.startswith('PP-')

    GiftService.redeem(grant.code, user)
    profile.refresh_from_db()
    assert profile.user_is_premium is True


def test_codes_avoid_the_confusable_characters():
    """The alphabet excludes 0/O/1/I because these get read aloud and retyped."""
    for _ in range(20):
        grant = GiftService.mint_comp('backer', 1)
        body = grant.code.replace('PP-', '').replace('-', '')
        assert not set(body) & set('0O1I'), f'{grant.code} contains a confusable character'


# ----------------------------------------------------------------------- the audit is grant-safe

def test_audit_subscription_status_never_selects_grant_only_users():
    """`audit_subscription_status --fix` sweeps users with a premium_tier and revokes through
    deactivate_subscription. A grant-holder has NO premium_tier, so the sweep must never select
    them -- and even if it did, deactivate now reconciles rather than flipping."""
    user, profile = _holder()
    GiftService.redeem(_issued().code, user)

    from users.models import CustomUser
    swept = CustomUser.objects.filter(premium_tier__isnull=False, id=user.id)
    assert not swept.exists(), 'the subscription audit would sweep a grant-only holder'


# -------------------------------------------------------------------------- the redeem page ----

def test_the_redeem_page_prefills_from_the_email_link(client):
    user, _ = _holder()
    client.force_login(user)

    page = client.get(reverse('gift_redeem') + '?code=PP-TEST-CODE').content.decode()
    assert 'PP-TEST-CODE' in page


def test_redeeming_through_the_page_works_end_to_end(client):
    user, profile = _holder()
    client.force_login(user)
    grant = _issued()

    response = client.post(reverse('gift_redeem'), {'code': grant.code})

    assert response.status_code == 302
    profile.refresh_from_db()
    assert profile.user_is_premium is True


def test_a_bad_code_gets_a_human_message_not_a_500(client):
    user, _ = _holder()
    client.force_login(user)

    response = client.post(reverse('gift_redeem'), {'code': 'PP-XXXX-XXXX'}, follow=True)

    assert response.status_code == 200
    assert 'does not exist' in response.content.decode()


def test_the_redeem_page_requires_login_but_keeps_the_code(client):
    """The emailed link works signed out: the login redirect carries the full path, code included,
    so the recipient lands back on a prefilled form."""
    response = client.get(reverse('gift_redeem') + '?code=PP-ABCD-EFGH')

    assert response.status_code == 302
    assert 'code%3DPP-ABCD-EFGH' in response['Location'] or 'code=PP-ABCD-EFGH' in response['Location']
