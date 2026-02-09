from django.utils import timezone
from django.urls import reverse
from datetime import timedelta
from trophies.models import Badge, Checklist, Concept


def get_whats_new(limit=8):
    """
    Aggregate recent platform activity into a unified feed.

    Item types:
    - new_badge: Recently created badge series (tier=1)
    - new_checklist: Recently published checklists
    - new_guide: Concepts that have guides, sorted by release date
    """
    now = timezone.now()
    two_weeks_ago = now - timedelta(days=14)
    items = []

    # New badge series (tier=1 only, recent, must have series_slug)
    new_badges = (
        Badge.objects.filter(tier=1, created_at__gte=two_weeks_ago)
        .exclude(series_slug='')
        .exclude(series_slug__isnull=True)
        .select_related('base_badge')
        .order_by('-created_at')[:3]
    )
    for badge in new_badges:
        items.append({
            'type': 'new_badge',
            'title': badge.effective_display_title or badge.name,
            'subtitle': badge.effective_display_series or badge.series_slug,
            'url': reverse('badge_detail', args=[badge.series_slug]),
            'timestamp': badge.created_at.isoformat(),
        })

    # Recently published checklists
    new_checklists = (
        Checklist.objects.filter(
            status='published',
            is_deleted=False,
            published_at__gte=two_weeks_ago,
        )
        .select_related('profile', 'concept')
        .order_by('-published_at')[:3]
    )
    for cl in new_checklists:
        items.append({
            'type': 'new_checklist',
            'title': cl.title,
            'subtitle': f"By {cl.profile.display_psn_username}",
            'url': reverse('checklist_detail', args=[cl.id]),
            'timestamp': cl.published_at.isoformat() if cl.published_at else cl.created_at.isoformat(),
        })

    # New guides (concepts with guide_slug, by guide creation date)
    new_guides = (
        Concept.objects
        .filter(guide_created_at__isnull=False)
        .filter(guide_created_at__gte=two_weeks_ago)
        .exclude(guide_slug__isnull=True)
        .exclude(guide_slug='')
        .order_by('-guide_created_at')[:3]
    )
    for concept in new_guides:
        # Get the first game for this concept to build URL
        game = concept.games.first()
        url = reverse('game_detail', args=[game.np_communication_id]) if game else reverse('guides_list')
        items.append({
            'type': 'new_guide',
            'title': concept.unified_title or 'New Guide',
            'subtitle': 'PPTV Guide',
            'url': url,
            'timestamp': concept.guide_created_at.isoformat(),
        })

    # Sort by timestamp descending, take limit
    items.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
    return items[:limit]
