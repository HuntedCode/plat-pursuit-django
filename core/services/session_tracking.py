"""
Analytics session management service.

30-minute inactivity timeout aligned with Google Analytics standards.
Separate from Django sessions for decoupled analytics tracking.
"""
import logging
import threading
import uuid
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger("psn_api")

SESSION_COOKIE_NAME = 'pp_analytics_session'
SESSION_TIMEOUT = 1800  # 30 minutes in seconds
SESSION_CACHE_PREFIX = 'analytics_session'


def _get_ip(request):
    """Extract client IP, respecting X-Forwarded-For."""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


def get_or_create_session(request):
    """
    Get or create analytics session for request.

    Returns:
        str: Session ID (UUID as string)
    """
    # Check for existing session cookie
    session_id_str = request.COOKIES.get(SESSION_COOKIE_NAME)

    if session_id_str:
        # Validate and refresh session from Redis
        session = _get_session_from_cache(session_id_str)
        if session:
            _refresh_session_activity(session_id_str, request)
            return session_id_str

    # Create new session
    return _create_new_session(request)


def _get_session_from_cache(session_id_str):
    """Fetch session metadata from Redis."""
    cache_key = f"{SESSION_CACHE_PREFIX}:{session_id_str}"
    return cache.get(cache_key)


def _refresh_session_activity(session_id_str, request):
    """Update last_activity timestamp in Redis and extend TTL."""
    cache_key = f"{SESSION_CACHE_PREFIX}:{session_id_str}"
    session_data = cache.get(cache_key)

    if not session_data:
        return

    # Update cache with new TTL
    session_data['last_activity'] = timezone.now().isoformat()
    cache.set(cache_key, session_data, SESSION_TIMEOUT)


def _create_new_session(request):
    """Create new analytics session."""
    session_id = uuid.uuid4()
    session_id_str = str(session_id)
    user_id = request.user.id if request.user.is_authenticated else None
    ip_address = _get_ip(request)
    referrer = request.META.get('HTTP_REFERER', '')
    user_agent = request.META.get('HTTP_USER_AGENT', '')

    # Store in Redis cache
    session_data = {
        'session_id': session_id_str,
        'user_id': user_id,
        'ip_address': ip_address,
        'created_at': timezone.now().isoformat(),
        'last_activity': timezone.now().isoformat(),
        'referrer': referrer,
        'user_agent': user_agent,
    }
    cache_key = f"{SESSION_CACHE_PREFIX}:{session_id_str}"
    cache.set(cache_key, session_data, SESSION_TIMEOUT)

    # Create DB record in background thread
    thread = threading.Thread(
        target=_create_session_db_record,
        args=(session_id, user_id, ip_address, referrer, user_agent),
        daemon=True,
    )
    thread.start()

    return session_id_str


def _create_session_db_record(session_id, user_id, ip_address, referrer, user_agent):
    """Create AnalyticsSession DB record (runs in background)."""
    try:
        from core.models import AnalyticsSession
        AnalyticsSession.objects.create(
            session_id=session_id,
            user_id=user_id,
            ip_address=ip_address or None,
            referrer=referrer[:500] if referrer else None,
            user_agent=user_agent[:500] if user_agent else None,
        )
    except Exception:
        logger.exception("Failed to create AnalyticsSession: %s", session_id)
