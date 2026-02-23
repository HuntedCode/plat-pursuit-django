"""
Dashboard module registry and data provider service.

The dashboard uses a Module Registry pattern where each module is a self-contained
triple: a Python data provider function, an HTML partial template, and optional JS.

Adding a new module:
    1. Write the provider function (in this file or import from a dedicated module)
    2. Add a descriptor dict to DASHBOARD_MODULES referencing the provider callable
    3. Create the partial template in templates/trophies/partials/dashboard/
"""
import logging
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

# Maximum number of modules free users can hide
MAX_FREE_HIDDEN = 3

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


# ---------------------------------------------------------------------------
# Placeholder Providers (Framework Validation)
# ---------------------------------------------------------------------------

def get_placeholder_server_data(profile):
    """Test provider for server-rendered module. Zero queries."""
    return {
        'message': 'Server module loaded successfully.',
        'timestamp': timezone.now().isoformat(),
        'profile_name': profile.display_psn_username or profile.psn_username,
    }


def get_placeholder_lazy_data(profile):
    """Test provider for lazy-loaded module. Zero queries."""
    return {
        'message': 'Lazy module loaded via AJAX.',
        'timestamp': timezone.now().isoformat(),
        'profile_name': profile.display_psn_username or profile.psn_username,
    }


def get_placeholder_premium_data(profile):
    """Test provider for premium-gated module. Zero queries."""
    return {
        'message': 'Premium module loaded successfully.',
        'timestamp': timezone.now().isoformat(),
        'profile_name': profile.display_psn_username or profile.psn_username,
    }


# ---------------------------------------------------------------------------
# Module Registry
# ---------------------------------------------------------------------------

DASHBOARD_MODULES = [
    {
        'slug': 'placeholder_server',
        'name': 'Server Module (Test)',
        'description': 'Framework test: validates server-side rendering pipeline.',
        'category': 'at_a_glance',
        'template': 'trophies/partials/dashboard/placeholder_server.html',
        'provider': get_placeholder_server_data,
        'requires_premium': False,
        'load_strategy': 'server',
        'default_order': 1,
        'default_settings': {},
        'cache_ttl': 0,
        'default_size': 'small',
        'allowed_sizes': ['small', 'medium', 'large'],
    },
    {
        'slug': 'placeholder_lazy',
        'name': 'Lazy Module (Test)',
        'description': 'Framework test: validates AJAX lazy-loading pipeline.',
        'category': 'at_a_glance',
        'template': 'trophies/partials/dashboard/placeholder_lazy.html',
        'provider': get_placeholder_lazy_data,
        'requires_premium': False,
        'load_strategy': 'lazy',
        'default_order': 2,
        'default_settings': {},
        'cache_ttl': 60,
        'default_size': 'medium',
        'allowed_sizes': ['small', 'medium', 'large'],
    },
    {
        'slug': 'placeholder_premium',
        'name': 'Premium Module (Test)',
        'description': 'Framework test: validates premium gating.',
        'category': 'at_a_glance',
        'template': 'trophies/partials/dashboard/placeholder_lazy.html',
        'provider': get_placeholder_premium_data,
        'requires_premium': True,
        'load_strategy': 'lazy',
        'default_order': 3,
        'default_settings': {},
        'cache_ttl': 60,
        'default_size': 'large',
        'allowed_sizes': ['medium', 'large'],
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


def get_all_modules_for_customize(config, is_premium):
    """
    Return all modules (including hidden) grouped by category for the customize panel.

    Each module dict gets extra 'is_hidden', 'is_locked', and 'effective_size' keys.
    """
    hidden = set(config.hidden_modules) if config.hidden_modules else set()
    module_settings = config.module_settings or {}
    categories = {}

    for mod in DASHBOARD_MODULES:
        is_locked = mod['requires_premium'] and not is_premium
        size = get_effective_size(mod, module_settings)
        entry = {
            **mod,
            'is_hidden': mod['slug'] in hidden,
            'is_locked': is_locked,
            'effective_size': size,
        }
        cat = mod['category']
        if cat not in categories:
            categories[cat] = {
                'name': _category_display_name(cat),
                'modules': [],
            }
        categories[cat]['modules'].append(entry)

    return categories


CATEGORY_DISPLAY_NAMES = {
    'at_a_glance': 'At a Glance',
    'progress': 'Progress & Goals',
    'highlights': 'Highlights & Achievements',
    'community': 'Community',
    'historical': 'Historical',
    'quick_links': 'Quick Links',
}


def _category_display_name(key):
    return CATEGORY_DISPLAY_NAMES.get(key, key.replace('_', ' ').title())


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


def get_lazy_module_data(profile, slug):
    """
    Fetch context for a single lazy-loaded module.

    Checks Django cache first; falls back to provider on miss.
    Returns the context dict or None if the module doesn't exist.
    """
    mod = get_module_by_slug(slug)
    if not mod:
        return None

    cache_key = _module_cache_key(slug, profile.id)
    ttl = mod.get('cache_ttl', DEFAULT_CACHE_TTL)

    if ttl > 0:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    provider_fn = mod['provider']
    try:
        data = provider_fn(profile)
    except Exception:
        logger.exception("Dashboard provider for %s failed for profile %s",
                         slug, profile.id)
        return {'error': True}

    if ttl > 0:
        cache.set(cache_key, data, ttl)

    return data


# ---------------------------------------------------------------------------
# Cache Helpers
# ---------------------------------------------------------------------------

def _module_cache_key(slug, profile_id):
    return f"dashboard:mod:{slug}:{profile_id}"


def invalidate_dashboard_cache(profile_id):
    """Delete all dashboard module cache keys for a profile."""
    keys_to_delete = []
    for mod in DASHBOARD_MODULES:
        if mod.get('cache_ttl', DEFAULT_CACHE_TTL) > 0:
            keys_to_delete.append(_module_cache_key(mod['slug'], profile_id))
    if keys_to_delete:
        cache.delete_many(keys_to_delete)
