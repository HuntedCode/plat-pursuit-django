"""
Shared utilities for API views.
"""


def safe_int(value, default=0):
    """Safely convert a query parameter to int, returning default on failure."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
