"""
Trophies utility functions and constants.

IMPORTANT: This module now re-exports from organized submodules for backward compatibility.
New code should import directly from the specific service or util modules:

Services (business logic):
- trophies.services.badge_service - Badge checking and awarding
- trophies.services.milestone_service - Milestone checking and awarding
- trophies.services.leaderboard_service - Leaderboard computation
- trophies.services.profile_stats_service - Profile statistics updates

Utils (helpers):
- trophies.utils.cache - Redis client and caching utilities
- trophies.utils.language - Language detection and name matching
- trophies.utils.constants - Platform lists, region codes, constants
"""

# Import from new organized modules for re-export
# This maintains backward compatibility for existing imports

# Cache utilities
from trophies.util_modules.cache import (
    get_redis_client,
    redis_client,
    log_api_call,
)

# Language utilities
from trophies.util_modules.language import (
    match_names,
    count_unique_game_groups,
    calculate_trimmed_mean,
    detect_asian_language,
)

# Constants
from trophies.util_modules.constants import (
    MODERN_PLATFORMS,
    ALL_PLATFORMS,
    TITLE_ID_BLACKLIST,
    TITLE_STATS_SUPPORTED_PLATFORMS,
    PREFERRED_MEDIA_PLATFORMS,
    SEARCH_ACCOUNT_IDS,
    NA_REGION_CODES,
    EU_REGION_CODES,
    JP_REGION_CODES,
    AS_REGION_CODES,
    KR_REGION_CODES,
    CN_REGION_CODES,
    REGIONS,
    SHOVELWARE_THRESHOLD,
    BRONZE_STAGE_XP,
    SILVER_STAGE_XP,
    GOLD_STAGE_XP,
    PLAT_STAGE_XP,
    BADGE_TIER_XP,
)

# Badge service functions
from trophies.services.badge_service import (
    check_profile_badges,
    handle_badge,
    notify_bot_role_earned as badge_notify_bot_role_earned,
    initial_badge_check,
)

# Milestone service functions
from trophies.services.milestone_service import (
    check_and_award_milestone,
    check_all_milestones_for_user,
    notify_bot_role_earned as milestone_notify_bot_role_earned,
)

# Use badge service's notify function as the main one (they're identical)
notify_bot_role_earned = badge_notify_bot_role_earned

# Leaderboard service functions
from trophies.services.leaderboard_service import (
    compute_earners_leaderboard,
    compute_progress_leaderboard,
    compute_total_progress_leaderboard,
    compute_badge_xp_leaderboard,
)

# Profile stats service functions
from trophies.services.profile_stats_service import (
    update_profile_games,
    update_profile_trophy_counts,
)

# Maintain the original logging setup
import logging
logger = logging.getLogger("psn_api")

# All exports for backward compatibility
__all__ = [
    # Cache utilities
    'get_redis_client',
    'redis_client',
    'log_api_call',
    'logger',

    # Language utilities
    'match_names',
    'count_unique_game_groups',
    'calculate_trimmed_mean',
    'detect_asian_language',

    # Constants
    'MODERN_PLATFORMS',
    'ALL_PLATFORMS',
    'TITLE_ID_BLACKLIST',
    'TITLE_STATS_SUPPORTED_PLATFORMS',
    'PREFERRED_MEDIA_PLATFORMS',
    'SEARCH_ACCOUNT_IDS',
    'NA_REGION_CODES',
    'EU_REGION_CODES',
    'JP_REGION_CODES',
    'AS_REGION_CODES',
    'KR_REGION_CODES',
    'CN_REGION_CODES',
    'REGIONS',
    'SHOVELWARE_THRESHOLD',
    'BRONZE_STAGE_XP',
    'SILVER_STAGE_XP',
    'GOLD_STAGE_XP',
    'PLAT_STAGE_XP',
    'BADGE_TIER_XP',

    # Badge functions
    'check_profile_badges',
    'handle_badge',
    'notify_bot_role_earned',
    'initial_badge_check',

    # Milestone functions
    'check_and_award_milestone',
    'check_all_milestones_for_user',

    # Leaderboard functions
    'compute_earners_leaderboard',
    'compute_progress_leaderboard',
    'compute_total_progress_leaderboard',
    'compute_badge_xp_leaderboard',

    # Profile stats functions
    'update_profile_games',
    'update_profile_trophy_counts',
]
