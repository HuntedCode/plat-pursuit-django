"""
Page view and site event tracking service.

Design:
- Redis key `pv:dedup:{page_type}:{object_id}:{viewer_key}` with 86400s TTL
  gates whether to count a view (one view per viewer per page per 24 hours).
- If the Redis gate passes, the PageView DB record and denormalized view_count
  update run in a background daemon thread to keep the hot path fast.
- SiteEvents (guide visits, share card downloads, recap shares) write synchronously
  since they are low-frequency.
"""
import logging
import threading

from django.core.cache import cache
from django.db.models import F

logger = logging.getLogger("psn_api")

_DEDUP_TTL = 86400  # 24 hours in seconds


def _get_ip(request):
    """Extract client IP, respecting X-Forwarded-For for proxied requests."""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


def _get_viewer_key(request):
    """
    Returns a stable string identifier for the viewer.
    Authenticated: 'u:{user_id}'
    Anonymous: 'ip:{ip_address}'
    """
    if request.user.is_authenticated:
        return f"u:{request.user.id}"
    ip = _get_ip(request)
    return f"ip:{ip}" if ip else "ip:unknown"


def _write_pageview_to_db(page_type, object_id, user_id, ip_address):
    """Write a PageView record and increment the parent model's view_count. Runs in background thread."""
    try:
        from core.models import PageView
        PageView.objects.create(
            page_type=page_type,
            object_id=str(object_id),
            user_id=user_id,
            ip_address=ip_address or None,
        )
        _increment_parent_view_count(page_type, object_id)
    except Exception:
        logger.exception("Failed to write page view: page_type=%s, object_id=%s", page_type, object_id)


def _increment_parent_view_count(page_type, object_id):
    """Increment the denormalized view_count on the parent model using F() for atomicity."""
    try:
        if page_type == 'profile':
            from trophies.models import Profile
            Profile.objects.filter(id=int(object_id)).update(view_count=F('view_count') + 1)
        elif page_type == 'game':
            from trophies.models import Game
            Game.objects.filter(id=int(object_id)).update(view_count=F('view_count') + 1)
        elif page_type == 'checklist':
            from trophies.models import Checklist
            Checklist.objects.filter(id=int(object_id)).update(view_count=F('view_count') + 1)
        elif page_type == 'badge':
            from trophies.models import Badge
            # Only increment the tier=1 badge (canonical series entry)
            Badge.objects.filter(series_slug=object_id, tier=1).update(view_count=F('view_count') + 1)
        elif page_type == 'index':
            from core.models import SiteSettings
            SiteSettings.objects.filter(id=1).update(index_page_view_count=F('index_page_view_count') + 1)
    except Exception:
        logger.exception("Failed to increment view_count: page_type=%s, object_id=%s", page_type, object_id)


def track_page_view(page_type, object_id, request):
    """
    Track a deduplicated page view with background DB write.

    Deduplication: one view per viewer per page per 24 hours.
    - Authenticated users identified by user_id
    - Anonymous users identified by IP address

    Args:
        page_type: One of 'profile', 'game', 'checklist', 'badge', 'index'
        object_id: The object's identifier (int PK for profile/game/checklist, series_slug str for badge, 'home' for index)
        request: Django HttpRequest object
    """
    try:
        viewer_key = _get_viewer_key(request)
        dedup_key = f"pv:dedup:{page_type}:{object_id}:{viewer_key}"

        # cache.add() sets the key only if it doesn't exist; returns True if added (new view)
        is_new_view = cache.add(dedup_key, 1, _DEDUP_TTL)

        if not is_new_view:
            return

        # Extract these in main thread before spawning background thread
        user_id = request.user.id if request.user.is_authenticated else None
        ip_address = _get_ip(request) if not request.user.is_authenticated else None

        thread = threading.Thread(
            target=_write_pageview_to_db,
            args=(page_type, str(object_id), user_id, ip_address),
            daemon=True,
        )
        thread.start()

    except Exception:
        logger.exception("track_page_view failed: page_type=%s, object_id=%s", page_type, object_id)


def track_site_event(event_type, object_id, request):
    """
    Record an internal site event. No deduplication — every occurrence is recorded.
    Writes synchronously (these are low-frequency actions).

    Args:
        event_type: One of:
            - 'guide_visit' - User visits a guide page
            - 'share_card_download' - User downloads a platinum share card image
            - 'recap_page_view' - User visits a monthly recap page
            - 'recap_share_generate' - User views the monthly recap share card on summary slide
            - 'recap_image_download' - User downloads monthly recap share image
        object_id: Related object identifier (guide_slug, earned_trophy_id, 'YYYY-MM')
        request: Django HttpRequest object
    """
    try:
        from core.models import SiteEvent
        user_id = request.user.id if request.user.is_authenticated else None
        SiteEvent.objects.create(
            event_type=event_type,
            object_id=str(object_id),
            user_id=user_id,
        )
    except Exception:
        logger.exception("track_site_event failed: event_type=%s, object_id=%s", event_type, object_id)
