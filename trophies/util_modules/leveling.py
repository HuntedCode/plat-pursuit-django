"""Per-job (element) leveling curve for Contract XP.

The curve is **1-based**: every job starts at level 1 with 0 XP (the RuneScape-style
floor, so a fresh Pursuer already "is" all 24 jobs rather than a wall of locked ones).
Cumulative XP to reach level L (L>=1) is BASE * (L-1)*L/2 -- a rising per-level cost
(reaching level n costs BASE*(n-1) more than reaching n-1). Starter curve, calibrate on
real data. Pursuer Level = sum of all of a profile's per-job levels (min 1 each). See
docs/design/rebuild/job-board-contracts.md and the plan cheerful-snacking-wozniak.md.
"""
import math

from trophies.util_modules.constants import JOB_LEVEL_BASE, JOB_LEVEL_CAP


def xp_for_level(level: int) -> int:
    """Cumulative XP required to BE at `level`. Level 1 = 0 XP (the floor); each higher
    level costs BASE more per step. Capped at JOB_LEVEL_CAP."""
    if level <= 1:
        return 0
    level = min(level, JOB_LEVEL_CAP)
    return JOB_LEVEL_BASE * (level - 1) * level // 2


def level_for_xp(total_xp: int) -> int:
    """Level reached for a cumulative XP total. Always >= 1 (the level-1 floor); capped
    at JOB_LEVEL_CAP."""
    if total_xp <= 0:
        return 1
    # Closed-form estimate (solve BASE * (L-1)L/2 <= total_xp), then correct any float drift.
    level = int((1 + math.sqrt(1 + 8 * total_xp / JOB_LEVEL_BASE)) / 2)
    level = min(max(level, 1), JOB_LEVEL_CAP)
    # Guard the cap: above the cap, xp_for_level clamps, so this loop must stop at the cap.
    while level < JOB_LEVEL_CAP and xp_for_level(level + 1) <= total_xp:
        level += 1
    while level > 1 and xp_for_level(level) > total_xp:
        level -= 1
    return level
