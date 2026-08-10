"""Badge rarity: the BADGE-SPECIFIC half. The vocabulary and grading live in `services/rarity.py`.

What is badge-specific is the population: rarity here is "of everyone making progress on this series,
what fraction earned this group's badge". The numerator is the group badge's maintained `earned_count`
denorm (owned by badge_apply); the denominator is the series' PURSUER base -- the count of
SeriesBadgeStanding rows, which recompute_standing keeps to profiles with real progress (zero-progress
rows are deleted). A whole-userbase ratio would read Mythic for almost every badge.

Derived live from indexed denorms at read time, never a rebuild cron -- the sealed system's philosophy
(see badge_leaderboards). Earners are a subset of pursuers in the normal path (earning a badge banks XP,
so the earner has a standing row), so the percentage stays <=100 -- but earned_count is a manual denorm
that a cascade profile-delete can leave stale, so `rarity_for` clamps defensively.

NOTE: both platform groups in a series share ONE pursuer denominator (standings are not split by
platform), a deliberate simplification -- so the two groups' percentages are "of all series pursuers",
not strictly cross-comparable. The display frames it that way ("% of pursuers earned it").
"""
from trophies.services.rarity import (  # noqa: F401  (re-exported: long-standing import surface)
    RARITY_CLASSES, RARITY_FILTER_CHOICES, RARITY_THRESHOLDS, RARITY_UNEARNED,
    rarity_class_for, rarity_for,
)


def group_rarity(earned_count: int, participants: int):
    """(pct, class) for a group badge, given its earners + the series' pursuer count.

    Thin badge-flavoured name over `rarity.rarity_for`; the grading rules are shared with every other
    surface so a badge and its title cannot disagree about what "Rare" means.
    """
    return rarity_for(earned_count, participants)


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
