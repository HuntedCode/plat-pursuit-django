"""Badge rarity: the BADGE-SPECIFIC half. The vocabulary and grading live in `services/rarity.py`.

What is badge-specific is only the NUMERATOR: the group badge's maintained `earned_count` denorm (owned
by badge_apply). The denominator is the shared one -- every PSN-linked account (`rarity.community_size`).

So rarity here reads "of the whole community, what fraction earned this badge", which is what PSN's own
trophy rarity means and what the legacy Badge model always used. It replaced a per-series PURSUER base
that could SHRINK (standing rows are deleted at zero progress), letting a badge look rarer because
people abandoned the series.

Derived live from indexed denorms at read time, never a rebuild cron -- the sealed system's philosophy
(see badge_leaderboards). `earned_count` is a manual denorm that a cascade profile-delete can leave
stale and an unlinked earner is not in the denominator, so `rarity_for` clamps to 100 defensively.
"""
from trophies.services.rarity import (  # noqa: F401  (re-exported: long-standing import surface)
    RARITY_CLASSES, RARITY_FILTER_CHOICES, RARITY_THRESHOLDS, RARITY_UNEARNED,
    community_size, rarity_class_for, rarity_for,
)


def group_rarity(earned_count: int, community_count: int):
    """(pct, class) for a group badge, given its earners + the community size.

    Thin badge-flavoured name over `rarity.rarity_for`; the grading rules are shared with every other
    surface so a badge and its title cannot disagree about what "Rare" means.
    """
    return rarity_for(earned_count, community_count)


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

    # The denominator is now a single site-wide scalar, so the correlated per-series subquery this used
    # to run is gone -- one fewer join per filtered badge, and it cannot disagree with what the cards
    # render because both read `rarity.community_size`.
    community = community_size()

    # NullIf(community, 0) -> NULL denominator on a fresh install, so the division is NULL (no
    # divide-by-zero) and reads as "no rarity". The pct is ROUNDED to 1 decimal to match rarity_for()'s
    # round(pct, 1) BEFORE bucketing -- otherwise a value in the thin band just under a ceiling would
    # filter into a different tier than the card displays. (Postgres round() is numeric-only, so cast the
    # double to Decimal first.) The min(100) clamp isn't mirrored: an over-100 pct buckets to common
    # either way, so it never changes the class.
    # Ascending-ceiling Whens: the first match wins (pct 0.4 -> mythic before rare).
    threshold_whens = [When(_pct__lt=ceiling, then=Value(name)) for ceiling, name in RARITY_THRESHOLDS]
    return (
        gb_qs
        .annotate(_pct=Round(
            Cast(
                ExpressionWrapper(100.0 * F('earned_count') / NullIf(Value(community), 0), output_field=FloatField()),
                output_field=DecimalField(max_digits=8, decimal_places=4),
            ),
            precision=1,
        ))
        .annotate(_rarity=Case(
            When(Q(earned_count=0) | Q(_pct__isnull=True), then=Value('')),   # no earners / no community -> no class
            *threshold_whens,
            default=Value('common'),
            output_field=CharField(),
        ))
    )
