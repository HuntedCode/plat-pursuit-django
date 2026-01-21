"""
Constants and configuration values for the trophies app.

This module centralizes all magic numbers, platform lists, region codes,
and other constants used throughout the application.
"""

# Platform definitions
MODERN_PLATFORMS = ['PS5', 'PS4']
ALL_PLATFORMS = MODERN_PLATFORMS + ['PS3', 'PSVITA', 'PSVR']
TITLE_STATS_SUPPORTED_PLATFORMS = MODERN_PLATFORMS
PREFERRED_MEDIA_PLATFORMS = ['PS5']

# Title ID blacklist - Games with known issues or duplicates
TITLE_ID_BLACKLIST = [
    'CUSA05214_00', 'CUSA01015_00', 'CUSA00129_00', 'CUSA00131_00',
    'CUSA05365_00', 'PPSA01650_00', 'PPSA02038_00', 'PPSA01614_00',
    'PPSA01604_00', 'PPSA01665_00',
]

# Special accounts for search/development
SEARCH_ACCOUNT_IDS = ['7532533859249281768']

# Region code mappings
NA_REGION_CODES = ['IP', 'UB', 'UP', 'US', 'UT']  # North America
EU_REGION_CODES = ['EB', 'EP']  # Europe
JP_REGION_CODES = ['JA', 'JB', 'JP', 'KP']  # Japan
AS_REGION_CODES = ['HA', 'HB', 'HP', 'HT']  # Asia
KR_REGION_CODES = ['KR']  # Korea
CN_REGION_CODES = ['CN']  # China

# All recognized regions
REGIONS = ['NA', 'EU', 'JP', 'AS', 'KR', 'CN']

# Shovelware detection threshold
# Games with platinum earn rate above this percentage are flagged as shovelware
SHOVELWARE_THRESHOLD = 90.0

# Badge XP values
BRONZE_STAGE_XP = 250  # XP per concept completed at Bronze tier
SILVER_STAGE_XP = 75   # XP per concept completed at Silver tier
GOLD_STAGE_XP = 250    # XP per concept completed at Gold tier
PLAT_STAGE_XP = 75     # XP per concept completed at Platinum tier
BADGE_TIER_XP = 3000   # XP awarded for completing a full badge tier

# Community Guidelines
COMMUNITY_GUIDELINES = [
    {
        "title": "Be Respectful",
        "description": "Treat all members with respect. No harassment, hate speech, personal attacks, or discriminatory language of any kind."
    },
    {
        "title": "Keep It Family-Friendly",
        "description": "Avoid NSFW content, explicit language, and mature themes. We want to maintain a welcoming environment for gamers of all ages."
    },
    {
        "title": "Stay On Topic",
        "description": "Keep discussions relevant to PlayStation gaming, trophies, and the game being discussed. Avoid off-topic conversations."
    },
    {
        "title": "No Spam or Self-Promotion",
        "description": "Don't post repetitive content, advertisements, or excessive self-promotion. Share helpful content, not spam."
    },
    {
        "title": "Use Spoiler Tags",
        "description": "Use spoiler tags ||like this|| when discussing plot points, endings, or major game revelations. Respect others' gaming experiences."
    },
    {
        "title": "Be Helpful and Constructive",
        "description": "Share tips, strategies, and advice that helps the community. Constructive criticism is welcome, but keep it respectful."
    },
    {
        "title": "Follow Platform Rules",
        "description": "Adhere to all applicable laws and PlayStation's Terms of Service. Don't share exploits, hacks, or methods to circumvent game systems."
    },
    {
        "title": "Report Don't Retaliate",
        "description": "If you see rule-breaking content, report it to moderators. Don't engage in arguments or retaliate with similar behavior."
    },
    {
        "title": "Protect Privacy",
        "description": "Don't share personal information about yourself or others. Keep PSN usernames public but respect privacy otherwise."
    }
]

# Banned Words - Comments containing these words will be automatically rejected
# Words are matched case-insensitively with word boundaries to avoid false positives
# Add variations and common misspellings as needed
BANNED_WORDS = [
    # Add your banned words here - this is just a template
    # Keep this list updated through the Django admin or database
]
