"""
Roman numeral utilities for search query expansion and Unicode normalization.

Provides functions to convert between Roman numerals (I, II, XV) and
Arabic numerals (1, 2, 15) to enhance game title searches, plus
normalization of Unicode Roman numeral codepoints to ASCII equivalents.
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


# Unicode Roman numeral codepoints -> ASCII equivalents
# U+2160-U+216B (uppercase I-XII) and U+2170-U+217B (lowercase i-xii)
_UNICODE_ROMAN_MAP = {
    '\u2160': 'I',    '\u2161': 'II',   '\u2162': 'III',  '\u2163': 'IV',
    '\u2164': 'V',    '\u2165': 'VI',   '\u2166': 'VII',  '\u2167': 'VIII',
    '\u2168': 'IX',   '\u2169': 'X',    '\u216A': 'XI',   '\u216B': 'XII',
    '\u2170': 'i',    '\u2171': 'ii',   '\u2172': 'iii',  '\u2173': 'iv',
    '\u2174': 'v',    '\u2175': 'vi',   '\u2176': 'vii',  '\u2177': 'viii',
    '\u2178': 'ix',   '\u2179': 'x',    '\u217A': 'xi',   '\u217B': 'xii',
}

_UNICODE_ROMAN_RE = re.compile(
    '[' + ''.join(_UNICODE_ROMAN_MAP.keys()) + ']'
)


def normalize_unicode_roman_numerals(text: str) -> str:
    """Replace Unicode Roman numeral characters with ASCII equivalents.

    PSN game titles sometimes use single-codepoint Unicode Roman numerals
    (e.g., U+2162 for III) instead of regular ASCII letters, making games
    unsearchable when users type normal characters.

    Args:
        text: The string to normalize.

    Returns:
        The string with Unicode Roman numerals replaced by ASCII equivalents.
    """
    if not text:
        return text
    return _UNICODE_ROMAN_RE.sub(lambda m: _UNICODE_ROMAN_MAP[m.group()], text)
