from django.db.models import Q
from trophies.models import Badge


def get_featured_badges(limit=6):
    """
    Return a curated selection of badges for the homepage showcase.

    Strategy: Top 4 by earned_count (social proof) + 2 newest badges.
    All filtered to tier=1 (Bronze entry point).
    """
    popular = list(
        Badge.objects.filter(tier=1)
        .filter(
            Q(badge_image__isnull=False) | Q(base_badge__badge_image__isnull=False)
        )
        .exclude(badge_image='')
        .exclude(base_badge__badge_image='')
        .exclude(series_slug='')
        .exclude(series_slug__isnull=True)
        .select_related('base_badge')
        .order_by('-earned_count')[:4]
    )

    popular_ids = [b.id for b in popular]

    newest = list(
        Badge.objects.filter(tier=1)
        .exclude(series_slug='')
        .exclude(series_slug__isnull=True)
        .exclude(id__in=popular_ids)
        .select_related('base_badge')
        .order_by('-created_at')[:max(0, limit - len(popular))]
    )

    badges = popular + newest

    enriched = []
    for badge in badges:
        layers = badge.get_badge_layers()
        has_custom_image = bool(
            badge.badge_image
            or (badge.base_badge and badge.base_badge.badge_image)
        )
        enriched.append({
            'name': badge.effective_display_title or badge.name,
            'series': badge.effective_display_series or badge.series_slug,
            'description': badge.effective_description or '',
            'tier': badge.tier,
            'earned_count': badge.earned_count,
            'series_slug': badge.series_slug,
            'layers': layers,
            'has_custom_image': has_custom_image,
        })
    return enriched
