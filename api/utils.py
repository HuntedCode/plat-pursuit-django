"""
Shared utilities for API views.
"""


def safe_int(value, default=0):
    """Safely convert a query parameter to int, returning default on failure."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def safe_bool(value, default=False):
    """Parse a query-parameter truthy string ('1', 'true', 'on', 'yes')."""
    if value is None:
        return default
    return str(value).strip().lower() in ('1', 'true', 'on', 'yes')
