"""
Language detection utilities - Helpers for detecting languages in game titles.

Provides functions for detecting Asian languages (Chinese, Japanese, Korean)
and fuzzy matching of game names.
"""
import difflib
from scipy import stats


def match_names(name1, name2, threshold=0.9):
    """
    Fuzzy match two game names with normalization.

    This function normalizes game names by:
    - Converting to lowercase
    - Removing trademark symbols (™, ®)
    - Trimming whitespace

    Then compares them using sequence matching.

    Args:
        name1: First game name
        name2: Second game name
        threshold: Minimum similarity ratio (0.0-1.0) to consider a match (default: 0.9)

    Returns:
        bool: True if names match above threshold, False otherwise
    """
    name1 = name1.lower().replace('™', '').replace('®', '').strip()
    name2 = name2.lower().replace('™', '').replace('®', '').strip()
    ratio = difflib.SequenceMatcher(None, name1, name2).ratio()
    return ratio >= threshold


def count_unique_game_groups(games_qs) -> int:
    """
    Count unique game groups using union-find algorithm.

    Groups games that share any title IDs together. This is useful for
    identifying regional variants of the same game.

    Args:
        games_qs: QuerySet or list of Game objects with title_ids attribute

    Returns:
        int: Number of unique game groups
    """
    from typing import List, Set

    games = list(games_qs)
    n = len(games)
    if n == 0:
        return 0

    parent = list(range(n))
    rank = [0] * n

    def find(x: int) -> int:
        if parent[x] != x:
            parent[x] = find(parent[x])
        return parent[x]

    def union(x: int, y: int):
        px, py = find(x), find(y)
        if px == py:
            return
        if rank[px] < rank[py]:
            parent[px] = py
        elif rank[py] < rank[px]:
            parent[py] = px
        else:
            parent[py] = px
            rank[px] += 1

    title_id_sets: List[Set[str]] = [set(game.title_ids) for game in games]
    for i in range(n):
        for j in range(i + 1, n):
            if title_id_sets[i] & title_id_sets[j]:
                union(i, j)

    unique_groups = len(set(find(i) for i in range(n)))
    return unique_groups


def calculate_trimmed_mean(data, trim_percent=0.1):
    """
    Calculate trimmed mean to handle outliers.

    Removes the specified percentage of extreme values from both ends
    before calculating the mean. Useful for rating calculations.

    Args:
        data: List of numeric values
        trim_percent: Percentage to trim from each end (default: 0.1 = 10%)

    Returns:
        float: Trimmed mean value, or None if data is empty
    """
    if not data:
        return None
    return stats.trim_mean(data, trim_percent)


def detect_asian_language(title: str) -> str:
    """
    Detect the primary Asian language in a game title.

    Analyzes character sets to determine if a title is primarily in:
    - Chinese (Han characters without Japanese-specific characters)
    - Japanese (Hiragana or Katakana present)
    - Korean (Hangul characters)

    Args:
        title: Game title string to analyze

    Returns:
        str: Detected language code ('CN', 'JP', 'KR') or 'Unknown'
    """
    def count_chinese(text):
        """Count Han (Chinese) characters."""
        return sum(1 for c in text if '\u4e00' <= c <= '\u9fff')

    def count_japanese_unique(text):
        """Count Japanese-specific characters (Hiragana + Katakana)."""
        hiragana = sum(1 for c in text if '\u3040' <= c <= '\u309f')
        katakana = sum(1 for c in text if '\u30a0' <= c <= '\u30ff')
        return hiragana + katakana

    def count_korean(text):
        """Count Korean Hangul characters."""
        return sum(1 for c in text if '\uac00' <= c <= '\ud7af')

    japanese_unique = count_japanese_unique(title)
    korean = count_korean(title)
    # Subtract Japanese characters from Chinese count (they share Han)
    chinese = count_chinese(title) - japanese_unique

    max_count = max(chinese, japanese_unique, korean)
    if max_count == 0:
        return 'Unknown'
    elif japanese_unique == max_count:
        return 'JP'
    elif korean == max_count:
        return 'KR'
    elif chinese == max_count:
        return 'CN'
