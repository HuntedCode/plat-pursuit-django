"""
Page view and site event tracking service.

Design:
- Redis key `pv:dedup:{page_type}:{object_id}:{session_id}` with 1800s TTL
  gates whether to count a view (one view per session per page per 30 minutes).
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

_DEDUP_TTL = 1800  # 30 minutes in seconds (aligned with session timeout)


def _get_ip(request):
    """Extract client IP, respecting X-Forwarded-For for proxied requests."""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


def _append_to_page_sequence(page_type, object_id, session_id):
    """
    Append a page visit to the session's page_sequence. Runs in background thread.

    Called on every page view (before dedup) to capture the full navigation path,
    including repeat visits to the same page.

    Note: For brand-new sessions, the AnalyticsSession DB row is created in a
    separate background thread. A single retry with 0.5s delay handles this race.
    """
    try:
        from django.utils import timezone
        from django.db import connection

        page_entry_json = (
            f'[{{"page_type": "{page_type}", '
            f'"object_id": "{str(object_id)}", '
            f'"timestamp": "{timezone.now().isoformat()}"}}]'
        )
        session_id_str = str(session_id)

        with connection.cursor() as cursor:
            cursor.execute("""
                UPDATE core_analyticssession
                SET page_sequence = page_sequence || %s::jsonb
                WHERE session_id = %s
            """, [page_entry_json, session_id_str])

            if cursor.rowcount == 0:
                import time
                time.sleep(0.5)
                cursor.execute("""
                    UPDATE core_analyticssession
                    SET page_sequence = page_sequence || %s::jsonb
                    WHERE session_id = %s
                """, [page_entry_json, session_id_str])

                if cursor.rowcount == 0:
                    logger.warning(
                        "AnalyticsSession row not found after retry (sequence): session_id=%s, page_type=%s",
                        session_id_str, page_type,
                    )
    except Exception:
        logger.exception("Failed to append page sequence: page_type=%s, object_id=%s", page_type, object_id)


def _write_pageview_to_db(page_type, object_id, user_id, ip_address, session_id):
    """
    Write a PageView record and increment counters. Runs in background thread.

    Only called for deduplicated views (unique page per session per 30-min window).
    Updates:
    - PageView record (creates new row)
    - Parent model view_count (Profile, Game, etc.)
    - AnalyticsSession page_count (unique pages visited)

    Note: page_sequence is updated separately by _append_to_page_sequence (pre-dedup).
    """
    try:
        from core.models import PageView
        from django.db import connection

        # Create PageView record
        PageView.objects.create(
            page_type=page_type,
            object_id=str(object_id),
            user_id=user_id,
            ip_address=ip_address or None,
            session_id=session_id,
        )

        # Increment parent model view_count
        _increment_parent_view_count(page_type, object_id)

        # Increment session page_count (unique pages only, deduped)
        session_id_str = str(session_id)
        with connection.cursor() as cursor:
            cursor.execute("""
                UPDATE core_analyticssession
                SET page_count = page_count + 1
                WHERE session_id = %s
            """, [session_id_str])
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
        elif page_type == 'game_list':
            from trophies.models import GameList
            GameList.objects.filter(id=int(object_id)).update(view_count=F('view_count') + 1)
        elif page_type == 'az_challenge':
            from trophies.models import Challenge
            Challenge.objects.filter(id=int(object_id)).update(view_count=F('view_count') + 1)
        elif page_type == 'index':
            from core.models import SiteSettings
            SiteSettings.objects.filter(id=1).update(index_page_view_count=F('index_page_view_count') + 1)
    except Exception:
        logger.exception("Failed to increment view_count: page_type=%s, object_id=%s", page_type, object_id)


def track_page_view(page_type, object_id, request):
    """
    Track a deduplicated page view with background DB write.

    Deduplication: one view per session per page per 30-minute window.
    Session identified by analytics_session_id (set by AnalyticsSessionMiddleware).

    Args:
        page_type: One of 'profile', 'game', 'checklist', 'badge', 'game_list', 'index', etc.
        object_id: The object's identifier (int PK for profile/game/checklist/game_list, series_slug str for badge, 'home' for index)
        request: Django HttpRequest object
    """
    try:
        # Get analytics session ID from request (set by middleware)
        session_id = getattr(request, 'analytics_session_id', None)
        if not session_id:
            logger.warning("No analytics_session_id on request - skipping track_page_view for %s:%s", page_type, object_id)
            return

        # Always append to page_sequence (captures full navigation path including repeat visits)
        seq_thread = threading.Thread(
            target=_append_to_page_sequence,
            args=(page_type, str(object_id), session_id),
            daemon=True,
        )
        seq_thread.start()

        # Dedup: one PageView record + page_count increment per page per session per 30-min window
        dedup_key = f"pv:dedup:{page_type}:{object_id}:{session_id}"
        is_new_view = cache.add(dedup_key, 1, _DEDUP_TTL)

        if not is_new_view:
            return

        # Extract these in main thread before spawning background thread
        user_id = request.user.id if request.user.is_authenticated else None
        ip_address = _get_ip(request) if not request.user.is_authenticated else None

        thread = threading.Thread(
            target=_write_pageview_to_db,
            args=(page_type, str(object_id), user_id, ip_address, session_id),
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
            - 'game_list_create' - User creates a new game list
            - 'game_list_share' - User copies/shares a game list URL
            - 'challenge_create' - User creates a new challenge
            - 'challenge_complete' - User completes a challenge (all slots done)
        object_id: Related object identifier (guide_slug, earned_trophy_id, 'YYYY-MM', challenge_id)
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
