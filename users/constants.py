"""
Constants and configuration values for the users app.

This module centralizes Stripe product IDs, price IDs, and premium tier
configurations for better maintainability.
"""

# Premium tier choices (used in model field)
#
# The 'ad_free' tier was retired in 2026-08 along with AdSense itself: it sold the removal of ads,
# so it had nothing left to sell. It had already been withdrawn from the storefront and never had a
# subscriber, hence no migration of existing members. Its Stripe products/prices and PayPal plan are
# archived on those platforms rather than referenced here.
PREMIUM_TIER_CHOICES = [
    ('premium_monthly', 'Premium Monthly'),
    ('premium_yearly', 'Premium Yearly'),
    ('supporter', 'Supporter'),
]

# Premium tier display names mapping
PREMIUM_TIER_DISPLAY = {
    'premium_monthly': 'Premium Monthly',
    'premium_yearly': 'Premium Yearly',
    'supporter': 'Supporter',
}

# Stripe Product ID Mappings
# Maps subscription tiers to their Stripe product IDs for both test and live modes
STRIPE_PRODUCTS = {
    'test': {
        'premium_monthly': 'prod_ThqljWr4cvnFFF',
        'premium_yearly': 'prod_ThqpPjDyERnoaF',
        'supporter': 'prod_ThquYbJOcBn65m',
    },
    'live': {
        'premium_monthly': 'prod_ThsI3EuCssYlTT',
        'premium_yearly': 'prod_ThsIi3Xd8fY2Hk',
        'supporter': 'prod_ThtYQAPoY5pSCN',
    }
}

# Stripe Price ID Mappings
# Maps subscription tiers to their Stripe price IDs for both test and live modes
STRIPE_PRICES = {
    'test': {
        'premium_monthly': 'price_1SkSXpR5jhcbjB32BA08Bv0o',
        'premium_yearly': 'price_1SkSY0R5jhcbjB327fYUtaJN',
        'supporter': 'price_1SkTlHR5jhcbjB32zjcM2I4P',
    },
    'live': {
        'premium_monthly': 'price_1SkR3wR5jhcbjB32vEaltpEJ',
        'premium_yearly': 'price_1SkR7jR5jhcbjB32BmKo4iQQ',
        'supporter': 'price_1SkRCuR5jhcbjB32yBFBm1h3',
    }
}

# Premium tiers that grant Discord roles
PREMIUM_DISCORD_ROLE_TIERS = ['premium_monthly', 'premium_yearly']
SUPPORTER_DISCORD_ROLE_TIERS = ['supporter']

# Premium tiers that actually grant premium features. Every live tier does; the list is kept separate
# from PREMIUM_TIER_CHOICES because the two answer different questions (what can be bought vs. what
# unlocks features), and a future non-feature tier would diverge them again.
ACTIVE_PREMIUM_TIERS = ['premium_monthly', 'premium_yearly', 'supporter']

# PayPal Plan ID Mappings
# Maps subscription tiers to their PayPal plan IDs for both sandbox and live modes.
# Create Products and Plans in the PayPal Developer Dashboard, then paste IDs here.
PAYPAL_PLANS = {
    'sandbox': {
        'premium_monthly': '',
        'premium_yearly': '',
        'supporter': '',
    },
    'live': {
        'premium_monthly': 'P-6FE79903U4175840ENGLBP2A',
        'premium_yearly': 'P-3SY42188DC612830VNGLBQMY',
        'supporter': 'P-5PM309711C131563TNGLBQ3Q',
    }
}

# Reverse lookup: PayPal plan ID -> tier name (built at import time for O(1) webhooks)
PAYPAL_PLAN_TO_TIER = {}
for _mode_plans in PAYPAL_PLANS.values():
    for _tier, _plan_id in _mode_plans.items():
        if _plan_id:
            PAYPAL_PLAN_TO_TIER[_plan_id] = _tier
