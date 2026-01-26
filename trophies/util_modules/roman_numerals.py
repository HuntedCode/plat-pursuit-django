"""
Roman numeral utilities for search query expansion.

Provides functions to convert between Roman numerals (I, II, XV) and
Arabic numerals (1, 2, 15) to enhance game title searches.
"""
import re

# Mapping of Roman numerals to Arabic numbers (covers typical game title range)
ROMAN_TO_ARABIC = {
    'I': 1, 'II': 2, 'III': 3, 'IV': 4, 'V': 5,
    'VI': 6, 'VII': 7, 'VIII': 8, 'IX': 9, 'X': 10,
    'XI': 11, 'XII': 12, 'XIII': 13, 'XIV': 14, 'XV': 15,
    'XVI': 16, 'XVII': 17, 'XVIII': 18, 'XIX': 19, 'XX': 20
}

ARABIC_TO_ROMAN = {v: k for k, v in ROMAN_TO_ARABIC.items()}

# Pattern to match standalone Roman numerals (word boundaries)
# Must be uppercase in the pattern, we handle case-insensitivity separately
ROMAN_PATTERN = re.compile(
    r'\b(XX|XIX|XVIII|XVII|XVI|XV|XIV|XIII|XII|XI|X|IX|VIII|VII|VI|V|IV|III|II|I)\b',
    re.IGNORECASE
)

# Pattern to match standalone Arabic numbers 1-20
ARABIC_PATTERN = re.compile(r'\b([1-9]|1[0-9]|20)\b')


def expand_numeral_query(query: str) -> list[str]:
    """
    Expand a search query to include numeral variations.

    Generates alternate versions of the query with Roman numerals converted
    to Arabic and vice versa, enabling searches like "Final Fantasy 15"
    to match "Final Fantasy XV".

    Args:
        query: The original search query string

    Returns:
        List of query variations (always includes original query first)

    Examples:
        >>> expand_numeral_query("Final Fantasy 15")
        ['Final Fantasy 15', 'Final Fantasy XV']
        >>> expand_numeral_query("Final Fantasy XV")
        ['Final Fantasy XV', 'Final Fantasy 15']
        >>> expand_numeral_query("Dark Souls")
        ['Dark Souls']
    """
    variations = [query]

    # Try converting Roman numerals to Arabic
    roman_match = ROMAN_PATTERN.search(query)
    if roman_match:
        roman = roman_match.group(1).upper()
        if roman in ROMAN_TO_ARABIC:
            arabic = str(ROMAN_TO_ARABIC[roman])
            converted = ROMAN_PATTERN.sub(arabic, query, count=1)
            if converted not in variations:
                variations.append(converted)

    # Try converting Arabic numerals to Roman
    arabic_match = ARABIC_PATTERN.search(query)
    if arabic_match:
        arabic = int(arabic_match.group(1))
        if arabic in ARABIC_TO_ROMAN:
            roman = ARABIC_TO_ROMAN[arabic]
            converted = ARABIC_PATTERN.sub(roman, query, count=1)
            if converted not in variations:
                variations.append(converted)

    return variations
