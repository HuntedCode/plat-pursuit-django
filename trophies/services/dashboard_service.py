"""
Dashboard module registry and data provider service.

The dashboard uses a Module Registry pattern where each module is a self-contained
triple: a Python data provider function, an HTML partial template, and optional JS.

Adding a new module:
    1. Write the provider function (in this file or import from a dedicated module)
    2. Add a descriptor dict to DASHBOARD_MODULES referencing the provider callable
    3. Create the partial template in templates/trophies/partials/dashboard/
"""
import inspect
import logging
from django.core.cache import cache
from django.db.models import F, FloatField, ExpressionWrapper

logger = logging.getLogger(__name__)

# Maximum number of modules free users can hide
MAX_FREE_HIDDEN = 3


def get_effective_premium(request):
    """
    Return effective premium status, accounting for staff preview override.

    Staff users can toggle a session variable to simulate premium/free views.
    Falls back to real profile.user_is_premium when no override is set.
    """
    preview = request.session.get('dashboard_preview_premium')
    if preview is not None:
        return preview
    return request.user.profile.user_is_premium

# Default cache TTL for lazy-loaded modules (seconds)
DEFAULT_CACHE_TTL = 600  # 10 minutes

# Valid module sizes and their CSS grid classes
# Grid: grid-cols-2 (tablet) / lg:grid-cols-4 (desktop) / 2xl:grid-cols-6 (1536px+)
VALID_SIZES = ('small', 'medium', 'large')
VALID_LOAD_STRATEGIES = ('server', 'lazy')

SIZE_GRID_CLASSES = {
    'small':  'col-span-2 lg:col-span-2 2xl:col-span-2',   # full tablet, 1/2 desktop, 1/3 wide
    'medium': 'col-span-2 lg:col-span-2 2xl:col-span-3',   # full tablet, 1/2 desktop, 1/2 wide
    'large':  'col-span-2 lg:col-span-4 2xl:col-span-6',   # full everywhere
}

# Item limits per module size
SIZE_LIMITS = {
    'small': 3,
    'medium': 6,
    'large': 10,
}


# ---------------------------------------------------------------------------
# Data Providers
# ---------------------------------------------------------------------------

def provide_trophy_snapshot(profile):
    """Trophy collection summary. Zero additional queries (all on Profile)."""
    total_earned = profile.total_trophies - profile.total_unearned
    return {
        'total_plats': profile.total_plats,
        'total_golds': profile.total_golds,
        'total_silvers': profile.total_silvers,
        'total_bronzes': profile.total_bronzes,
        'total_trophies': profile.total_trophies,
        'total_earned': total_earned,
        'total_unearned': profile.total_unearned,
        'total_games': profile.total_games,
        'total_completes': profile.total_completes,
        'total_hiddens': profile.total_hiddens,
        'avg_progress': profile.avg_progress,
        'trophy_level': profile.trophy_level,
        'tier': profile.tier,
        'is_plus': profile.is_plus,
        'earn_rate': round(total_earned / profile.total_trophies * 100, 1) if profile.total_trophies else 0,
    }


def provide_recent_platinums(profile, settings=None):
    """Last N platinum trophies earned with game info and rarity."""
    from trophies.models import EarnedTrophy

    settings = settings or {}
    limit = settings.get('limit', 6)
    plats = (
        EarnedTrophy.objects
        .filter(profile=profile, trophy__trophy_type='platinum', earned=True)
        .select_related('trophy__game__concept')
        .order_by('-earned_date_time')[:limit]
    )

    platinums = []
    for et in plats:
        game = et.trophy.game
        concept = getattr(game, 'concept', None) if game else None
        platinums.append({
            'game_name': concept.unified_title if concept else game.title_name if game else 'Unknown',
            'icon_url': concept.concept_icon_url if concept else (game.title_image if game else ''),
            'earned_date': et.earned_date_time,
            'earn_rate': et.trophy.trophy_earn_rate,
            'np_communication_id': game.np_communication_id if game else None,
        })

    return {'platinums': platinums}


def provide_challenge_hub(profile, size='large'):
    """Overview of all 3 challenge types with mini visual previews."""
    from trophies.models import Challenge
    from trophies.services.challenge_service import get_calendar_stats, get_calendar_month_data
    from django.utils import timezone
    import pytz

    challenges = Challenge.objects.filter(profile=profile, is_deleted=False)

    # Get user's timezone for calendar today highlight
    tz_name = profile.user.user_timezone if profile.user else 'UTC'
    user_tz = pytz.timezone(tz_name or 'UTC')
    now_local = timezone.now().astimezone(user_tz)

    result = {}
    for ctype in ('az', 'calendar', 'genre'):
        # Priority: active first, then most recently completed
        challenge = (
            challenges.filter(challenge_type=ctype, is_complete=False).first()
            or challenges.filter(challenge_type=ctype, is_complete=True).order_by('-completed_at').first()
        )

        if not challenge:
            result[ctype] = None
            continue

        data = {
            'challenge_id': challenge.id,
            'challenge_name': challenge.name,
            'is_complete': challenge.is_complete,
            'completed_at': challenge.completed_at,
        }

        if ctype == 'az':
            data['filled'] = challenge.filled_count
            data['completed'] = challenge.completed_count
            data['total'] = 26
            data['pct'] = round(challenge.completed_count / 26 * 100) if 26 else 0
            # Letter strip data
            slots = challenge.az_slots.all().order_by('letter')
            data['slots'] = [
                {'letter': s.letter, 'filled': s.game_id is not None, 'completed': s.is_completed}
                for s in slots
            ]
        elif ctype == 'calendar':
            month_data = get_calendar_month_data(challenge)
            stats = get_calendar_stats(challenge, month_data=month_data)
            filled = stats.get('total_filled', 0)
            data['filled'] = filled
            data['total'] = 365
            data['streak'] = stats.get('longest_streak', 0)
            data['pct'] = round(filled / 365 * 100)
            # Current month mini-calendar with today highlight
            current_month_num = now_local.month
            current_month = month_data[current_month_num - 1]
            data['current_month'] = {
                'name': current_month['month_name'],
                'weekday_offset': current_month['weekday_offset'],
                'today': now_local.day,
                'days': [{'day': d['day'], 'is_filled': d['is_filled']} for d in current_month['days']],
            }
        elif ctype == 'genre':
            from trophies.services.challenge_service import get_subgenre_status
            data['filled'] = challenge.filled_count
            data['completed'] = challenge.completed_count
            total = challenge.total_items or challenge.genre_slots.count()
            data['total'] = total
            data['bonus_count'] = challenge.bonus_count
            data['pct'] = round(challenge.completed_count / total * 100) if total else 0
            # Genre tag data
            slots = challenge.genre_slots.all().order_by('genre')
            data['slots'] = [
                {'genre': s.genre_display or s.genre, 'filled': s.concept_id is not None, 'completed': s.is_completed}
                for s in slots
            ]
            # Subgenre tags with status
            subgenre_status = get_subgenre_status(challenge)
            data['subgenres'] = sorted([
                {'name': key.replace('_', ' ').title(), 'status': status}
                for key, status in subgenre_status.items()
            ], key=lambda s: (s['status'] != 'platted', s['name']))
            data['subgenre_total'] = len(subgenre_status)
            data['platted_subgenre_count'] = sum(1 for v in subgenre_status.values() if v == 'platted')

        result[ctype] = data

    return result


def provide_badge_progress(profile, settings=None):
    """In-progress badges sorted by completion percentage.

    Only shows the next earnable tier per series: Bronze if unearned,
    Silver only if Bronze is earned, Gold only if Silver is earned, etc.
    """
    from trophies.models import UserBadgeProgress, UserBadge

    settings = settings or {}
    limit = settings.get('limit', 4)

    # Fetch more than needed so we can filter by prerequisite tier in Python
    fetch_limit = limit * 3
    progress_list = list(
        UserBadgeProgress.objects
        .filter(profile=profile, completed_concepts__gt=0)
        .select_related('badge', 'badge__base_badge')
        .filter(badge__required_stages__gt=0, badge__is_live=True)
        .annotate(
            pct=ExpressionWrapper(
                F('completed_concepts') * 100.0 / F('badge__required_stages'),
                output_field=FloatField()
            )
        )
        .exclude(pct__gte=100)
        .order_by('-pct')[:fetch_limit]
    )

    # Pre-fetch earned badge IDs for prerequisite checking (single query)
    earned_badge_ids = set(
        UserBadge.objects.filter(profile=profile).values_list('badge_id', flat=True)
    )

    # Build lookup of badges by (series_slug, tier) for prerequisite resolution
    from trophies.models import Badge
    series_slugs = {bp.badge.series_slug for bp in progress_list}
    badges_by_key = {}
    if series_slugs:
        for b in Badge.objects.filter(series_slug__in=series_slugs, is_live=True).only('id', 'series_slug', 'tier'):
            badges_by_key[(b.series_slug, b.tier)] = b.id

    badges_in_progress = []
    for bp in progress_list:
        badge = bp.badge

        # Only show if prerequisite tier is met:
        # Tier 1 (Bronze) has no prerequisite. Higher tiers require previous tier earned.
        if badge.tier > 1:
            prev_badge_id = badges_by_key.get((badge.series_slug, badge.tier - 1))
            if not prev_badge_id or prev_badge_id not in earned_badge_ids:
                continue

        badges_in_progress.append({
            'layers': badge.get_badge_layers(),
            'series_name': badge.effective_display_series or badge.name,
            'completed': bp.completed_concepts,
            'required': badge.required_stages,
            'pct': round(bp.pct, 1),
            'tier': badge.tier,
            'tier_name': badge.get_tier_display(),
            'series_slug': badge.series_slug,
        })

        if len(badges_in_progress) >= limit:
            break

    # Overall stats from ProfileGamification (reverse OneToOne, may not exist)
    from trophies.models import ProfileGamification
    try:
        gamification = profile.gamification
    except ProfileGamification.DoesNotExist:
        gamification = None

    return {
        'badges_in_progress': badges_in_progress,
        'total_earned': gamification.total_badges_earned if gamification else 0,
        'unique_earned': gamification.unique_badges_earned if gamification else 0,
    }


# ---------------------------------------------------------------------------
# Module Registry
# ---------------------------------------------------------------------------

DASHBOARD_MODULES = [
    {
        'slug': 'trophy_snapshot',
        'name': 'Trophy Snapshot',
        'description': 'Your trophy collection at a glance: platinums, golds, completion rate, and more.',
        'category': 'at_a_glance',
        'template': 'trophies/partials/dashboard/trophy_snapshot.html',
        'provider': provide_trophy_snapshot,
        'requires_premium': False,
        'load_strategy': 'server',
        'default_order': 1,
        'default_settings': {},
        'configurable_settings': [],
        'cache_ttl': 0,
        'default_size': 'medium',
        'allowed_sizes': ['small', 'medium', 'large'],
    },
    {
        'slug': 'recent_platinums',
        'name': 'Recent Platinums',
        'description': 'Your latest platinum conquests with rarity and earn dates.',
        'category': 'at_a_glance',
        'template': 'trophies/partials/dashboard/recent_platinums.html',
        'provider': provide_recent_platinums,
        'requires_premium': False,
        'load_strategy': 'lazy',
        'default_order': 2,
        'default_settings': {'limit': 6},
        'configurable_settings': [
            {'key': 'limit', 'label': 'Items to show', 'type': 'select', 'default': 6,
             'options': [{'value': 3, 'label': '3'}, {'value': 6, 'label': '6'}, {'value': 10, 'label': '10'}]},
        ],
        'cache_ttl': 300,
        'default_size': 'medium',
        'allowed_sizes': ['small', 'medium', 'large'],
    },
    {
        'slug': 'challenge_hub',
        'name': 'Challenge Hub',
        'description': 'Track your A-Z, Calendar, and Genre challenge progress all in one place.',
        'category': 'progress',
        'template': 'trophies/partials/dashboard/challenge_hub.html',
        'provider': provide_challenge_hub,
        'requires_premium': False,
        'load_strategy': 'lazy',
        'default_order': 3,
        'default_settings': {},
        'configurable_settings': [],
        'cache_ttl': 300,
        'default_size': 'large',
        'allowed_sizes': ['medium', 'large'],
    },
    {
        'slug': 'badge_progress',
        'name': 'Badge Progress',
        'description': 'Badges you are closest to earning. Keep pushing, hunter!',
        'category': 'badges',
        'template': 'trophies/partials/dashboard/badge_progress.html',
        'provider': provide_badge_progress,
        'requires_premium': False,
        'load_strategy': 'lazy',
        'default_order': 4,
        'default_settings': {'limit': 4},
        'configurable_settings': [
            {'key': 'limit', 'label': 'Items to show', 'type': 'select', 'default': 4,
             'options': [{'value': 2, 'label': '2'}, {'value': 4, 'label': '4'}, {'value': 6, 'label': '6'}]},
        ],
        'cache_ttl': 600,
        'default_size': 'medium',
        'allowed_sizes': ['small', 'medium', 'large'],
    },
]

# Pre-built lookup dict for O(1) slug access
_MODULE_LOOKUP = {mod['slug']: mod for mod in DASHBOARD_MODULES}


def _validate_registry():
    """Validate module descriptors at import time. Catches config errors early."""
    seen_slugs = set()
    for mod in DASHBOARD_MODULES:
        slug = mod['slug']
        assert slug not in seen_slugs, f"Duplicate dashboard module slug: {slug}"
        seen_slugs.add(slug)

        assert mod.get('default_size') in VALID_SIZES, \
            f"Invalid default_size '{mod.get('default_size')}' for module {slug}"
        assert all(s in VALID_SIZES for s in mod.get('allowed_sizes', [])), \
            f"Invalid allowed_sizes for module {slug}"
        assert mod.get('load_strategy') in VALID_LOAD_STRATEGIES, \
            f"Invalid load_strategy '{mod.get('load_strategy')}' for module {slug}"
        assert callable(mod.get('provider')), \
            f"Provider for module {slug} is not callable"

        # Pre-compute whether provider accepts size/settings parameters
        sig = inspect.signature(mod['provider'])
        mod['_accepts_size'] = 'size' in sig.parameters
        mod['_accepts_settings'] = 'settings' in sig.parameters

_validate_registry()


def get_module_registry():
    """Return the full list of registered module descriptors."""
    return DASHBOARD_MODULES


def get_module_by_slug(slug):
    """Return a single module descriptor by slug, or None."""
    return _MODULE_LOOKUP.get(slug)


def get_valid_slugs():
    """Return set of all registered module slugs."""
    return set(_MODULE_LOOKUP)


# ---------------------------------------------------------------------------
# Size Helpers
# ---------------------------------------------------------------------------

def get_effective_size(module_descriptor, module_settings):
    """
    Return the effective size for a module, considering user overrides.

    Falls back to default_size if the user's chosen size is invalid
    or not in allowed_sizes.
    """
    slug = module_descriptor['slug']
    default = module_descriptor.get('default_size', 'medium')
    allowed = module_descriptor.get('allowed_sizes', list(VALID_SIZES))

    user_settings = module_settings.get(slug, {}) if module_settings else {}
    user_size = user_settings.get('size')

    if user_size and user_size in allowed:
        return user_size
    return default


def get_size_grid_class(size):
    """Return the CSS grid class string for a given size."""
    return SIZE_GRID_CLASSES.get(size, SIZE_GRID_CLASSES['medium'])


def get_effective_settings(module_descriptor, module_settings):
    """
    Return effective settings for a module, merging user overrides with defaults.

    Each setting in configurable_settings has a 'default' value. User overrides
    from module_settings[slug] take precedence when present and valid.
    """
    slug = module_descriptor['slug']
    defaults = module_descriptor.get('default_settings', {})
    user_settings = module_settings.get(slug, {}) if module_settings else {}

    # Start with defaults, overlay user overrides
    effective = {**defaults}
    configurable = module_descriptor.get('configurable_settings', [])

    for setting in configurable:
        key = setting['key']
        if key in user_settings:
            user_val = user_settings[key]
            # Validate select options
            if setting['type'] == 'select':
                valid_values = [opt['value'] for opt in setting.get('options', [])]
                if user_val in valid_values:
                    effective[key] = user_val
            elif setting['type'] == 'toggle':
                if isinstance(user_val, bool):
                    effective[key] = user_val
        elif key not in effective:
            effective[key] = setting['default']

    return effective


def validate_module_size(slug, size):
    """
    Validate that a size is allowed for a given module.
    Returns (is_valid, error_message).
    """
    mod = get_module_by_slug(slug)
    if not mod:
        return False, f'Unknown module: {slug}'

    allowed = mod.get('allowed_sizes', list(VALID_SIZES))
    if size not in allowed:
        return False, f'Size "{size}" not allowed for {slug}. Allowed: {", ".join(allowed)}'

    return True, None


# ---------------------------------------------------------------------------
# Module Ordering & Filtering
# ---------------------------------------------------------------------------

def get_ordered_modules(config, is_premium):
    """
    Build the display-ordered list of modules for a user.

    Applies premium filtering, user ordering, and visibility preferences.
    Each returned module dict is enriched with 'effective_size' and 'grid_class'.
    New modules not yet in the user's saved order are appended at their
    default_order position.
    """
    # Filter out premium modules for free users
    available = [
        mod for mod in DASHBOARD_MODULES
        if not mod['requires_premium'] or is_premium
    ]

    # Filter out hidden modules
    hidden = set(config.hidden_modules) if config.hidden_modules else set()
    visible = [mod for mod in available if mod['slug'] not in hidden]

    # Apply custom ordering (premium only)
    if is_premium and config.module_order:
        mod_lookup = {mod['slug']: mod for mod in visible}
        ordered = []

        for slug in config.module_order:
            if slug in mod_lookup:
                ordered.append(mod_lookup.pop(slug))

        remaining = sorted(mod_lookup.values(), key=lambda m: m['default_order'])
        ordered.extend(remaining)
    else:
        ordered = sorted(visible, key=lambda m: m['default_order'])

    # Enrich each module with effective size and grid class
    module_settings = config.module_settings or {}
    enriched = []
    for mod in ordered:
        enriched_mod = {**mod}
        size = get_effective_size(mod, module_settings)
        enriched_mod['effective_size'] = size
        enriched_mod['grid_class'] = get_size_grid_class(size)
        enriched.append(enriched_mod)

    return enriched


def get_tabs_for_customize(config, is_premium):
    """
    Return all modules grouped by their effective tab assignment for the customize panel.

    Unlike get_dashboard_tabs() which filters hidden modules, this includes ALL modules
    (with is_hidden flag) so users can toggle them back on. Returns an ordered list of
    tab dicts matching the tab bar order.
    """
    tab_config = config.tab_config or {}
    hidden = set(config.hidden_modules) if config.hidden_modules else set()
    module_settings = config.module_settings or {}

    # Build module-to-tab mapping (same logic as get_dashboard_tabs)
    module_tab_map = {mod['slug']: mod['category'] for mod in DASHBOARD_MODULES}
    if is_premium:
        overrides = tab_config.get('module_tab_overrides', {})
        if isinstance(overrides, dict):
            for slug, tab_slug in overrides.items():
                if slug in module_tab_map:
                    module_tab_map[slug] = tab_slug

    # Build tab structures
    tab_order = tab_config.get('tab_order', DEFAULT_TAB_ORDER) if is_premium else DEFAULT_TAB_ORDER
    tabs = {}
    for cat_slug in DEFAULT_TAB_ORDER:
        tabs[cat_slug] = {
            'slug': cat_slug,
            'name': CATEGORY_DISPLAY_NAMES.get(cat_slug, cat_slug.replace('_', ' ').title()),
            'short_name': CATEGORY_SHORT_NAMES.get(cat_slug),
            'icon': TAB_ICONS.get(cat_slug, 'star'),
            'is_custom': False,
            'modules': [],
        }

    # Add custom tabs
    if is_premium:
        custom_tabs = tab_config.get('custom_tabs', {})
        if isinstance(custom_tabs, dict):
            for tab_slug, tab_data in custom_tabs.items():
                if tab_slug in DEFAULT_TAB_ORDER:
                    continue
                if not isinstance(tab_data, dict):
                    logger.warning("Skipping malformed custom tab %s (expected dict, got %s)", tab_slug, type(tab_data).__name__)
                    continue
                tabs[tab_slug] = {
                    'slug': tab_slug,
                    'name': str(tab_data.get('name', 'Custom Tab'))[:20],
                    'short_name': str(tab_data.get('name', 'Custom Tab'))[:20],
                    'icon': tab_data.get('icon', 'star') if tab_data.get('icon') in VALID_TAB_ICONS else 'star',
                    'is_custom': True,
                    'modules': [],
                }

    # Assign ALL modules to tabs (including hidden, for toggle-back)
    for mod in DASHBOARD_MODULES:
        is_locked = mod['requires_premium'] and not is_premium
        size = get_effective_size(mod, module_settings)
        settings = get_effective_settings(mod, module_settings if is_premium else {})
        entry = {
            **mod,
            'is_hidden': mod['slug'] in hidden,
            'is_locked': is_locked,
            'effective_size': size,
            'effective_settings': settings,
        }

        target_tab = module_tab_map.get(mod['slug'], mod['category'])
        if target_tab not in tabs:
            target_tab = mod['category']
        tabs[target_tab]['modules'].append(entry)

    # Order modules within each tab
    if is_premium and config.module_order:
        order_lookup = {slug: i for i, slug in enumerate(config.module_order)}
        for tab in tabs.values():
            tab['modules'].sort(key=lambda m: order_lookup.get(m['slug'], m['default_order']))
    else:
        for tab in tabs.values():
            tab['modules'].sort(key=lambda m: m['default_order'])

    # Build ordered list
    ordered = []
    seen = set()
    for tab_slug in tab_order:
        if tab_slug in tabs and tab_slug not in seen:
            seen.add(tab_slug)
            ordered.append(tabs[tab_slug])
    for tab_slug in DEFAULT_TAB_ORDER:
        if tab_slug not in seen:
            seen.add(tab_slug)
            ordered.append(tabs[tab_slug])
    # Append any custom tabs not in tab_order
    for tab_slug, tab_data in tabs.items():
        if tab_slug not in seen:
            ordered.append(tab_data)

    return ordered


CATEGORY_DISPLAY_NAMES = {
    'at_a_glance': 'At a Glance',
    'progress': 'Progress & Challenges',
    'badges': 'Badges & Achievements',
    'community': 'Community',
    'highlights': 'Highlights & History',
    'premium': 'Premium',
}

# Short names for tab bar (fits without scrolling)
CATEGORY_SHORT_NAMES = {
    'at_a_glance': 'At a Glance',
    'progress': 'Progress',
    'badges': 'Badges',
    'community': 'Community',
    'highlights': 'Highlights',
    'premium': 'Premium',
}

# Default tab order and icons (SVG path data for inline rendering)
DEFAULT_TAB_ORDER = ['premium', 'at_a_glance', 'progress', 'badges', 'community', 'highlights']

TAB_ICONS = {
    'at_a_glance': 'trophy_cup',
    'progress': 'chart',
    'badges': 'medal',
    'community': 'users',
    'highlights': 'star',
    'premium': 'crown',
}

VALID_TAB_ICONS = {'trophy_cup', 'target', 'chart', 'medal', 'users', 'star', 'crown', 'heart'}


def _category_display_name(key):
    return CATEGORY_DISPLAY_NAMES.get(key, key.replace('_', ' ').title())


def get_dashboard_tabs(config, is_premium):
    """
    Build the tab structure for the dashboard.

    Returns an ordered list of tab dicts, each containing its modules.
    Default tabs come from CATEGORY_DISPLAY_NAMES. Premium users can have
    custom tabs and module overrides stored in config.tab_config.
    """
    tab_config = config.tab_config or {}
    active_tab = tab_config.get('active_tab', 'at_a_glance')
    hidden = set(config.hidden_modules) if config.hidden_modules else set()
    module_settings = config.module_settings or {}

    # Build module-to-tab mapping
    # Start with defaults from module descriptors
    module_tab_map = {mod['slug']: mod['category'] for mod in DASHBOARD_MODULES}

    # Build default tabs
    tab_order = tab_config.get('tab_order', DEFAULT_TAB_ORDER) if is_premium else DEFAULT_TAB_ORDER
    tabs = {}
    for cat_slug in DEFAULT_TAB_ORDER:
        tabs[cat_slug] = {
            'slug': cat_slug,
            'name': CATEGORY_DISPLAY_NAMES.get(cat_slug, cat_slug.replace('_', ' ').title()),
            'short_name': CATEGORY_SHORT_NAMES.get(cat_slug),
            'icon': TAB_ICONS.get(cat_slug, 'star'),
            'is_custom': False,
            'modules': [],
        }

    # Add custom tabs (premium only, validated)
    if is_premium:
        custom_tabs = tab_config.get('custom_tabs', {})
        if isinstance(custom_tabs, dict):
            for tab_slug, tab_data in custom_tabs.items():
                # Prevent collision with default tab slugs
                if tab_slug in DEFAULT_TAB_ORDER:
                    continue
                if not isinstance(tab_data, dict):
                    continue
                tabs[tab_slug] = {
                    'slug': tab_slug,
                    'name': str(tab_data.get('name', 'Custom Tab'))[:50],
                    'icon': tab_data.get('icon', 'star') if tab_data.get('icon') in VALID_TAB_ICONS else 'star',
                    'is_custom': True,
                    'modules': [],
                }

    # Apply premium tab overrides (validated: target tab must exist)
    if is_premium:
        overrides = tab_config.get('module_tab_overrides', {})
        if isinstance(overrides, dict):
            for slug, tab_slug in overrides.items():
                if slug in module_tab_map and tab_slug in tabs:
                    module_tab_map[slug] = tab_slug

    # Assign modules to tabs
    for mod in DASHBOARD_MODULES:
        # Skip premium modules for free users
        if mod['requires_premium'] and not is_premium:
            continue

        slug = mod['slug']
        # Skip hidden modules
        if slug in hidden:
            continue

        target_tab = module_tab_map.get(slug, mod['category'])
        if target_tab not in tabs:
            target_tab = mod['category']  # Fallback to default category

        # Enrich module
        size = get_effective_size(mod, module_settings)
        settings = get_effective_settings(mod, module_settings if is_premium else {})
        enriched = {
            **mod,
            'effective_size': size,
            'grid_class': get_size_grid_class(size),
            'effective_settings': settings,
        }

        tabs[target_tab]['modules'].append(enriched)

    # Apply custom module ordering within each tab
    if is_premium and config.module_order:
        order_lookup = {slug: i for i, slug in enumerate(config.module_order)}
        for tab in tabs.values():
            tab['modules'].sort(key=lambda m: order_lookup.get(m['slug'], m['default_order']))
    else:
        for tab in tabs.values():
            tab['modules'].sort(key=lambda m: m['default_order'])

    # Build ordered tab list
    ordered_tabs = []
    seen = set()
    for tab_slug in tab_order:
        if tab_slug in tabs and tab_slug not in seen:
            seen.add(tab_slug)
            ordered_tabs.append(tabs[tab_slug])

    # Append any tabs not in tab_order (new default tabs or custom tabs)
    for tab_slug in DEFAULT_TAB_ORDER:
        if tab_slug not in seen:
            seen.add(tab_slug)
            ordered_tabs.append(tabs[tab_slug])
    for tab_slug in tabs:
        if tab_slug not in seen:
            seen.add(tab_slug)
            ordered_tabs.append(tabs[tab_slug])

    # Set active tab
    active_found = False
    for tab in ordered_tabs:
        tab['is_active'] = (tab['slug'] == active_tab)
        if tab['is_active']:
            active_found = True

    # Fallback: if active tab not found, activate the first one
    if not active_found and ordered_tabs:
        ordered_tabs[0]['is_active'] = True

    return ordered_tabs


# ---------------------------------------------------------------------------
# Data Providers: Server-Rendered Modules
# ---------------------------------------------------------------------------

def get_server_module_data(profile, modules):
    """
    Batch-fetch context dicts for all server-rendered modules.

    Returns {slug: context_dict} for modules with load_strategy == 'server'.
    """
    data = {}
    for mod in modules:
        if mod['load_strategy'] != 'server':
            continue
        provider_fn = mod['provider']
        try:
            data[mod['slug']] = provider_fn(profile)
        except Exception:
            logger.exception("Dashboard provider for %s failed for profile %s",
                             mod['slug'], profile.id)
            data[mod['slug']] = {'error': True}
    return data


def get_lazy_module_data(profile, slug, size=None, module_settings=None):
    """
    Fetch context for a single lazy-loaded module.

    Checks Django cache first; falls back to provider on miss.
    Settings and size are passed to providers that accept them.
    Cache keys include a settings hash so different configs are cached independently.
    Returns the context dict or None if the module doesn't exist.
    """
    mod = get_module_by_slug(slug)
    if not mod:
        return None

    effective_size = size or mod.get('default_size', 'medium')
    effective = get_effective_settings(mod, module_settings or {})

    # Build cache key that includes settings so different configs cache separately
    import hashlib
    settings_hash = hashlib.md5(str(sorted(effective.items())).encode()).hexdigest()[:8]
    cache_key = _module_cache_key(slug, profile.id, settings_hash)
    ttl = mod.get('cache_ttl', DEFAULT_CACHE_TTL)

    if ttl > 0:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    provider_fn = mod['provider']
    try:
        if mod.get('_accepts_settings'):
            data = provider_fn(profile, settings=effective)
        elif mod.get('_accepts_size'):
            data = provider_fn(profile, size=effective_size)
        else:
            data = provider_fn(profile)
    except Exception:
        logger.exception("Dashboard provider for %s failed for profile %s",
                         slug, profile.id)
        return {'error': True}

    if ttl > 0:
        try:
            cache.set(cache_key, data, ttl)
        except Exception:
            logger.debug("Could not cache dashboard module %s (non-serializable data)", slug)

    return data


# ---------------------------------------------------------------------------
# Cache Helpers
# ---------------------------------------------------------------------------

def _module_cache_key(slug, profile_id, size=None):
    if size:
        return f"dashboard:mod:{slug}:{profile_id}:{size}"
    return f"dashboard:mod:{slug}:{profile_id}"


def invalidate_dashboard_cache(profile_id):
    """Delete all dashboard module cache keys for a profile (all sizes)."""
    keys_to_delete = []
    for mod in DASHBOARD_MODULES:
        if mod.get('cache_ttl', DEFAULT_CACHE_TTL) > 0:
            slug = mod['slug']
            for size in mod.get('allowed_sizes', list(VALID_SIZES)):
                keys_to_delete.append(_module_cache_key(slug, profile_id, size))
    if keys_to_delete:
        cache.delete_many(keys_to_delete)
