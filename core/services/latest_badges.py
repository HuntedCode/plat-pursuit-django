from trophies.models import Badge


def get_latest_badges(limit=10):
    """Return recently created badge series for homepage showcase."""

    badges = (
        Badge.objects.live().filter(tier=1)
        .exclude(series_slug='')
        .exclude(series_slug__isnull=True)
        .select_related('base_badge')
        .order_by('-created_at')[:limit]
    )

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
            'series_slug': badge.series_slug,
            'description': badge.description[:100] if badge.description else '',
            'earned_count': badge.earned_count,
            'layers': layers,
            'has_custom_image': has_custom_image,
            'created_at': badge.created_at.isoformat(),
        })
    return enriched
