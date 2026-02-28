"""
Constants and configuration values for the trophies app.

This module centralizes magic numbers, configuration values, and constants
used throughout the trophies application for better maintainability.
"""

# Pagination Settings
GAMES_PER_PAGE = 25
TROPHIES_PER_PAGE = 50
PROFILES_PER_PAGE = 25
BADGES_PER_PAGE = 25
GUIDES_PER_PAGE = 6
LEADERBOARD_PER_PAGE = 50
TROPHY_CASE_PER_PAGE = 25
PROFILE_DETAIL_PER_PAGE = 50

# Trophy Selection Limits
MAX_TROPHY_SELECTIONS_PREMIUM = 10
MAX_TROPHY_SELECTIONS_FREE = 3

# Sync Configuration
SYNC_COOLDOWN_PREFERRED_MINUTES = 5
SYNC_COOLDOWN_STANDARD_HOURS = 1
SYNC_LOCK_TIMEOUT_SECONDS = 10

# Verification
VERIFICATION_CODE_LENGTH = 3  # Hex characters (results in 6 char string)
VERIFICATION_CODE_EXPIRY_HOURS = 1

# Cache Timeouts (in seconds)
CACHE_TIMEOUT_WEEK = 604800      # 7 days
CACHE_TIMEOUT_DAY = 86400         # 24 hours
CACHE_TIMEOUT_HOUR = 3600         # 1 hour
CACHE_TIMEOUT_RATING = 3600       # 1 hour for rating averages
CACHE_TIMEOUT_STATS = 3600        # 1 hour for game stats
CACHE_TIMEOUT_IMAGES = 86400      # 24 hours for game images
CACHE_TIMEOUT_FEATURED_GUIDE = 86400  # 24 hours

# Batch Processing
PROFILEGAME_BATCH_SIZE = 500
TROPHY_BATCH_SIZE = 100

# Notification Settings
PLATINUM_NOTIFICATION_DELAY_DAYS = 2
DISCORD_NOTIFICATION_ENABLED = True

# Trophy Types
TROPHY_TYPE_BRONZE = 'bronze'
TROPHY_TYPE_SILVER = 'silver'
TROPHY_TYPE_GOLD = 'gold'
TROPHY_TYPE_PLATINUM = 'platinum'

TROPHY_TYPES = [
    TROPHY_TYPE_BRONZE,
    TROPHY_TYPE_SILVER,
    TROPHY_TYPE_GOLD,
    TROPHY_TYPE_PLATINUM,
]

# Trophy Type Display Names
TROPHY_TYPE_DISPLAY = {
    TROPHY_TYPE_BRONZE: 'Bronze',
    TROPHY_TYPE_SILVER: 'Silver',
    TROPHY_TYPE_GOLD: 'Gold',
    TROPHY_TYPE_PLATINUM: 'Platinum',
}

# Sync Status Values
SYNC_STATUS_SYNCED = 'synced'
SYNC_STATUS_SYNCING = 'syncing'
SYNC_STATUS_ERROR = 'error'
SYNC_STATUS_PENDING = 'pending'

SYNC_STATUSES = [
    SYNC_STATUS_SYNCED,
    SYNC_STATUS_SYNCING,
    SYNC_STATUS_ERROR,
    SYNC_STATUS_PENDING,
]

# Sync Tier Values
SYNC_TIER_PREFERRED = 'preferred'
SYNC_TIER_BASIC = 'basic'

# Verification Status Values
VERIFICATION_STATUS_VERIFIED = 'verified'
VERIFICATION_STATUS_PENDING = 'pending'
VERIFICATION_STATUS_FAILED = 'failed'

# Badge Types
BADGE_TYPE_SERIES = 'series'
BADGE_TYPE_COLLECTION = 'collection'
BADGE_TYPE_MEGAMIX = 'megamix'
BADGE_TYPE_DEVELOPER = 'developer'
BADGE_TYPE_MISC = 'misc'

BADGE_TYPES = [
    BADGE_TYPE_SERIES,
    BADGE_TYPE_COLLECTION,
    BADGE_TYPE_MEGAMIX,
    BADGE_TYPE_DEVELOPER,
    BADGE_TYPE_MISC,
]

# Milestone Criteria Types
MILESTONE_CRITERIA_PLAT_COUNT = 'plat_count'
MILESTONE_CRITERIA_COMPLETION_COUNT = 'completion_count'
MILESTONE_CRITERIA_TROPHY_COUNT = 'trophy_count'
MILESTONE_CRITERIA_MANUAL = 'manual'

# Rating Scale
RATING_MIN = 1
RATING_MAX = 10
RATING_TRIMMED_MEAN_PERCENT = 0.1  # Trim 10% from each end for hours calculation

# Profile Stats Update
PROFILE_STATS_UPDATE_BATCH_SIZE = 100

# API Rate Limiting
API_RATE_LIMIT_PER_MINUTE = 60
API_RATE_LIMIT_PER_HOUR = 300

# View Type Options
VIEW_TYPE_GRID = 'grid'
VIEW_TYPE_LIST = 'list'

# Sort Options
SORT_NEWEST = 'newest'
SORT_OLDEST = 'oldest'
SORT_ALPHA = 'alpha'
SORT_COMPLETION = 'completion'
SORT_COMPLETION_INV = 'completion_inv'
SORT_TROPHIES = 'trophies'
SORT_EARNED = 'earned'
SORT_UNEARNED = 'unearned'

# File Upload Limits
MAX_AVATAR_SIZE_MB = 5
MAX_BACKGROUND_SIZE_MB = 10

# Default Values
DEFAULT_TIMEZONE = 'UTC'
DEFAULT_LANGUAGE = 'en'

# Job Queue Priorities
PRIORITY_HIGH = 'high_priority'
PRIORITY_MEDIUM = 'medium_priority'
PRIORITY_LOW = 'low_priority'

JOB_PRIORITIES = [
    PRIORITY_HIGH,
    PRIORITY_MEDIUM,
    PRIORITY_LOW,
]

# Redis Key Prefixes
REDIS_PREFIX_GAME_IMAGES = 'game:imageurls'
REDIS_PREFIX_GAME_STATS = 'game:stats'
REDIS_PREFIX_CONCEPT_AVERAGES = 'concept:averages'
REDIS_PREFIX_LEADERBOARD = 'leaderboard'
REDIS_PREFIX_PROFILE_STATS = 'profile:stats'
REDIS_PREFIX_BADGE_PROGRESS = 'badge:progress'
REDIS_PREFIX_FEATURED_GUIDE = 'featured_guide'

# Leaderboard Types
LEADERBOARD_PLATINUM = 'platinum'
LEADERBOARD_COMPLETION = 'completion'
LEADERBOARD_TROPHIES = 'trophies'
LEADERBOARD_BADGE_XP = 'badge_xp'
LEADERBOARD_BADGE_PROGRESS = 'badge_progress'

# Trophy Rarity Thresholds (PSN rarity percentages)
RARITY_ULTRA_RARE = 1.0      # < 1%
RARITY_VERY_RARE = 5.0       # 1-5%
RARITY_RARE = 15.0           # 5-15%
RARITY_UNCOMMON = 50.0       # 15-50%
RARITY_COMMON = 100.0        # 50-100%

RARITY_LABELS = {
    'ultra_rare': 'Ultra Rare',
    'very_rare': 'Very Rare',
    'rare': 'Rare',
    'uncommon': 'Uncommon',
    'common': 'Common',
}

# Premium Tier Identifiers (matches users.constants but duplicated here for trophies app)
PREMIUM_TIER_AD_FREE = 'ad_free'
PREMIUM_TIER_MONTHLY = 'premium_monthly'
PREMIUM_TIER_YEARLY = 'premium_yearly'
PREMIUM_TIER_SUPPORTER = 'supporter'

# Tiers that grant full premium features
ACTIVE_PREMIUM_TIERS = [
    PREMIUM_TIER_MONTHLY,
    PREMIUM_TIER_YEARLY,
    PREMIUM_TIER_SUPPORTER,
]

# Tab Identifiers for ProfileDetailView
TAB_GAMES = 'games'
TAB_TROPHIES = 'trophies'
TAB_BADGES = 'badges'

PROFILE_TABS = [
    TAB_GAMES,
    TAB_TROPHIES,
    TAB_BADGES,
]

# Milestone Trophy Markers
MILESTONE_FIRST_TROPHY = 'first'
MILESTONE_50_PERCENT = '50_percent'
MILESTONE_PLATINUM = 'platinum'
MILESTONE_100_PERCENT = '100_percent'

# Game Flags
GAME_IS_REGIONAL = 'is_regional'
GAME_IS_SHOVELWARE = 'is_shovelware'
GAME_IS_OBTAINABLE = 'is_obtainable'
GAME_IS_DELISTED = 'is_delisted'
GAME_HAS_ONLINE_TROPHIES = 'has_online_trophies'

# Maximum text lengths (for validation/display)
MAX_USERNAME_LENGTH = 50
MAX_GAME_TITLE_LENGTH = 200
MAX_TROPHY_NAME_LENGTH = 200
MAX_ABOUT_ME_LENGTH = 1000

# Shovelware Detection Thresholds
SHOVELWARE_PLATINUM_RATE_THRESHOLD = 90.0  # > 90% platinum rate
SHOVELWARE_AVG_HOURS_THRESHOLD = 0.5       # < 30 minutes average
