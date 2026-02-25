"""
Region detection utilities for PSN API responses.

Extracts region codes from PSN title detail responses using the same
approach as the andshrew/PlayStation-Titles dataset: parsing the first
2 characters of the product/content ID.
"""
from trophies.util_modules.constants import (
    NA_REGION_CODES, EU_REGION_CODES, JP_REGION_CODES,
    AS_REGION_CODES, KR_REGION_CODES, CN_REGION_CODES,
)

# All known 2-char region prefixes from PSN product/content IDs
ALL_REGION_PREFIXES = frozenset(
    NA_REGION_CODES + EU_REGION_CODES + JP_REGION_CODES +
    AS_REGION_CODES + KR_REGION_CODES + CN_REGION_CODES
)


def _extract_prefix_from_product_id(product_id: str) -> str | None:
    """
    Extract the 2-char region prefix from a PSN product/content ID.

    Format: "UP9000-PPSA28997_00-SONSOFSPARTAPS50" -> "UP"

    Returns the prefix if it matches a known region code, else None.
    """
    if product_id and len(product_id) >= 2:
        prefix = product_id[:2].upper()
        if prefix in ALL_REGION_PREFIXES:
            return prefix
    return None


def detect_region_from_details(details: dict) -> str | None:
    """
    Detect region from a PSN API title detail response.

    Uses product ID prefixes (same approach as the andshrew/PlayStation-Titles
    TSV contentId field). Does NOT use contentRating.authority since we always
    query with Country: US, making that signal always return ESRB/NA.

    Args:
        details: The details dict from game_title.get_details()[0]

    Returns:
        A 2-char region code (e.g., 'UP', 'EP', 'JP') compatible with
        Game.add_region(), or None if region cannot be determined.
    """
    # Signal 1: defaultProduct.id prefix (primary)
    default_product = details.get('defaultProduct')
    if default_product:
        product_id = default_product.get('id', '')
        prefix = _extract_prefix_from_product_id(product_id)
        if prefix:
            return prefix

    # Signal 2: categorizedProducts IDs (fallback)
    categorized = details.get('categorizedProducts') or []
    for category in categorized:
        for pid in category.get('ids', []):
            prefix = _extract_prefix_from_product_id(pid)
            if prefix:
                return prefix

    return None
