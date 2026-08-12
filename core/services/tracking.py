"""
Site event tracking service.

Records discrete, user-initiated actions (share card downloads, recap shares,
guide visits, challenge creates). Writes synchronously because these are
low-frequency -- one per deliberate click, not one per page load.

HISTORY: this module also carried per-request page-view and session tracking,
removed 2026-08. That system spawned a background thread per request to write
PageView/AnalyticsSession rows, and Django connections are thread-local, so each
thread opened a Postgres connection it never closed. Under a spoofed-UA scraper
sending no cookies every request looked new, so connections were consumed at the
request rate until max_connections was exhausted and the whole site queued behind
it. Its per-response Set-Cookie had also been silently disabling Cloudflare edge
caching site-wide. Traffic analytics now come from Cloudflare and Search Console,
which measure at the edge and cost nothing per request.

Challenge.view_count survived the removal: it is incremented inline by the three
challenge detail views, independent of this module.
"""
import logging

from core.services.bot_detection import is_bot_user_agent

logger = logging.getLogger("psn_api")


def track_site_event(event_type, object_id, request):
    """
    Record an internal site event. No deduplication -- every occurrence is recorded.
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
            - 'sync_search' - User searches for a PSN profile via hotbar sync
        object_id: Related object identifier (guide_slug, earned_trophy_id, 'YYYY-MM', challenge_id,
            'username|new|pid:N' or 'username|existing|pid:N' for sync_search)
        request: Django HttpRequest object
    """
    try:
        # Skip bot traffic. SiteEvents are user-initiated actions (challenge
        # creates, recap shares, etc.) so a bot triggering one is almost always
        # either a scripted abuser or a misclassified UA -- drop it rather than
        # pollute the funnel. Classified inline from the UA now that
        # AnalyticsSessionMiddleware (which used to set request.is_bot) is gone.
        # Pure regex, no DB and no cookie, and it only runs on real click events.
        if is_bot_user_agent(request.META.get('HTTP_USER_AGENT', '')):
            return

        from core.models import SiteEvent
        user_id = request.user.id if request.user.is_authenticated else None
        SiteEvent.objects.create(
            event_type=event_type,
            object_id=str(object_id),
            user_id=user_id,
        )
    except Exception:
        logger.exception("track_site_event failed: event_type=%s, object_id=%s", event_type, object_id)
