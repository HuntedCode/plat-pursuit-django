from django.conf import settings

def ads(request):
    enabled = settings.ADSENSE_ENABLED

    if request.path.startswith('/accounts/'):
        enabled = False

    if request.user.is_authenticated and request.user.premium_tier:
        enabled = False

    return {
        'ADSENSE_PUB_ID': settings.ADSENSE_PUB_ID,
        'ADSENSE_ENABLED': enabled
    }

def moderation(request):
    """
    Provide pending reports count for staff members.

    Only queries the database if the user is authenticated staff to avoid
    unnecessary overhead for regular users.
    """
    pending_reports_count = 0

    if request.user.is_authenticated and request.user.is_staff:
        from trophies.models import CommentReport
        pending_reports_count = CommentReport.objects.filter(status='pending').count()

    return {
        'pending_reports_count': pending_reports_count
    }


def premium_theme_background(request):
    """
    Inject premium user's gradient theme as a fallback site-wide background.

    This provides the user_theme_style variable to templates, which is used
    when no page-specific game background (image_urls.bg_url) is set.
    """
    if not request.user.is_authenticated:
        return {}

    profile = getattr(request.user, 'profile', None)
    if not profile or not profile.user_is_premium:
        return {}

    if profile.selected_theme:
        from trophies.themes import get_theme_style
        return {'user_theme_style': get_theme_style(profile.selected_theme)}

    return {}