import json
import logging

from django.conf import settings

logger = logging.getLogger(__name__)

def ads(request):
    enabled = settings.ADSENSE_ENABLED

    no_ad_prefixes = ['/accounts/', '/staff/', '/api/', '/admin/', '/fundraiser/']
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
    Provide pending reports count for staff members and pending game family
    proposals count for superusers.

    Only queries the database if the user is authenticated staff to avoid
    unnecessary overhead for regular users. Results are cached for 60 seconds
    to prevent per-request DB queries on every page load.
    """
    pending_reports_count = 0
    pending_proposals_count = 0

    if request.user.is_authenticated and request.user.is_staff:
        from django.core.cache import cache
        from trophies.models import CommentReport

        pending_reports_count = cache.get_or_set(
            'mod:pending_reports_count',
            lambda: CommentReport.objects.filter(status='pending').count(),
            60,
        )

        if request.user.is_superuser:
            from trophies.models import GameFamilyProposal
            pending_proposals_count = cache.get_or_set(
                'mod:pending_proposals_count',
                lambda: GameFamilyProposal.objects.filter(status='pending').count(),
                60,
            )

    return {
        'pending_reports_count': pending_reports_count,
        'pending_proposals_count': pending_proposals_count,
    }


def premium_theme_background(request):
    """
    Inject premium user's gradient theme as a fallback site-wide background.

    This provides the user_theme_style variable to templates, which is used
    when no page-specific game background (image_urls.bg_url) is set.
    """
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
    if not _viewer_has_linked_profile(request):
        return {}

    try:
        from django.core.cache import cache
        from fundraiser.models import Fundraiser
        from django.utils import timezone

        def _fetch_id():
            from django.db.models import Q
            now = timezone.now()
            fundraiser = (
                Fundraiser.objects
                .filter(banner_active=True, start_date__lte=now)
                .filter(Q(end_date__isnull=True) | Q(end_date__gte=now))
                .first()
            )
            return fundraiser.pk if fundraiser else 0

        fundraiser_id = cache.get_or_set('fundraiser:active_banner', _fetch_id, 60)
        if fundraiser_id:
            fundraiser = Fundraiser.objects.filter(pk=fundraiser_id).first()
            if fundraiser:
                return {'active_fundraiser': fundraiser}
    except ImportError:
        logger.warning("Fundraiser app not available", exc_info=True)
    except Exception:
        logger.warning("Failed to check active fundraiser", exc_info=True)

    return {}


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

    Dynamic items: when a fundraiser is active (``banner_active=True`` and
    within its start/end window), a Fundraiser tab is appended to the
    Dashboard hub's items. Reuses the ``fundraiser:active_banner`` cache key
    populated by ``active_fundraiser`` so there's no extra DB hit on the
    hot path.

    See ``docs/architecture/ia-and-subnav.md`` for the design rationale and
    the URL prefix matching algorithm.
    """
    try:
        from core.hub_subnav import (
            RenderedSubnavItem,
            build_rendered_items,
            resolve_hub_subnav,
        )

        match = resolve_hub_subnav(request)
        if match is None:
            return {'hub_section': None}

        hub = match['hub']
        is_auth = bool(getattr(request, 'user', None) and request.user.is_authenticated)

        extras: tuple[RenderedSubnavItem, ...] = ()
        if hub.key == 'dashboard' and _viewer_has_linked_profile(request):
            extras = _fundraiser_subnav_extras()

        items = build_rendered_items(hub, is_authenticated=is_auth, extras=extras)

        return {
            'hub_section': hub.key,
            'hub_subnav_label': hub.label,
            'hub_subnav_icon': hub.icon,
            'hub_subnav_items': items,
            'hub_subnav_active_slug': match['active_slug'],
        }
    except Exception:
        logger.debug("Failed to resolve hub_subnav for path %s", request.path, exc_info=True)
        return {'hub_section': None}


def _fundraiser_subnav_extras():
    """
    Build the dynamic Fundraiser sub-nav item for the Dashboard hub, or an
    empty tuple if no campaign is currently active.

    Shares the ``fundraiser:active_banner`` cache key with
    ``active_fundraiser`` (60s TTL, PK-only value) so this is a cache
    GET on the hot path. Uses ``get_or_set`` so the lookup works
    regardless of whether ``active_fundraiser`` has run yet on this
    request; subsequent processors then hit the primed cache.
    """
    from django.core.cache import cache
    from django.db.models import Q
    from django.urls import NoReverseMatch, reverse
    from django.utils import timezone

    from core.hub_subnav import RenderedSubnavItem

    def _fetch_id():
        try:
            from fundraiser.models import Fundraiser
        except ImportError:
            return 0
        now = timezone.now()
        fundraiser = (
            Fundraiser.objects
            .filter(banner_active=True, start_date__lte=now)
            .filter(Q(end_date__isnull=True) | Q(end_date__gte=now))
            .first()
        )
        return fundraiser.pk if fundraiser else 0

    fundraiser_id = cache.get_or_set('fundraiser:active_banner', _fetch_id, 60)
    if not fundraiser_id:
        return ()

    try:
        from fundraiser.models import Fundraiser
        fundraiser = Fundraiser.objects.filter(pk=fundraiser_id).only('slug').first()
    except Exception:
        return ()

    if not fundraiser:
        return ()

    try:
        url = reverse('fundraiser', args=[fundraiser.slug])
    except NoReverseMatch:
        return ()

    return (RenderedSubnavItem(slug='fundraiser', label='Fundraiser', url=url, icon='heart'),)
