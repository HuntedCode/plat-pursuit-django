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

# ---------------------------------------------------------------------------------------------
# The supporter ladder.
# ---------------------------------------------------------------------------------------------
# Six levels, monthly or yearly, yearly priced at ten months (two free).
#
# EVERY LEVEL GETS EVERY PERK. What escalates is RECOGNITION, never capability -- that is what keeps
# this a dial and not a door (docs/design/rebuild/premium-proposal.md). Somebody paying $50 gets a
# more visible thank-you than somebody paying $4; neither gets a feature the other cannot reach, and
# nobody gets one a free hunter cannot reach. `test_every_level_gets_every_perk` fails if a level
# ever grows a key beyond price and recognition, because that is the moment this becomes a feature
# ladder and the page's central promise turns false.
#
# NAMING IS UNSETTLED. `bronze` / `silver` / `gold` / `platinum` are ALREADY load-bearing words here:
# they are the PSN trophy types (trophies/constants.py:49) and the badge medallion metals, with their
# own colours in the design system (`.pgl__rung--bronze` = #cf9160, and friends). A "Platinum
# Supporter" on a site where the platinum is what you grind hundreds of hours for reads as buying the
# achievement, which the flair guardrail forbids. `titanium` and `diamond` do not collide. Kept as-is
# pending Jeffrey's call; `slug` and `name` are separate precisely so a rename is one edit here rather
# than a sweep through CSS, copy and colour tokens. (The tier PIP colours in support.css are already
# deliberately clear of the trophy hues for the same reason.)
#
# `recognition` drives the public supporter wall:
#   none  -> not listed          named -> name on the site          linked -> name + a link
SUPPORT_TIERS = [
    {'slug': 'bronze',   'name': 'Bronze Supporter',   'monthly': 4,  'yearly': 40,  'recognition': 'none'},
    {'slug': 'silver',   'name': 'Silver Supporter',   'monthly': 10, 'yearly': 100, 'recognition': 'none'},
    {'slug': 'gold',     'name': 'Gold Supporter',     'monthly': 20, 'yearly': 200, 'recognition': 'named'},
    {'slug': 'platinum', 'name': 'Platinum Supporter', 'monthly': 30, 'yearly': 300, 'recognition': 'named'},
    {'slug': 'titanium', 'name': 'Titanium Supporter', 'monthly': 40, 'yearly': 400, 'recognition': 'linked'},
    {'slug': 'diamond',  'name': 'Diamond Supporter',  'monthly': 50, 'yearly': 500, 'recognition': 'linked'},
]

# The ladder above is DESIGN ONLY until its twelve Stripe prices and twelve PayPal plans exist.
#
# While this is True the storefront renders the ladder with inert buttons, so the page can be designed
# without being blocked on billing configuration. The checkout POST handler, `success_url`,
# `subscribe_success` and the webhooks underneath are all untouched -- switching this off is wiring a
# button to a contract that already works, not building one.
#
# The view forces it False whenever `STRIPE_MODE == 'live'`, so production can never render a row of
# dead buy buttons; it falls back to the unavailable state instead. That is a runtime guard rather
# than a checklist item on purpose, because checklists get skipped.
SUPPORT_TIERS_ARE_PLACEHOLDERS = True

# What members are testing right now, or None between betas.
#
# Early access is one of the two things the Support page sells hardest ("you get a say in what this
# becomes"), so it needs a PERMANENT half that stands on its own -- otherwise the pitch disappears
# for whoever visits during a quiet week, including someone who subscribed FOR it and arrives to find
# nothing there. The template always renders what early access means; this only fills in the current
# example when there is one.
#
# A constant rather than a model on purpose: betas are occasional and this is one blurb. If it ever
# needs scheduling, per-beta signup, or non-staff editing, THAT is when it earns a table.
#
# Set to None when nothing is in testing. Shape when it is:
#     {'name': 'The new Challenges', 'blurb': 'One sentence on what it is and what we want to learn.'}
CURRENT_BETA = None

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
    },
    {
        'slug': 'discord',
        'name': 'Discord',
        'everyone': 'The server is open to anyone',
        'member': 'A supporter role, and the room where we work out what gets built next',
        # Sold as ONE perk on purpose: the say IS the Discord perk, so the dependency on a second
        # platform is visible before payment rather than discovered after.
        'note': 'You will need to be in Discord with us for that part.',
    },
    {
        'slug': 'mark',
        'name': 'Supporter mark',
        'everyone': 'Your name, as you earned it',
        'member': 'A quiet supporter mark beside it, site-wide',
        # Guardrail: flair is a SEPARATE visual language from earned status, never a better one.
        'note': 'Never louder than something you earned.',
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
