from django.utils import timezone
from django.conf import settings
import pytz
import threading

# Thread-local storage for current request
_thread_locals = threading.local()

def get_current_request():
    """Get the current request from thread-local storage."""
    return getattr(_thread_locals, 'request', None)

class TimezoneMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Store request in thread-local storage
        _thread_locals.request = request

        tzname = settings.TIME_ZONE
        if request.user.is_authenticated:
            tzname = request.user.user_timezone
        timezone.activate(pytz.timezone(tzname))

        response = self.get_response(request)

        # Clean up thread-local storage
        if hasattr(_thread_locals, 'request'):
            del _thread_locals.request

        return response