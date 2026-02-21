from django.db.models import Q
from trophies.models import Badge


def get_featured_badges(limit=6):
    """
    Return a curated selection of badges for the homepage showcase.

    Strategy: Top 4 by earned_count (social proof) + 2 newest badges.
    All filtered to tier=1 (Bronze entry point).
    """
    base_qs = (
        Badge.objects.live().filter(tier=1)
        .filter(
            Q(badge_image__isnull=False, badge_image__gt='')
            | Q(base_badge__badge_image__isnull=False, base_badge__badge_image__gt='')
        )
        .exclude(series_slug='')
        .exclude(series_slug__isnull=True)
        .select_related('base_badge')
    )

    popular = list(base_qs.order_by('-earned_count')[:4])
    popular_ids = [b.id for b in popular]

    newest = list(
        base_qs.exclude(id__in=popular_ids)
        .order_by('-created_at')[:max(0, limit - len(popular))]
    )

    enriched = []
    for badge in popular + newest:
        enriched.append({
            'name': badge.effective_display_title or badge.name,
            'series': badge.effective_display_series or badge.series_slug,
            'description': badge.effective_description or '',
            'tier': badge.tier,
            'earned_count': badge.earned_count,
            'series_slug': badge.series_slug,
            'layers': badge.get_badge_layers(),
        })
    return enriched
