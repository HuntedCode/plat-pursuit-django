import json
import logging

logger = logging.getLogger(__name__)

def site_links(request):
    """Site-wide external links. One source: the Discord invite used to be hardcoded in seven
    templates (with casing drift), while the setting the emails read was never defined."""
    from django.conf import settings
    return {'discord_invite_url': settings.DISCORD_INVITE_URL}


def active_fundraiser(request):
    """
    Inject the currently active fundraiser for the site-wide banner.

    The banner is only shown to viewers who are logged in AND have a
    linked PSN profile: claiming badge artworks requires a profile, so
    the banner is noise for anonymous users and for users who haven't
    finished onboarding. Non-qualifying viewers get an empty context,
    which the banner partial treats as "don't render."

    Caches the fundraiser's PK for 60 seconds (model instances can't be
    JSON-serialized by django-redis's JSONSerializer, so we cache the ID
    and do a cheap PK lookup). A cached value of 0 means "no active
    fundraiser" to distinguish from a cache miss. The cache is shared
    across all users; the per-user gate lives at render time.
    """
    fundraiser = _active_fundraiser_or_none(request)
    return {'active_fundraiser': fundraiser} if fundraiser else {}


def _viewer_has_linked_profile(request):
    """True when the viewer is authenticated AND has a linked PSN profile."""
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return False
    profile = getattr(user, 'profile', None)
    return bool(profile and profile.is_linked)


def high_sync_volume(request):
    """
    Check Redis for high sync volume flag and inject banner data into all templates.
    Single Redis GET per request (sub-millisecond).
    """
    try:
        from trophies.util_modules.cache import redis_client

        raw = redis_client.get('site:high_sync_volume')
        if not raw:
            return {}

        raw_str = raw.decode() if isinstance(raw, bytes) else raw
        parsed = json.loads(raw_str)
        return {
            'high_sync_volume': True,
            'high_sync_volume_count': parsed.get('heavy_count', 0),
            'high_sync_volume_activated_at': parsed.get('activated_at', 0),
        }
    except Exception:
        logger.debug("Failed to read high sync volume flag from Redis", exc_info=True)
        return {}


def psn_outage(request):
    """
    Check Redis for PSN outage flag and inject banner data into all templates.
    Single Redis GET per request (sub-millisecond).
    """
    try:
        from trophies.util_modules.cache import redis_client

        raw = redis_client.get('site:psn_outage')
        if not raw:
            return {}

        raw_str = raw.decode() if isinstance(raw, bytes) else raw
        parsed = json.loads(raw_str)
        return {
            'psn_outage': True,
            'psn_outage_activated_at': parsed.get('activated_at', 0),
        }
    except Exception:
        logger.debug("Failed to read PSN outage flag from Redis", exc_info=True)
        return {}


def hub_subnav(request):
    """
    Resolve the active hub-of-hubs sub-navigation for the current request.

    Inspects ``request.path`` against the configured hub prefixes
    (``core.hub_subnav.HUB_SUBNAV_CONFIG``) and returns the active hub plus
    the active sub-nav slug. Pages that don't belong to any hub get
    ``hub_section=None`` so the ``hub_subnav.html`` template short-circuits
    and renders nothing.

    Dynamic behavior: the personal (My Pursuit) hub appends the viewer's Profile
    as a dynamic item (its URL needs their username), and the personal strip is
    auth-gated (hidden for anon). Viewing your OWN profile swaps that page's
    Community chrome for the personal strip.

    See ``docs/architecture/ia-and-subnav.md`` for the design rationale and
    the URL prefix matching algorithm.
    """
    try:
        from core.hub_subnav import build_rendered_items, resolve_hub_subnav

        match = resolve_hub_subnav(request)
        if match is None:
            return {'hub_section': None}

        hub = match['hub']
        active_slug = match['active_slug']

        # Ownership-aware Profile chrome was removed with the Profile strip-item (2026-08): the swap
        # existed to put your own profile under a personal strip WITH a Profile tab to highlight, and
        # with no such tab it would have rendered a strip highlighting nothing. Every profile page now
        # carries the same chrome whoever is looking, and the avatar menu is the single route to yours.

        is_auth = bool(getattr(request, 'user', None) and request.user.is_authenticated)

        # The personal ("My Pursuit") strip is a login-gated wayfinder: hide it entirely for
        # anonymous viewers so the Home (/) reads as a hero and public members (e.g. /milestones/)
        # don't sprout a personal strip.
        if hub.key == 'my_pursuit' and not is_auth:
            return {'hub_section': None}

        # No dynamic extras: Profile is reached from the avatar menu, and the fundraiser lives in the
        # Support hub.
        is_member = is_auth and bool(getattr(request.user, 'premium_tier', ''))
        items = build_rendered_items(hub, is_authenticated=is_auth, is_member=is_member,
                                     active_slug=active_slug)
        active_label = next((i.label for i in items if i.slug == active_slug), '')

        return {
            'hub_section': hub.key,
            'hub_subnav_label': hub.label,
            'hub_subnav_icon': hub.icon,
            'hub_subnav_items': items,
            'hub_subnav_active_slug': active_slug,
            'hub_subnav_active_label': active_label,   # current page, for the mobile collapse bar
        }
    except Exception:
        logger.debug("Failed to resolve hub_subnav for path %s", request.path, exc_info=True)
        return {'hub_section': None}


def _active_fundraiser_or_none(request=None):
    """The currently-live, banner-active Fundraiser (or None), gated to viewers with a linked
    profile (matches the site-wide banner audience). Shares the ``fundraiser:active_banner`` cache
    key (60s, PK-only) so it's a cache GET on the hot path."""
    if request is not None and not _viewer_has_linked_profile(request):
        return None
    try:
        from fundraiser.models import get_active_fundraiser
        return get_active_fundraiser()
    except Exception:
        logger.debug("Failed to resolve active fundraiser", exc_info=True)
        return None


def moderation_alert(request):
    """The Mod Center entry in the avatar menu, and whether it needs attention.

    Everyone who is not a moderator returns an empty dict before anything else happens: no cache
    read, no query, nothing. That gate is the whole cost of this processor for ~every visitor.

    The FLAG is separate from the COUNT on purpose. The entry has to appear for a moderator whose
    queues are empty -- a link that materialises only when there is work is a link nobody can find
    when they go looking for it. The count only decides whether it is wearing a marker.
    """
    from trophies.mixins import is_mod_or_admin

    if not is_mod_or_admin(getattr(request, 'user', None)):
        return {}
    try:
        from trophies.services import moderation_service
        return {'show_mod_center': True, 'mod_open_count': moderation_service.open_report_count()}
    except Exception:
        # The menu entry still renders. Losing the count costs a moderator a marker; letting this
        # raise would cost every moderator every page on the site.
        logger.debug("Failed to resolve the moderation attention count", exc_info=True)
        return {'show_mod_center': True}


def navsync(request):
    """Global profile sync state for the navbar's status-aware avatar + panel.

    The old hotbar was a per-view bar (ProfileHotbarMixin); the sync surface now
    lives in the always-present navbar, so its context must be global. Cheap: every
    value reads off the already-loaded ``request.user.profile`` (no new queries; the
    queue lookup only runs mid-sync). Anon / profile-less viewers get nothing, so the
    navbar renders the plain account avatar with no sync ring.
    """
    user = getattr(request, "user", None)
    if not (user and user.is_authenticated and hasattr(user, "profile")):
        return {}
    profile = user.profile
    data = {
        "profile": profile,
        "sync_status": profile.sync_status,
        "progress_percentage": profile.sync_percentage,
        "seconds_to_next_sync": profile.get_seconds_to_next_sync(),
    }
    if profile.sync_status == "syncing":
        try:
            from trophies.views.sync_views import _get_queue_position
            data["queue_position"] = _get_queue_position(profile.id)
        except Exception:
            logger.debug("Failed to resolve sync queue position", exc_info=True)
    return {"navsync": data}
