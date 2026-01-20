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