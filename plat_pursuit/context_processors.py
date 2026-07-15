import json
import logging

from django.conf import settings

logger = logging.getLogger(__name__)

def ads(request):
    enabled = settings.ADSENSE_ENABLED

    no_ad_prefixes = ['/accounts/', '/staff/', '/api/', '/admin/', '/fundraiser/', '/support/']
    if any(request.path.startswith(p) for p in no_ad_prefixes):
        enabled = False

    if request.user.is_authenticated and request.user.premium_tier:
        enabled = False

    return {
        'ADSENSE_PUB_ID': settings.ADSENSE_PUB_ID,
        'ADSENSE_ENABLED': enabled,
        'ADSENSE_TEST_MODE': settings.ADSENSE_TEST_MODE,
    }

def moderation(request):
    """
    Provide pending reports count for staff members.

    Only queries the database if the user is authenticated staff to avoid
    unnecessary overhead for regular users. Results are cached for 60 seconds
    to prevent per-request DB queries on every page load.
    """
    pending_reports_count = 0

    if request.user.is_authenticated and request.user.is_staff:
        from django.core.cache import cache
        from trophies.models import CommentReport

        pending_reports_count = cache.get_or_set(
            'mod:pending_reports_count',
            lambda: CommentReport.objects.filter(status='pending').count(),
            60,
        )

    return {
        'pending_reports_count': pending_reports_count,
        # Kept for template compatibility during the Phase 2.6 transition;
        # GameFamilyProposal is no longer used and the count is always 0.
        'pending_proposals_count': 0,
    }


def premium_theme_background(request):
    """
    Inject premium user's gradient theme as a site-wide <body> background
    (the user_theme_style variable).

    DISABLED: premium themes are turned off site-wide -- every user gets the base
    substrate background. Flip PREMIUM_THEMES_ENABLED / remove the guard to restore
    (the settings picker is disabled too; the settings-page rebuild will bring both
    back).
    """
    PREMIUM_THEMES_ENABLED = False
    if not PREMIUM_THEMES_ENABLED:
        return {}

    if not request.user.is_authenticated:
        return {}

    profile = getattr(request.user, 'profile', None)
    if not profile or not profile.user_is_premium:
        return {}

    if profile.selected_theme:
        from trophies.themes import get_theme_style
        return {'user_theme_style': get_theme_style(profile.selected_theme)}

    return {}


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
        from core.hub_subnav import (
            MY_PURSUIT_HUB,
            RenderedSubnavItem,
            build_rendered_items,
            resolve_hub_subnav,
        )

        match = resolve_hub_subnav(request)
        if match is None:
            return {'hub_section': None}

        hub = match['hub']
        active_slug = match['active_slug']

        # Ownership-aware Profile chrome: viewing your OWN profile swaps the page's chrome from
        # Community to the personal My Pursuit strip (Profile active); anyone else's profile keeps
        # the Community chrome the resolver returned. Same shared URL either way.
        if _is_own_profile_page(request):
            hub = MY_PURSUIT_HUB
            active_slug = 'profile'

        is_auth = bool(getattr(request, 'user', None) and request.user.is_authenticated)

        # The personal ("My Pursuit") strip is a login-gated wayfinder: hide it entirely for
        # anonymous viewers so the Home (/) reads as a hero and public members (e.g. /milestones/)
        # don't sprout a personal strip.
        if hub.key == 'my_pursuit' and not is_auth:
            return {'hub_section': None}

        extras: tuple[RenderedSubnavItem, ...] = ()
        if hub.key == 'my_pursuit' and _viewer_has_linked_profile(request):
            # Profile (its URL needs the viewer's own username). The fundraiser lives in the
            # Support hub now, not as a personal-strip item.
            extras = _profile_subnav_extra(request)

        items = build_rendered_items(hub, is_authenticated=is_auth, extras=extras)
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


def _profile_subnav_extra(request):
    """The viewer's own Profile as a dynamic sub-nav item -- its URL needs their username, so it
    can't be a static config item. Part of the personal hub's tools group (appended after Recap).
    Returns an empty tuple if the viewer has no reversible profile URL."""
    from django.urls import NoReverseMatch, reverse

    from core.hub_subnav import RenderedSubnavItem

    profile = getattr(getattr(request, 'user', None), 'profile', None)
    if not profile or not getattr(profile, 'psn_username', None):
        return ()
    try:
        url = reverse('profile_detail', kwargs={'psn_username': profile.psn_username})
    except NoReverseMatch:
        return ()
    return (RenderedSubnavItem(slug='profile', label='Profile', url=url, icon='user'),)


def _is_own_profile_page(request):
    """True when the request is the viewer's OWN, LINKED profile (detail or trophy-case) page --
    used to swap the profile chrome from Community to the personal My Pursuit strip. Gated on
    is_linked so it agrees with the Profile strip-item (which also needs a linked profile), so an
    unlinked-own viewer degrades cleanly to Community chrome rather than getting a strip with no
    Profile tab. Cheap: reads resolver kwargs + the already-loaded profile, no query."""
    rm = getattr(request, 'resolver_match', None)
    if rm is None or rm.url_name not in ('profile_detail', 'trophy_case'):
        return False
    profile = getattr(getattr(request, 'user', None), 'profile', None)
    if not profile or not getattr(profile, 'is_linked', False):
        return False
    viewed = (rm.kwargs or {}).get('psn_username')
    own = getattr(profile, 'psn_username', None)
    return bool(viewed and own) and viewed.lower() == own.lower()


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
