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

# What a membership actually gets you.
#
# ONE source of truth, because the two hand-written copies (13 feature cards on the storefront, 11
# checklist rows on the management page) had already drifted apart -- and worse, SEVEN of the
# storefront's thirteen advertised perks no longer existed at all: Dashboard Customization and the
# 9 Premium Modules (dashboard retired), 105+ Site Themes (no premium_theme code remains), Profile
# Showcases and Profile Customization (/profile-editor/ redirects to /), Unlimited Game Lists
# (/lists/ redirects to /), and Full Recap History (the gate was deleted from five places -- it is
# free for everyone now). People were paying against a list that was mostly fiction.
#
# THE SHAPE IS THE POINT. Every entry states what EVERYONE gets and what a MEMBER gets, because
# premium is a dial, not a door (docs/design/rebuild/premium-proposal.md, amended 2026-08-19):
# nothing is ever gated, members just get more of the same thing. A perk that cannot fill in
# `everyone` is a wall, and does not belong here.
#
# Nothing goes on this list that cannot be pointed at in running code. Current anchors:
#   sync       -> SyncService.PREFERRED_COOLDOWN (5m) vs STANDARD_COOLDOWN (1h)
#   discord    -> trophies/services/discord_roles.py, granted off premium_tier
#   mark       -> `.legendary-title` (comments, leaderboard cells) + `.pp-hcard--supporter` (hunters)
#   early      -> trophies/mixins.py -> beta_access_required
PREMIUM_PERKS = [
    {
        'slug': 'sync',
        'name': 'Manual syncing',
        'everyone': 'Once an hour',
        'member': 'Every five minutes',
        'note': 'Automatic background syncing runs for everyone either way.',
    },
    {
        'slug': 'discord',
        'name': 'Discord',
        'everyone': 'The server is open to anyone',
        'member': 'A supporter role, and the room where we work out what gets built next',
        # Sold as ONE perk on purpose: the say IS the Discord perk, so the dependency on a second
        # platform is visible before payment rather than discovered after.
        'note': 'Steering the roadmap happens in conversation there, not as a vote on the site.',
    },
    {
        'slug': 'mark',
        'name': 'Supporter mark',
        'everyone': 'Your name, as you earned it',
        'member': 'A quiet supporter mark beside it, site-wide',
        # Guardrail: flair is a SEPARATE visual language from earned status, never a better one.
        'note': 'Deliberately understated. It says you chip in, never that you hunt better.',
    },
    {
        'slug': 'early',
        'name': 'New things',
        'everyone': 'When they ship',
        'member': 'Before they ship, while they can still change',
    },
    {
        'slug': 'credit',
        'name': 'Credit',
        'everyone': 'Our thanks',
        'member': 'A permanent PlatPursuit Supporter credit, kept even if you stop',
    },
]

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
