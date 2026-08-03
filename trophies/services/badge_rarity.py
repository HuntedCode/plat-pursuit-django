"""Badge rarity: derived LIVE, no cron, no stored fields (see docs/design/rebuild/badge-backend-rebuild.md).

Rarity is a pure function of two live values: the group badge's maintained `earned_count` denorm (owned by
badge_apply) and the series' PURSUER base -- the count of SeriesBadgeStanding rows for the series, which
recompute_standing keeps to profiles with real progress (zero-progress rows are deleted). So rarity is
"of everyone making progress on this series, what fraction earned this group's badge" -- a meaningful
completion-rarity, unlike a whole-userbase ratio where almost every badge would read Mythic.

This matches the sealed system's live-read philosophy (badge_leaderboards): derive from indexed denorm at read
time, never a rebuild cron. Earners are a subset of pursuers in the normal path (earning a badge banks XP, so
the earner has a SeriesBadgeStanding row), so the percentage stays <=100 -- but earned_count is a manual denorm
that a cascade profile-delete can leave stale, so we clamp defensively rather than trust the bound as hard.

NOTE: both platform groups in a series share ONE pursuer denominator (the series' standings aren't split by
platform), a deliberate simplification -- so the two groups' percentages are "of all series pursuers", not
strictly cross-comparable. The display frames it that way ("% of pursuers earned it").
"""

# % of a series' pursuers who earned the badge -> rarity class (below the ceiling wins; else common). Tuned
# for the PURSUER-relative denominator (of people making progress, how many finished), NOT the whole userbase.
RARITY_THRESHOLDS = ((5.0, 'mythic'), (15.0, 'rare'), (35.0, 'uncommon'))


def rarity_class_for(pct: float) -> str:
    """Bucket a pursuer-completion percentage; lower = rarer."""
    for ceiling, name in RARITY_THRESHOLDS:
        if pct < ceiling:
            return name
    return 'common'


def group_rarity(earned_count: int, participants: int):
    """(pct, class) for a group badge given its earners + the series' pursuer count.
      - No pursuer base yet (participants == 0)  -> (None, '')  -> the caller renders 'rarity pending'.
      - Pursuers exist but nobody's earned it    -> (0.0, '')   -> honest "0% earned", but NO rarity class:
        0 earners is unearned, not a Mythic achievement, so it must not wear the prestige chip.
      - Otherwise the real percentage + its bucket."""
    if not participants:
        return None, ''
    pct = round(min(100.0, 100.0 * earned_count / participants), 1)
    return pct, (rarity_class_for(pct) if earned_count else '')
