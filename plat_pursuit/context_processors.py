from django.conf import settings

def ads(request):
    enabled = settings.ADSENSE_ENABLED

    if request.path.startswith('/accounts/') or request.path.startswith('/profiles/$'):
        enabled = False

    if request.user.is_authenticated:
        user = request.user
        if hasattr(user, 'profile') and user.profile.sync_tier == 'preferred':
            enabled = False

    return {
        'ADSENSE_PUB_ID': settings.ADSENSE_PUB_ID,
        'ADSENSE_ENABLED': enabled
    }