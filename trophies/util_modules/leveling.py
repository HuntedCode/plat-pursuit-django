"""Per-job (element) leveling curve for Contract XP.

The cumulative XP required to reach level L is BASE * L*(L+1)/2 (a rising per-level
cost: level n costs BASE*n more than level n-1). Starter curve -- calibrate on real
data. Pursuer Level = sum of all of a profile's per-job levels. See
docs/design/rebuild/job-board-contracts.md and the plan cheerful-snacking-wozniak.md.
"""
import math

from trophies.util_modules.constants import JOB_LEVEL_BASE, JOB_LEVEL_CAP


def xp_for_level(level: int) -> int:
    """Cumulative XP required to be AT `level` (level 0 = 0 XP). Capped at JOB_LEVEL_CAP."""
    if level <= 0:
        return 0
    level = min(level, JOB_LEVEL_CAP)
    return JOB_LEVEL_BASE * level * (level + 1) // 2


def level_for_xp(total_xp: int) -> int:
    """Highest level reached for a cumulative XP total (capped at JOB_LEVEL_CAP)."""
    if total_xp <= 0:
        return 0
    # Closed-form estimate (solve BASE * L(L+1)/2 <= total_xp), then correct any float drift.
    level = int((-1 + math.sqrt(1 + 8 * total_xp / JOB_LEVEL_BASE)) / 2)
    level = min(max(level, 0), JOB_LEVEL_CAP)
    # Guard the cap: above the cap, xp_for_level clamps, so this loop must stop at the cap.
    while level < JOB_LEVEL_CAP and xp_for_level(level + 1) <= total_xp:
        level += 1
    while level > 0 and xp_for_level(level) > total_xp:
        level -= 1
    return level
