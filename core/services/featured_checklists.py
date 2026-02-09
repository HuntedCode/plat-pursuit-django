from django.db.models import Count
from trophies.models import Checklist


def get_featured_checklists(limit=4):
    """
    Return popular published checklists for the homepage showcase.
    Ordered by most tracked (progress_save_count), then most upvoted.
    """
    checklists = (
        Checklist.objects.filter(
            status='published',
            is_deleted=False,
        )
        .select_related('profile', 'concept')
        .annotate(
            section_count=Count('sections', distinct=True),
        )
        .order_by('-progress_save_count', '-upvote_count')[:limit]
    )

    enriched = []
    for cl in checklists:
        enriched.append({
            'id': cl.id,
            'title': cl.title,
            'description': cl.description[:150] if cl.description else '',
            'thumbnail_url': cl.thumbnail.url if cl.thumbnail else None,
            'upvote_count': cl.upvote_count,
            'progress_save_count': cl.progress_save_count,
            'section_count': cl.section_count,
            'author': {
                'username': cl.profile.display_psn_username,
                'avatar_url': cl.profile.avatar_url,
                'is_premium': cl.profile.user_is_premium,
            },
            'game_name': cl.concept.unified_title if cl.concept else '',
            'game_icon': cl.concept.concept_icon_url if cl.concept else '',
        })
    return enriched
