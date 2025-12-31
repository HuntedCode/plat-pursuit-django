from django.conf import settings

def ads(request):
    enabled = settings.ADSENSE_ENABLED

    if request.path.startswith('/accounts/'):
        enabled = False

    if request.user.is_authenticated and request.user.profile and request.user.profile.sync_tier == 'preferred':
        enabled = False

    return {
        'ADSENSE_PUB_ID': settings.ADSENSE_PUB_ID,
        'ADSENSE_ENABLED': enabled
    }