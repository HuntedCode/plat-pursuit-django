"""Rarity: one vocabulary, one grading function, for anything in the product that can be rare.

Rarity started life inside the badge system and stayed there, so five surfaces ended up hand-rolling
their own presentation off two shared tokens. This module is the shared half: the classes, the
thresholds, the grading function and the display metadata. `badge_rarity` keeps only what is genuinely
badge-specific (the queryset annotation), and every other consumer reads from here.

**Rarity is community-level and absolute.** It describes the THING, not your relationship to it -- a
Mythic title is Mythic whether or not you hold it, and it reads the same for every hunter. That is why
nothing here takes a profile.

**The denominator is the eligible population, not the whole userbase.** For badges that is the series'
pursuers (profiles with real progress); for anything else it is whoever could plausibly have earned it.
Against the whole userbase almost everything reads Mythic, which makes the scale useless.

**Grading is derived, never stored.** The thresholds below are fixed; the population underneath them is
not. So a grade CAN change as a community grows into something -- a title earned by 3% of pursuers at
launch may read Uncommon a year later. That is intended: the grade describes the thing as it stands
today, and a fixed scale over a live population is the whole model. Don't "fix" it by freezing grades.
"""

#: Percentage of the eligible population that earned it -> class. First ceiling under wins; else common.
#: Tuned for an eligible-population denominator, NOT the whole userbase.
RARITY_THRESHOLDS = ((5.0, 'mythic'), (15.0, 'rare'), (35.0, 'uncommon'))

#: Rarest first. `unearned` is deliberately NOT in here -- 0 earners is unearned, not an achievement,
#: so it must never wear a prestige grade. It is handled as its own state by callers.
RARITY_CLASSES = ('mythic', 'rare', 'uncommon', 'common')

#: The "nobody has this yet" state. A filter value and a display state, never a grade.
RARITY_UNEARNED = 'unearned'

#: Display metadata, so a surface never re-decides what a grade is called or which glyph it wears.
#: The icon ids are symbols in `components/_frame_rarity_sprite.html`, auto-mounted in base.html;
#: common has none on purpose -- the baseline grade should not announce itself.
RARITY_LABELS = {
    'common': 'Common', 'uncommon': 'Uncommon', 'rare': 'Rare', 'mythic': 'Mythic',
    RARITY_UNEARNED: 'Be the first',
}
RARITY_ICONS = {'uncommon': 'rarity-dot', 'rare': 'rarity-diamond', 'mythic': 'rarity-sparkle'}

#: Chip order for filter UIs: rarest earned first, then the not-yet-earned nudge.
RARITY_FILTER_CHOICES = [(c, RARITY_LABELS[c]) for c in RARITY_CLASSES] + [
    (RARITY_UNEARNED, RARITY_LABELS[RARITY_UNEARNED]),
]


def rarity_class_for(pct: float) -> str:
    """Bucket an earned-percentage; lower is rarer."""
    for ceiling, name in RARITY_THRESHOLDS:
        if pct < ceiling:
            return name
    return 'common'


def rarity_for(earned_count: int, eligible_count: int):
    """(pct, class) for anything gradeable.

      - No eligible population yet  -> (None, '')  -> callers render "rarity pending".
      - Population exists, 0 earners -> (0.0, '')  -> an honest 0%, but NO class: unearned is not
        an achievement and must not wear the prestige grade.
      - Otherwise the real percentage and its bucket.

    Both are LIVE. The thresholds are fixed, the population moves, so a grade genuinely can change over
    time -- see the note on drift in the module docstring.
    """
    if not eligible_count:
        return None, ''
    pct = round(min(100.0, 100.0 * earned_count / eligible_count), 1)
    if not earned_count:
        return pct, ''
    return pct, rarity_class_for(pct)
