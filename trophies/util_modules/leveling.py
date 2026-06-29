"""Per-job (element) leveling + prestige tiers for Contract XP.

FLAT, CAP-LESS curve: every level costs the same `JOB_XP_PER_LEVEL`, and there is no cap --
the level number climbs forever (the open-ended endgame). 1-based: every job starts at
level 1 with 0 XP (the floor, so a fresh Pursuer already "is" all 24 jobs rather than a
wall of locked ones). Pursuer Level = sum of all of a profile's per-job levels.

Why flat: XP is fully fungible, so any modifier behaves consistently at every level -- a
double-XP event always doubles levels gained, a quest's +N XP is always worth the same
number of levels. An escalating curve would quietly make bonuses worth less at high level.

PRESTIGE TIERS carry the milestone journey on top of the flat number: a bounded, NAMED
ladder (Initiate -> ... -> Master at 99 -> Legend), and past the top tier the raw level
number is the infinite flex. Thresholds are config -- tune freely; names are placeholders.
See docs/design/rebuild/xp-economy.md.
"""
from trophies.util_modules.constants import JOB_XP_PER_LEVEL


def xp_for_level(level: int) -> int:
    """Cumulative XP required to BE at `level` (1-based; level 1 = 0 XP). Flat: each level
    above 1 costs another `JOB_XP_PER_LEVEL`."""
    if level <= 1:
        return 0
    return JOB_XP_PER_LEVEL * (level - 1)


def level_for_xp(total_xp: int) -> int:
    """Level reached for a cumulative XP total. Always >= 1 (the floor); uncapped."""
    if total_xp <= 0:
        return 1
    return total_xp // JOB_XP_PER_LEVEL + 1


# --- Prestige tiers ---------------------------------------------------------
# (min_level, key, name), ascending. Bounded, named milestone ladder; the level number
# keeps climbing past the top tier (Legend) -- that's the cap-less endgame, so we never
# need to invent endless tier names. Thresholds are config; names are placeholders.
JOB_TIERS = [
    (1,   'initiate',    'Initiate'),
    (10,  'apprentice',  'Apprentice'),
    (25,  'adept',       'Adept'),
    (50,  'expert',      'Expert'),
    (75,  'veteran',     'Veteran'),
    (99,  'master',      'Master'),
    (150, 'grandmaster', 'Grandmaster'),
    (250, 'legend',      'Legend'),
]


def tier_for_level(level: int) -> dict:
    """The prestige tier for a job level. Returns {key, name, min_level, next_level} where
    next_level is the level the NEXT tier unlocks at, or None at the top (Legend is
    open-ended -- the level number carries on past it)."""
    chosen = JOB_TIERS[0]
    next_level = None
    for i, tier in enumerate(JOB_TIERS):
        if level >= tier[0]:
            chosen = tier
            next_level = JOB_TIERS[i + 1][0] if i + 1 < len(JOB_TIERS) else None
        else:
            break
    return {'key': chosen[1], 'name': chosen[2], 'min_level': chosen[0], 'next_level': next_level}
