"""
Milestone constants - Single source of truth for milestone type definitions.

Shared across milestone_service, milestone_handlers, badge_views, and signals.
"""
from collections import OrderedDict

# Month key to month number mapping (used by calendar handlers)
MONTH_MAP = {
    'calendar_month_jan': 1, 'calendar_month_feb': 2, 'calendar_month_mar': 3,
    'calendar_month_apr': 4, 'calendar_month_may': 5, 'calendar_month_jun': 6,
    'calendar_month_jul': 7, 'calendar_month_aug': 8, 'calendar_month_sep': 9,
    'calendar_month_oct': 10, 'calendar_month_nov': 11, 'calendar_month_dec': 12,
}

# Derived list of all calendar month criteria types
CALENDAR_MONTH_TYPES = list(MONTH_MAP.keys())

# All calendar-related criteria types (months + aggregate types)
ALL_CALENDAR_TYPES = set(CALENDAR_MONTH_TYPES) | {'calendar_months_total', 'calendar_complete'}

# One-off criteria types: binary earned/not, no tier ladder
ONE_OFF_TYPES = {
    'psn_linked', 'discord_linked', 'manual', 'is_premium', 'calendar_complete',
} | set(CALENDAR_MONTH_TYPES)

# Category configuration for the milestone list page tabs
MILESTONE_CATEGORIES = OrderedDict([
    ('overview', {
        'name': 'Overview',
        'icon': 'overview',
        'criteria_types': [],
    }),
    ('trophy_hunting', {
        'name': 'Trophy Hunting',
        'icon': 'trophy',
        'criteria_types': ['plat_count', 'trophy_count', 'completion_count', 'playtime_hours'],
    }),
    ('community', {
        'name': 'Community',
        'icon': 'community',
        'criteria_types': ['rating_count', 'comment_upvotes', 'checklist_upvotes'],
    }),
    ('collection', {
        'name': 'Collection',
        'icon': 'collection',
        'criteria_types': ['badge_count', 'stage_count'],
    }),
    ('challenges', {
        'name': 'Challenges',
        'icon': 'challenges',
        'criteria_types': ['az_progress', 'calendar_months_total', 'calendar_complete'] + CALENDAR_MONTH_TYPES,
    }),
    ('getting_started', {
        'name': 'Getting Started',
        'icon': 'rocket',
        'criteria_types': ['psn_linked', 'discord_linked', 'is_premium'],
    }),
    ('supporter', {
        'name': 'Supporter',
        'icon': 'supporter',
        'criteria_types': ['subscription_months'],
    }),
    ('special', {
        'name': 'Special',
        'icon': 'sparkle',
        'criteria_types': ['manual'],
    }),
])

# Human-readable display names for criteria types
CRITERIA_TYPE_DISPLAY_NAMES = {
    'plat_count': 'Platinum Trophies',
    'trophy_count': 'Total Trophies',
    'completion_count': '100% Completions',
    'playtime_hours': 'Playtime Hours',
    'rating_count': 'Game Ratings',
    'comment_upvotes': 'Comment Upvotes',
    'checklist_upvotes': 'Checklist Upvotes',
    'badge_count': 'Badges Earned',
    'stage_count': 'Badge Stages Completed',
    'az_progress': 'A-Z Challenge Progress',
    'calendar_months_total': 'Calendar Months Completed',
    'calendar_complete': 'Full Calendar',
    'is_premium': 'Premium Membership',
    'psn_linked': 'PSN Account',
    'discord_linked': 'Discord Account',
    'manual': 'Special Awards',
    'subscription_months': 'Subscription Loyalty',
}
for _m in ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec']:
    CRITERIA_TYPE_DISPLAY_NAMES[f'calendar_month_{_m}'] = f'{_m.capitalize()} Calendar'
