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


RARITY_CLASSES = ('common', 'uncommon', 'rare', 'mythic')
# "Be the first" filter value: badges nobody has earned yet (earned_count == 0 -> no rarity class). Kept OUT of
# RARITY_CLASSES so it's handled specially (it matches earned_count == 0, exactly like the card's nudge, not a
# bucket). Lets rarity filtering be a complete set + doubles as a "badges I could be first on" finder.
RARITY_UNEARNED = 'unearned'
# Chip order for the filter UI: rarest earned first, then the not-yet-earned nudge at the end.
RARITY_FILTER_CHOICES = [
    ('mythic', 'Mythic'), ('rare', 'Rare'), ('uncommon', 'Uncommon'), ('common', 'Common'),
    (RARITY_UNEARNED, 'Be the first'),
]


def annotate_group_rarity(gb_qs):
    """Annotate a GroupBadge queryset with `_rarity` -- its LIVE rarity class ('' when none) -- so rarity can be
    filtered DB-side (whale-safe, paginated) instead of in Python. Mirrors group_rarity() exactly: pct =
    100 * earned_count / the series' pursuer count (SeriesBadgeStanding rows), bucketed by RARITY_THRESHOLDS;
    '' when there are no pursuers or no earners. Pursuers come from a per-series correlated subquery
    (SeriesBadgeStanding is indexed on series_slug -> a fast index count)."""
    from django.db.models import (
        Subquery, OuterRef, Count, Case, When, Value, F, Q, FloatField, DecimalField, CharField, ExpressionWrapper,
    )
    from django.db.models.functions import Coalesce, NullIf, Round, Cast
    from trophies.models import SeriesBadgeStanding

    part_sq = (
        SeriesBadgeStanding.objects.filter(series_slug=OuterRef('series__series_slug'))
        .order_by().values('series_slug').annotate(n=Count('id')).values('n')[:1]
    )
    # NullIf(part, 0) -> NULL denominator when there are no pursuers, so the division is NULL (no divide-by-zero)
    # and reads as "no rarity". The pct is ROUNDED to 1 decimal to match group_rarity()'s round(pct, 1) BEFORE
    # bucketing -- otherwise a value in the thin band just under a ceiling would filter into a different tier than
    # the card displays. (Postgres round() is numeric-only, so cast the double to Decimal first.) The min(100)
    # clamp isn't mirrored: an over-100 pct buckets to common either way, so it never changes the class.
    # Ascending-ceiling Whens: the first match wins (pct 3 -> mythic before rare).
    threshold_whens = [When(_pct__lt=ceiling, then=Value(name)) for ceiling, name in RARITY_THRESHOLDS]
    return (
        gb_qs
        .annotate(_part=Coalesce(Subquery(part_sq), Value(0)))
        .annotate(_pct=Round(
            Cast(
                ExpressionWrapper(100.0 * F('earned_count') / NullIf(F('_part'), 0), output_field=FloatField()),
                output_field=DecimalField(max_digits=8, decimal_places=4),
            ),
            precision=1,
        ))
        .annotate(_rarity=Case(
            When(Q(earned_count=0) | Q(_pct__isnull=True), then=Value('')),   # no earners / no pursuers -> no class
            *threshold_whens,
            default=Value('common'),
            output_field=CharField(),
        ))
    )


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
