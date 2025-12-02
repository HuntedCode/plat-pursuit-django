from django.utils import timezone
from django.conf import settings
import pytz

class TimezoneMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
    
    def __call__(self, request):
        tzname = settings.TIME_ZONE
        if request.user.is_authenticated:
            tzname = request.user.user_timezone
        timezone.activate(pytz.timezone(tzname))
        return self.get_response(request)