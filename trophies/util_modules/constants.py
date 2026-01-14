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
