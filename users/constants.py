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
# The first three are the LEGACY tiers, grandfathered 2026-08: their billing never changes and the
# webhooks renew them forever, but the storefront no longer offers them. The six ladder slugs are
# what it sells. `premium_tier` stores a SLUG only -- monthly vs yearly is a billing detail that
# lives on the processor, never on the user.
PREMIUM_TIER_CHOICES = [
    ('premium_monthly', 'Premium Monthly'),
    ('premium_yearly', 'Premium Yearly'),
    ('supporter', 'Supporter'),
    ('backer', 'Backer'),
    ('contributor', 'Contributor'),
    ('patron', 'Patron'),
    ('sponsor', 'Sponsor'),
    ('benefactor', 'Benefactor'),
    ('cornerstone', 'Cornerstone'),
]

# Premium tier display names mapping
PREMIUM_TIER_DISPLAY = {
    'premium_monthly': 'Premium Monthly',
    'premium_yearly': 'Premium Yearly',
    'supporter': 'Supporter',
    'backer': 'Backer',
    'contributor': 'Contributor',
    'patron': 'Patron',
    'sponsor': 'Sponsor',
    'benefactor': 'Benefactor',
    'cornerstone': 'Cornerstone',
}

# ---------------------------------------------------------------------------------------------
# The supporter ladder.
# ---------------------------------------------------------------------------------------------
# Six levels, monthly or yearly, yearly priced at ten months (two free).
#
# EVERY LEVEL GETS EVERY PERK. What escalates is RECOGNITION, never capability -- that is what keeps
# this a dial and not a door (docs/design/rebuild/premium-proposal.md). Somebody paying $30 gets a
# more visible thank-you than somebody paying $4; neither gets a feature the other cannot reach, and
# nobody gets one a free hunter cannot reach. `test_every_level_gets_every_perk` fails if a level
# ever grows a key beyond price, recognition and its own look, because that is the moment this
# becomes a feature ladder and the page's central promise turns false.
#
# NAMES COME FROM THE GIVING REGISTER, NOT THE STANDING ONE. That is a hard constraint, not a
# preference. This site already spends "evocative single-noun standing word" on three separate EARNED
# systems -- the 11 `PURSUER_RANKS` (Newbie ... Warden, Marshal, Vanquisher, Paragon, Luminary,
# Ascendant), the 24 Job names (Slayer, Vanguard, Pathfinder, Cartographer, Mastermind, Champion,
# Maestro ...) and the PSN trophy grades. Any supporter name from that field either collides outright
# or simply reads as something a hunter earned, which the flair guardrail in `visual-identity.md`
# forbids: a bought marker must never read as "better hunter".
#
# Two earlier ladders proved it. Bronze/Silver/Gold/Platinum collided with the trophy grades AND the
# badge medallion metals. The replacement -- Friend/Ally/Patron/Champion/Guardian/Luminary -- looked
# safe and was not: **Luminary is the 10th Pursuer rank**, earned around 690 games, and **Champion is
# a Job** in the heart discipline. Someone paying $30 a month would have carried the name of a rank
# somebody else ground hundreds of hours for.
#
# These words describe what you DO for the project. Nobody would ever name an achievement "Backer",
# so the field is structurally safe rather than accidentally clear, and `test_no_level_name_collides
# _with_something_earned` checks it against every ladder in the codebase rather than trusting review.
#
# THE MARK IS A STAR, and only a star, at every level. It builds an outline star -> one filled ->
# two -> three -> four -> five, so a level is legible at a glance without ever introducing a second
# shape that could be mistaken for something earned. `colour` is per level and rotates through the
# hue wheel (sea green -> sky -> periwinkle -> violet -> orchid -> rose): a continuous, obviously
# synthetic ramp with no metal anywhere in it.
#
# EVERY LEVEL IS CREDITED. There was a `recognition` field once, keeping the bottom two levels off
# the supporter wall so that being listed was what the middle of the ladder bought. It is gone, and
# the reasoning is worth keeping: a wall that lists only the higher levels is thin until there ARE
# higher levels, and the obvious fix -- hide the bottom rungs once enough people are above them --
# would take somebody's credit away after they had it. Removing recognition from a person who
# already had it is worse than never giving it, so the rule is simply that everyone supporting is
# credited.
#
# If it is ever gated again, that is one field and one filter, not a redesign.
#
SUPPORT_TIERS = [
    {'slug': 'backer',      'name': 'Backer',      'monthly': 4,  'yearly': 40,
     'stars': 1, 'outline': True,  'colour': '#4fc4a3'},
    {'slug': 'contributor', 'name': 'Contributor', 'monthly': 10, 'yearly': 100,
     'stars': 1, 'outline': False, 'colour': '#47b6e6'},
    {'slug': 'patron',      'name': 'Patron',      'monthly': 15, 'yearly': 150,
     'stars': 2, 'outline': False, 'colour': '#6875ee'},
    {'slug': 'sponsor',     'name': 'Sponsor',     'monthly': 20, 'yearly': 200,
     'stars': 3, 'outline': False, 'colour': '#a666ea'},
    {'slug': 'benefactor',  'name': 'Benefactor',  'monthly': 25, 'yearly': 250,
     'stars': 4, 'outline': False, 'colour': '#e55dd9'},
    {'slug': 'cornerstone', 'name': 'Cornerstone', 'monthly': 30, 'yearly': 300,
     'stars': 5, 'outline': False, 'colour': '#f56a9e'},
]

# THE PALETTE IS A MEASURED RAMP, not six hand-picked colours. Hue steps ~35 degrees from teal to
# rose, and lightness climbs toward the top so the last two levels differ on TWO axes.
#
# That last part is why: an earlier ramp put Benefactor at hue 314 and Cornerstone at 338 -- 23
# degrees apart, both mid-lightness, both plainly "pink". At 11px on a star, or in a name on a
# leaderboard row, they were not tellable apart. `test_no_two_levels_look_alike` measures the gaps
# rather than trusting the hexes to look distinct in a list.
#
# Deliberately no warm metal anywhere in it: bronze, silver, gold and platinum are the trophy grades
# AND the badge medallion metals here, so a warm level would put a bought mark in the same visual
# family as an earned grade.

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
SUPPORT_TIERS_ARE_PLACEHOLDERS = False


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
#   mark       -> `.pp-supname`/`.pp-supstar` (the successor pair; site-wide wiring pending the
#                 Profile tier denorm) -- `.legendary-title` is LEGACY and being removed
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
# ONE product per ladder level with TWO prices hanging off it. That shape is load-bearing: webhook
# tier recovery is a PRODUCT-id reverse lookup (`get_tier_from_product_id` scans this map), so a
# product-per-level keeps recovery interval-free -- six new entries here and zero code change there.
# Ladder ids are EMPTY until `bootstrap_support_skus` creates them and prints the block to paste.
#
# ⚠ LIVE ladder ids must not exist before the rebuild cutover: webhooks fan out to every registered
# endpoint, and prod's `main` build treats an unknown product as deactivate-worthy. See the plan's
# prod-safety note and docs/guides/support-skus.md.
STRIPE_PRODUCTS = {
    'test': {
        'premium_monthly': 'prod_ThqljWr4cvnFFF',
        'premium_yearly': 'prod_ThqpPjDyERnoaF',
        'supporter': 'prod_ThquYbJOcBn65m',
        'backer': '',
        'contributor': '',
        'patron': '',
        'sponsor': '',
        'benefactor': '',
        'cornerstone': '',
    },
    'live': {
        'premium_monthly': 'prod_ThsI3EuCssYlTT',
        'premium_yearly': 'prod_ThsIi3Xd8fY2Hk',
        'supporter': 'prod_ThtYQAPoY5pSCN',
        'backer': '',
        'contributor': '',
        'patron': '',
        'sponsor': '',
        'benefactor': '',
        'cornerstone': '',
    }
}

# Stripe Price ID Mappings -- LEGACY TIERS ONLY, deliberately.
# `get_prices_from_stripe` raises on ONE missing id and `_prices()` then returns {} for everything,
# which would blank the legacy support band the moment a ladder price was unsynced. Ladder prices
# live in STRIPE_LADDER_PRICES below and resolve through their own helper.
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

# The ladder's twelve Stripe prices: {mode: {slug: {interval: price_id}}}. Filled by
# `bootstrap_support_skus` (which also syncs them into djstripe -- checkout does
# `Price.objects.get`, so an unsynced id 500s the moment the placeholder flag flips).
STRIPE_LADDER_PRICES = {
    'test': {
        'backer': {'monthly': 'price_1U6ozjR5jhcbjB32vStaZGFu', 'yearly': 'price_1U6ozjR5jhcbjB32OrSMYweY'},
        'contributor': {'monthly': 'price_1U6ozjR5jhcbjB32pGR5QfTE', 'yearly': 'price_1U6ozjR5jhcbjB32Sl7gZ63E'},
        'patron': {'monthly': 'price_1U6ozkR5jhcbjB32SG0QxrP5', 'yearly': 'price_1U6ozkR5jhcbjB32rstksVZ5'},
        'sponsor': {'monthly': 'price_1U6ozkR5jhcbjB32ifOJlGei', 'yearly': 'price_1U6ozkR5jhcbjB3205hXP412'},
        'benefactor': {'monthly': 'price_1U6ozlR5jhcbjB320zizoLjt', 'yearly': 'price_1U6ozlR5jhcbjB32b9FE2O1Q'},
        'cornerstone': {'monthly': 'price_1U6ozlR5jhcbjB32MBfED7X1', 'yearly': 'price_1U6ozlR5jhcbjB32RimP8LHD'},
    },
    # LIVE stays empty until rebuild cutover -- see the fan-out hazard note above and
    # docs/guides/support-skus.md. bootstrap_support_skus --live-ok fills it, nothing else.
    'live': {slug: {'monthly': '', 'yearly': ''} for slug in
             ('backer', 'contributor', 'patron', 'sponsor', 'benefactor', 'cornerstone')},
}

# The ladder's twelve PayPal plans, same shape (PayPal plans are per-interval, so twelve of them;
# the flat reverse map below collapses the interval back out, which is fine -- the tier IS the slug).
PAYPAL_LADDER_PLANS = {
    'sandbox': {
        'backer': {'monthly': 'P-23W906671W757821JNKEB34Y', 'yearly': 'P-76U879239G148313LNKEB34Y'},
        'contributor': {'monthly': 'P-4PN39583R75130028NKEB35I', 'yearly': 'P-1PB32711DL985164FNKEB35I'},
        'patron': {'monthly': 'P-20E18527BJ007815VNKEB35Y', 'yearly': 'P-08S10938GY144830XNKEB35Y'},
        'sponsor': {'monthly': 'P-59391312V9169962DNKEB36I', 'yearly': 'P-0J052628DY755845BNKEB36I'},
        'benefactor': {'monthly': 'P-7AF02996PT9535847NKEB36Q', 'yearly': 'P-28D98899V0798471PNKEB36Y'},
        'cornerstone': {'monthly': 'P-6MD3335322577564ANKEB37I', 'yearly': 'P-3KL63947BR196261BNKEB37I'},
    },
    'live': {slug: {'monthly': '', 'yearly': ''} for slug in
             ('backer', 'contributor', 'patron', 'sponsor', 'benefactor', 'cornerstone')},
}

# Premium tiers that grant Discord roles. All six ladder levels grant the PREMIUM role (decided
# 2026-08-20); the PLUS role stays with the legacy `supporter` tier only, until it dies out.
PREMIUM_DISCORD_ROLE_TIERS = ['premium_monthly', 'premium_yearly',
                              'backer', 'contributor', 'patron',
                              'sponsor', 'benefactor', 'cornerstone']
SUPPORTER_DISCORD_ROLE_TIERS = ['supporter']

# Premium tiers that actually grant premium features. Every live tier does; the list is kept separate
# from PREMIUM_TIER_CHOICES because the two answer different questions (what can be bought vs. what
# unlocks features), and a future non-feature tier would diverge them again.
ACTIVE_PREMIUM_TIERS = ['premium_monthly', 'premium_yearly', 'supporter',
                        'backer', 'contributor', 'patron',
                        'sponsor', 'benefactor', 'cornerstone']

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

# Reverse lookup: PayPal plan ID -> tier name (built at import time for O(1) webhooks).
# Walks the legacy flat map AND the nested ladder map; the ladder's two intervals collapse to one
# slug, which is exactly right -- `premium_tier` stores the slug and the interval stays a billing
# detail on the processor.
PAYPAL_PLAN_TO_TIER = {}
for _mode_plans in PAYPAL_PLANS.values():
    for _tier, _plan_id in _mode_plans.items():
        if _plan_id:
            PAYPAL_PLAN_TO_TIER[_plan_id] = _tier
for _mode_plans in PAYPAL_LADDER_PLANS.values():
    for _tier, _intervals in _mode_plans.items():
        for _plan_id in _intervals.values():
            if _plan_id:
                PAYPAL_PLAN_TO_TIER[_plan_id] = _tier

# Derived conveniences for the checkout path.
LADDER_SLUGS = [t['slug'] for t in SUPPORT_TIERS]

# GRANDFATHERED PRESENTATION (decided 2026-08-21): legacy subscribers keep their billing and their
# tier slugs untouched, but WEAR the ladder level nearest their price -- colour, stars, level name
# -- on the Credits wall and anywhere else supporter identity renders. Presentation only: nothing
# reads this map for billing, availability, or role decisions. The mapping is by price proximity,
# so if a legacy price ever changes on the processor side, revisit the target here.
LEGACY_TIER_LEVEL_MAP = {
    'premium_monthly': 'backer',
    'premium_yearly': 'backer',
    'supporter': 'contributor',
}
