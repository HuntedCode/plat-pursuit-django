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


# --- Pursuer rank: the account-wide standing on the SUM of element levels ---------
# A long, military-flavored ladder (named tier + Roman-numeral division, V -> I) that the
# single Pursuer Level climbs. There is ONE Pursuer per account, so unlike the per-element
# tiers this ladder is deliberately deep -- a fresh thing to reach toward for the long haul.
# NEWBIE is the divisionless floor (a brand-new account, every element at level 1); the 9
# middle tiers each split into 5 divisions (V at entry climbing to I at the top, the gamer
# convention); ASCENDANT is the divisionless, open-ended apex -- past it the raw Pursuer
# Level number is the flex (same cap-less spirit as the elements' Legend). Thresholds are
# config placeholders (tune on real adoption data); the names are locked. See xp-economy.md.
PURSUER_DIVISIONS = 5
_PURSUER_NUMERALS = ['V', 'IV', 'III', 'II', 'I']  # index 0 = entry (V) ... index 4 = top (I)

# (min_level, key, name, has_divisions), ascending by min_level.
PURSUER_RANKS = [
    (0,    'newbie',     'Newbie',     False),
    (40,   'recruit',    'Recruit',    True),
    (120,  'seeker',     'Seeker',     True),
    (250,  'hunter',     'Hunter',     True),
    (450,  'ranger',     'Ranger',     True),
    (750,  'warden',     'Warden',     True),
    (1150, 'marshal',    'Marshal',    True),
    (1700, 'vanquisher', 'Vanquisher', True),
    (2500, 'paragon',    'Paragon',    True),
    (3600, 'luminary',   'Luminary',   True),
    (5200, 'ascendant',  'Ascendant',  False),
]


def pursuer_rank_for_level(level: int) -> dict:
    """The account-wide Pursuer rank for a Pursuer Level (the sum of every element level).

    Returns {key, name, division, division_roman, label, min_level, next_level, next_label,
    levels_to_next}. `division` is 1-5 (1 = top of the tier, the 'I') for the divisioned
    middle tiers, or None for Newbie / Ascendant. `next_level` is the Pursuer Level at which
    the next division or tier begins (None at Ascendant, the open-ended top); `label` is the
    display string ("Warden III", or just "Newbie" / "Ascendant")."""
    idx = 0
    for i, rank in enumerate(PURSUER_RANKS):
        if level >= rank[0]:
            idx = i
        else:
            break
    min_level, key, name, has_div = PURSUER_RANKS[idx]
    next_tier_floor = PURSUER_RANKS[idx + 1][0] if idx + 1 < len(PURSUER_RANKS) else None

    if not has_div:
        # Newbie (the floor) or Ascendant (the open-ended apex): a single divisionless band.
        next_label = PURSUER_RANKS[idx + 1][2] if next_tier_floor is not None else None
        return {
            'key': key, 'name': name, 'division': None, 'division_roman': '', 'label': name,
            'min_level': min_level, 'next_level': next_tier_floor, 'next_label': next_label,
            'levels_to_next': (next_tier_floor - level) if next_tier_floor is not None else 0,
        }

    # Divisioned tier: split [min_level, next_tier_floor) into PURSUER_DIVISIONS equal bands;
    # band 0 is entry (numeral V), band 4 is the top (numeral I).
    step = (next_tier_floor - min_level) / PURSUER_DIVISIONS
    band = min(PURSUER_DIVISIONS - 1, int((level - min_level) / step))
    next_boundary = round(min_level + (band + 1) * step)
    if band + 1 < PURSUER_DIVISIONS:
        next_label = f'{name} {_PURSUER_NUMERALS[band + 1]}'   # next division up
    else:
        next_label = PURSUER_RANKS[idx + 1][2]                  # promote to the next tier
    return {
        'key': key, 'name': name,
        'division': PURSUER_DIVISIONS - band,                  # 5 (V) .. 1 (I)
        'division_roman': _PURSUER_NUMERALS[band],
        'label': f'{name} {_PURSUER_NUMERALS[band]}',
        'min_level': min_level, 'next_level': next_boundary, 'next_label': next_label,
        'levels_to_next': max(0, next_boundary - level),
    }
