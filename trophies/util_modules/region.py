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

# Content rating authority to region code mapping (weakest signal)
CONTENT_RATING_AUTHORITY_TO_REGION = {
    'ESRB': 'US',       # North America
    'PEGI': 'EP',       # Europe
    'CERO': 'JP',       # Japan
    'GRAC': 'KR',       # Korea
    'GRB': 'KR',        # Korea (alternate name)
    'GSRR': 'HP',       # Asia (Taiwan)
    'CSRR': 'HP',       # Asia (Taiwan, alternate name)
    'USK': 'EP',        # Europe (Germany)
    'ACB': 'EP',        # Europe (Australia/NZ share EU stack)
    'OFLC': 'EP',       # Europe (Australia/NZ, older name)
    'DJCTQ': 'US',      # North America (Brazil uses NA stack)
    'RARS': 'EP',       # Europe (Russia)
}


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

    Uses a cascade of signals ordered by reliability:
    1. defaultProduct.id prefix (most reliable, same as TSV contentId)
    2. categorizedProducts IDs (fallback if no defaultProduct)
    3. contentRating.authority (weakest, affected by Country header)

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

    # Signal 3: contentRating.authority (weakest)
    authority = details.get('contentRating', {}).get('authority', '').upper()
    region_code = CONTENT_RATING_AUTHORITY_TO_REGION.get(authority)
    if region_code:
        return region_code

    return None
