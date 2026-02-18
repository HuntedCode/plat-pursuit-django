"""
Constants and configuration values for the users app.

This module centralizes Stripe product IDs, price IDs, and premium tier
configurations for better maintainability.
"""

# Premium tier choices (used in model field)
PREMIUM_TIER_CHOICES = [
    ('ad_free', 'Ad Free'),
    ('premium_monthly', 'Premium Monthly'),
    ('premium_yearly', 'Premium Yearly'),
    ('supporter', 'Supporter'),
]

# Premium tier display names mapping
PREMIUM_TIER_DISPLAY = {
    'ad_free': 'Ad Free',
    'premium_monthly': 'Premium Monthly',
    'premium_yearly': 'Premium Yearly',
    'supporter': 'Supporter',
}

# Stripe Product ID Mappings
# Maps subscription tiers to their Stripe product IDs for both test and live modes
STRIPE_PRODUCTS = {
    'test': {
        'ad_free': 'prod_ThqmB1BoJZn7TY',
        'premium_monthly': 'prod_ThqljWr4cvnFFF',
        'premium_yearly': 'prod_ThqpPjDyERnoaF',
        'supporter': 'prod_ThquYbJOcBn65m',
    },
    'live': {
        'ad_free': 'prod_ThtXPwe3AD46Au',
        'premium_monthly': 'prod_ThsI3EuCssYlTT',
        'premium_yearly': 'prod_ThsIi3Xd8fY2Hk',
        'supporter': 'prod_ThtYQAPoY5pSCN',
    }
}

# Stripe Price ID Mappings
# Maps subscription tiers to their Stripe price IDs for both test and live modes
STRIPE_PRICES = {
    'test': {
        'ad_free': 'price_1SkTknR5jhcbjB32fnM6oP5A',
        'premium_monthly': 'price_1SkSXpR5jhcbjB32BA08Bv0o',
        'premium_yearly': 'price_1SkSY0R5jhcbjB327fYUtaJN',
        'supporter': 'price_1SkTlHR5jhcbjB32zjcM2I4P',
    },
    'live': {
        'ad_free': 'price_1SkR4XR5jhcbjB325xchFZm5',
        'premium_monthly': 'price_1SkR3wR5jhcbjB32vEaltpEJ',
        'premium_yearly': 'price_1SkR7jR5jhcbjB32BmKo4iQQ',
        'supporter': 'price_1SkRCuR5jhcbjB32yBFBm1h3',
    }
}

# Premium tiers that grant Discord roles
PREMIUM_DISCORD_ROLE_TIERS = ['premium_monthly', 'premium_yearly']
SUPPORTER_DISCORD_ROLE_TIERS = ['supporter']

# Premium tiers that actually grant premium features (ad_free doesn't)
ACTIVE_PREMIUM_TIERS = ['premium_monthly', 'premium_yearly', 'supporter']

# PayPal Plan ID Mappings
# Maps subscription tiers to their PayPal plan IDs for both sandbox and live modes.
# Create Products and Plans in the PayPal Developer Dashboard, then paste IDs here.
PAYPAL_PLANS = {
    'sandbox': {
        'ad_free': '',
        'premium_monthly': '',
        'premium_yearly': '',
        'supporter': '',
    },
    'live': {
        'ad_free': 'P-51097223GD3632526NGLBPBA',
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
