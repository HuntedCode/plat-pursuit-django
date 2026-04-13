"""Shared helpers for browse page views (Games, Genre/Theme Detail, Flagged Games)."""

from trophies.models import Badge


def get_badge_picker_context(request):
    """Build context dict for the browse badge picker modal.

    Returns picker_badges (list of dicts) and selected_badge_name (str).
    """
    badges = Badge.objects.filter(
        is_live=True, tier=1, series_slug__isnull=False,
    ).exclude(
        series_slug='',
    ).select_related('base_badge').order_by('display_series', 'name')

    picker_badges = []
    for b in badges:
        picker_badges.append({
            'series_slug': b.series_slug,
            'name': b.name,
            'display_series': b.display_series,
            'badge_type': b.badge_type,
            'earned_count': b.earned_count,
            'required_stages': b.required_stages,
            'layers': b.get_badge_layers(),
        })

    selected_slug = request.GET.get('badge_series', '')
    selected_name = ''
    if selected_slug:
        match = next(
            (b for b in picker_badges if b['series_slug'] == selected_slug),
            None,
        )
        if match:
            selected_name = match['display_series'] or match['name']

    return {
        'picker_badges': picker_badges,
        'selected_badge_name': selected_name,
    }
