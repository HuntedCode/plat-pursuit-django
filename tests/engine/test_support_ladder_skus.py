"""The supporter ladder's SKU wiring: 6 levels x 2 intervals across Stripe and PayPal.

The load-bearing shape: ONE Stripe product per level with TWO prices, because webhook tier recovery
is a PRODUCT-id reverse lookup -- so recovery stays interval-free and gains exactly six entries. On
PayPal, plans are per-interval (twelve of them) and the flat plan->slug reverse map collapses the
interval back out, which is correct: `premium_tier` stores the slug, the interval is billing detail.
"""
import pytest

from users.constants import (ACTIVE_PREMIUM_TIERS, LADDER_SLUGS,
                             PAYPAL_LADDER_PLANS, PREMIUM_DISCORD_ROLE_TIERS,
                             PREMIUM_TIER_CHOICES, PREMIUM_TIER_DISPLAY, STRIPE_LADDER_PRICES,
                             STRIPE_PRODUCTS, SUPPORT_TIERS, SUPPORTER_DISCORD_ROLE_TIERS)


def test_every_ladder_slug_is_a_real_tier_everywhere():
    """A slug missing from any of these maps fails SILENTLY somewhere downstream: the model refuses
    the choice, an email renders 'Unknown', or a webhook cannot recover the tier."""
    choices = dict(PREMIUM_TIER_CHOICES)
    for slug in LADDER_SLUGS:
        assert slug in choices, f'{slug} cannot be stored on CustomUser.premium_tier'
        assert slug in PREMIUM_TIER_DISPLAY, f'{slug} renders as Unknown in every email'
        assert slug in ACTIVE_PREMIUM_TIERS, f'{slug} grants no features'
        for mode in ('test', 'live'):
            assert slug in STRIPE_PRODUCTS[mode], f'{slug} unrecoverable from a {mode} webhook'
            assert set(STRIPE_LADDER_PRICES[mode][slug]) == {'monthly', 'yearly'}
        # NON-EMPTY, not merely present: the presence-only version passed on '' while test-mode
        # webhooks deactivated real ladder purchases (the beta incident). Live stays empty by
        # design until cutover -- that half lives in test_live_ids_stay_empty_until_cutover.
        assert STRIPE_PRODUCTS['test'][slug], f'{slug} has no test product id; its webhook deactivates the buyer'
        for mode in ('sandbox', 'live'):
            assert set(PAYPAL_LADDER_PLANS[mode][slug]) == {'monthly', 'yearly'}


def test_the_legacy_tiers_survive_in_every_recovery_map():
    """Grandfathering lives or dies here: the webhooks recover a renewing subscription's tier from
    these maps, so deleting a legacy entry silently deactivates every subscriber on it at their next
    renewal event."""
    from users.services.subscription_service import SubscriptionService

    for mode in ('test', 'live'):
        for legacy in ('premium_monthly', 'premium_yearly', 'supporter'):
            product_id = STRIPE_PRODUCTS[mode][legacy]
            assert product_id, f'{legacy} lost its {mode} product id'
            assert SubscriptionService.get_tier_from_product_id(product_id) == legacy


def test_ladder_price_resolution_is_isolated_from_the_legacy_path():
    """`resolve_ladder_price_id` returns None for an unconfigured pair rather than raising -- the
    legacy resolver raises on ONE miss and its caller degrades everything, which is the right shape
    for three tiers that exist together and the wrong one for a ladder that fills in per bootstrap
    run. Since the 2026-08-21 test-mode bootstrap, TEST resolves and LIVE is the unconfigured
    side (empty until rebuild cutover, deliberately)."""
    from users.services.subscription_service import SubscriptionService

    # Test mode: configured by the bootstrap paste.
    assert SubscriptionService.resolve_ladder_price_id('patron', 'monthly', False) ==         STRIPE_LADDER_PRICES['test']['patron']['monthly']
    # Unconfigured -> None, never an exception. Live emptiness IS the pre-cutover safety state.
    assert SubscriptionService.resolve_ladder_price_id('patron', 'monthly', True) is None
    assert SubscriptionService.resolve_ladder_price_id('nonsense', 'monthly', False) is None
    assert SubscriptionService.resolve_ladder_price_id('patron', 'weekly', False) is None


def test_live_ids_stay_empty_until_cutover():
    """THE fan-out hazard pin: live SKU ids existing before prod runs ladder-aware code would
    have prod deactivating the subscribers who buy them. When the cutover paste lands, this test
    is UPDATED deliberately -- it failing is the point."""
    for slug, intervals in STRIPE_LADDER_PRICES['live'].items():
        assert not any(intervals.values()), f'live Stripe id for {slug} before cutover'
    for slug, intervals in PAYPAL_LADDER_PLANS['live'].items():
        assert not any(intervals.values()), f'live PayPal id for {slug} before cutover'
    for slug in LADDER_SLUGS:
        assert not STRIPE_PRODUCTS['live'][slug], f'live product id for {slug} before cutover'


def test_discord_roles_ladder_gets_premium_and_plus_stays_legacy():
    """Decided 2026-08-20: all six levels grant the PREMIUM role; the PLUS role stays with the
    legacy supporter tier only, until it dies out."""
    for slug in LADDER_SLUGS:
        assert slug in PREMIUM_DISCORD_ROLE_TIERS, f'{slug} grants no Discord role'
        assert slug not in SUPPORTER_DISCORD_ROLE_TIERS, f'{slug} grants the Plus role'
    assert SUPPORTER_DISCORD_ROLE_TIERS == ['supporter']


def test_ladder_prices_in_the_constant_match_the_design():
    """The bootstrap command creates SKUs FROM these numbers, and the support band prices ladder
    supporters from them too -- so a drifted number here miscounts the transparency row and
    mis-creates the SKU in the same stroke."""
    by_slug = {t['slug']: t for t in SUPPORT_TIERS}
    assert [by_slug[s]['monthly'] for s in LADDER_SLUGS] == [4, 10, 15, 20, 25, 30]
    for slug in LADDER_SLUGS:
        assert by_slug[slug]['yearly'] == by_slug[slug]['monthly'] * 10
