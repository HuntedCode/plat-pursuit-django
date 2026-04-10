"""
REST API views for profile card share images and public forum signatures.

Profile Card endpoints:
    GET /api/v1/profile-card/html/       - Rendered HTML for preview (auth required)
    GET /api/v1/profile-card/png/        - Server-side PNG render (auth required)
    POST /api/v1/profile-card/settings/  - Update card settings (auth required)
    POST /api/v1/profile-card/regenerate-token/ - Regenerate public sig token (auth required)

Public forum signature endpoints (no auth, in main urls.py):
    GET /sig/<uuid:token>.png            - Pre-rendered PNG
    GET /sig/<uuid:token>.svg            - Pre-rendered SVG
"""
from django.http import HttpResponse, Http404, FileResponse
from django.template.loader import render_to_string
from django.utils.decorators import method_decorator
from django_ratelimit.decorators import ratelimit
from rest_framework import status as http_status
from rest_framework.authentication import SessionAuthentication, TokenAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.services.profile_card_service import ProfileCardDataService
from core.services.share_image_cache import ShareImageCache
from trophies.models import ProfileCardSettings

import logging

logger = logging.getLogger(__name__)


def _resolve_badge_image(badge_image_url):
    """
    Resolve a badge image URL to a serve path or base64 data URI.
    Handles full URLs, /media/ paths, and static file paths.
    Mirrors the logic in share_card_utils.process_badge_images().
    """
    if not badge_image_url:
        return ''

    # Full URL: cache as same-origin temp file
    if badge_image_url.startswith(('http://', 'https://')):
        return ShareImageCache.fetch_and_cache(badge_image_url) or ''

    # Media file path
    if badge_image_url.startswith('/media/'):
        from django.conf import settings
        relative_path = badge_image_url[len('/media/'):]
        file_path = settings.MEDIA_ROOT / relative_path
        return ShareImageCache.local_file_to_base64(str(file_path)) or ''

    # Static file path (e.g., images/badges/default.png)
    from django.contrib.staticfiles.finders import find as static_find
    static_path = static_find(badge_image_url)
    if static_path:
        return ShareImageCache.local_file_to_base64(static_path) or ''

    return ''


# ---------------------------------------------------------------------------
# Authenticated endpoints: Social card preview and download
# ---------------------------------------------------------------------------

class ProfileCardHTMLView(APIView):
    """
    GET /api/v1/profile-card/html/?image_format=landscape

    Returns rendered HTML for the profile card share image preview.
    """
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication, TokenAuthentication]

    @method_decorator(ratelimit(key='user', rate='60/m', method='GET', block=True))
    def get(self, request):
        profile = getattr(request.user, 'profile', None)
        if not profile:
            return Response(
                {'error': 'No profile linked to this account'},
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        data = ProfileCardDataService.get_profile_card_data(profile)

        # Cache avatar and badge images for template
        avatar_serve = ShareImageCache.fetch_and_cache(data['avatar_url'])
        badge_image_serve = _resolve_badge_image(data.get('badge_image_url', ''))

        # Cache recent/rarest plat icons
        recent_plat_icon_serve = ''
        if data.get('recent_plat_icon'):
            recent_plat_icon_serve = ShareImageCache.fetch_and_cache(data['recent_plat_icon'])

        rarest_plat_icon_serve = ''
        if data.get('rarest_plat_icon'):
            rarest_plat_icon_serve = ShareImageCache.fetch_and_cache(data['rarest_plat_icon'])

        context = {
            'format': 'landscape',
            **data,
            'avatar_url': avatar_serve or '',
            'badge_image_url': badge_image_serve or '',
            'recent_plat_icon': recent_plat_icon_serve or '',
            'rarest_plat_icon': rarest_plat_icon_serve or '',
        }

        html = render_to_string(
            'shareables/partials/profile_card_landscape.html', context
        )

        return Response({'html': html})


class ProfileCardPNGView(APIView):
    """
    GET /api/v1/profile-card/png/?image_format=landscape&theme=default

    Server-side PNG rendering via Playwright. Returns finished PNG as download.
    """
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication, TokenAuthentication]

    @method_decorator(ratelimit(key='user', rate='20/m', method='GET', block=True))
    def get(self, request):
        profile = getattr(request.user, 'profile', None)
        if not profile:
            return Response(
                {'error': 'No profile linked to this account'},
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        theme_key = request.query_params.get('theme', 'default')

        data = ProfileCardDataService.get_profile_card_data(profile)

        avatar_serve = ShareImageCache.fetch_and_cache(data['avatar_url'])
        badge_image_serve = _resolve_badge_image(data.get('badge_image_url', ''))

        recent_plat_icon_serve = ''
        if data.get('recent_plat_icon'):
            recent_plat_icon_serve = ShareImageCache.fetch_and_cache(data['recent_plat_icon'])

        rarest_plat_icon_serve = ''
        if data.get('rarest_plat_icon'):
            rarest_plat_icon_serve = ShareImageCache.fetch_and_cache(data['rarest_plat_icon'])

        context = {
            'format': 'landscape',
            **data,
            'avatar_url': avatar_serve or '',
            'badge_image_url': badge_image_serve or '',
            'recent_plat_icon': recent_plat_icon_serve or '',
            'rarest_plat_icon': rarest_plat_icon_serve or '',
        }

        html = render_to_string(
            'shareables/partials/profile_card_landscape.html', context
        )

        try:
            from core.services.playwright_renderer import render_png
            png_bytes = render_png(html, format_type='landscape', theme_key=theme_key)
        except Exception as e:
            logger.exception(f"[PROFILE-CARD-PNG] Playwright render failed: {e}")
            return Response(
                {'error': 'Failed to render share image'},
                status=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        username = data.get('psn_username', 'profile')
        safe_name = "".join(
            c for c in username if c.isalnum() or c in (' ', '-', '_')
        ).strip() or 'profile-card'
        filename = f"{safe_name}-profile-card.png"

        response = HttpResponse(png_bytes, content_type='image/png')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response


# ---------------------------------------------------------------------------
# Card settings management
# ---------------------------------------------------------------------------

class ProfileCardSettingsView(APIView):
    """
    POST /api/v1/profile-card/settings/

    Update profile card settings (theme, public sig toggle).
    """
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication, TokenAuthentication]

    def get(self, request):
        """Return current card settings."""
        profile = getattr(request.user, 'profile', None)
        if not profile:
            return Response(
                {'error': 'No profile linked to this account'},
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        settings_obj, _ = ProfileCardSettings.objects.get_or_create(profile=profile)
        return Response({
            'public_sig_enabled': settings_obj.public_sig_enabled,
            'public_sig_token': str(settings_obj.public_sig_token),
            'card_theme': settings_obj.card_theme,
        })

    def post(self, request):
        profile = getattr(request.user, 'profile', None)
        if not profile:
            return Response(
                {'error': 'No profile linked to this account'},
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        settings_obj, _ = ProfileCardSettings.objects.get_or_create(profile=profile)
        update_fields = []

        if 'public_sig_enabled' in request.data:
            settings_obj.public_sig_enabled = bool(request.data['public_sig_enabled'])
            update_fields.append('public_sig_enabled')

            # Trigger initial render when enabling
            if settings_obj.public_sig_enabled:
                try:
                    from core.services.profile_card_renderer import render_all_sigs
                    render_all_sigs(profile)
                except Exception:
                    logger.exception('[PROFILE-CARD] Failed to render sigs on enable')

        if 'card_theme' in request.data:
            theme_key = request.data['card_theme']
            # Validate theme exists
            from trophies.themes import GRADIENT_THEMES
            if theme_key not in GRADIENT_THEMES and theme_key != 'default':
                return Response(
                    {'error': 'Invalid theme'},
                    status=http_status.HTTP_400_BAD_REQUEST,
                )
            settings_obj.card_theme = theme_key
            update_fields.append('card_theme')

        if update_fields:
            settings_obj.save(update_fields=update_fields)

        return Response({
            'public_sig_enabled': settings_obj.public_sig_enabled,
            'public_sig_token': str(settings_obj.public_sig_token),
            'card_theme': settings_obj.card_theme,
        })


class ProfileCardRegenerateTokenView(APIView):
    """
    POST /api/v1/profile-card/regenerate-token/

    Regenerate the public sig token, invalidating all existing embed URLs.
    """
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication, TokenAuthentication]

    def post(self, request):
        profile = getattr(request.user, 'profile', None)
        if not profile:
            return Response(
                {'error': 'No profile linked to this account'},
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        settings_obj, _ = ProfileCardSettings.objects.get_or_create(profile=profile)

        # Delete old sig files
        from core.services.profile_card_renderer import PROFILE_SIGS_DIR
        old_token = str(settings_obj.public_sig_token)
        for ext in ('png', 'svg'):
            old_file = PROFILE_SIGS_DIR / f"{old_token}.{ext}"
            old_file.unlink(missing_ok=True)

        # Regenerate
        settings_obj.regenerate_token()

        # Re-render with new token if enabled
        if settings_obj.public_sig_enabled:
            try:
                from core.services.profile_card_renderer import render_all_sigs
                render_all_sigs(profile)
            except Exception:
                logger.exception('[PROFILE-CARD] Failed to render sigs after token regen')

        return Response({
            'public_sig_token': str(settings_obj.public_sig_token),
            'message': 'Token regenerated. Old embed URLs are now invalid.',
        })


# ---------------------------------------------------------------------------
# Displayed badge selection
# ---------------------------------------------------------------------------

class SetDisplayedBadgeView(APIView):
    """
    POST /api/v1/badges/displayed/

    Set the user's displayed badge. Pass {"badge_id": <id>} to select,
    or {"badge_id": null} to clear.
    """
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication, TokenAuthentication]

    def post(self, request):
        from trophies.models import UserBadge, Badge
        from trophies.services.dashboard_service import invalidate_dashboard_cache

        profile = getattr(request.user, 'profile', None)
        if not profile:
            return Response(
                {'error': 'No profile linked to this account'},
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        badge_id = request.data.get('badge_id')

        # Clear all displayed badges first
        UserBadge.objects.filter(
            profile=profile, is_displayed=True,
        ).update(is_displayed=False)

        if badge_id is not None:
            try:
                badge_id = int(badge_id)
            except (ValueError, TypeError):
                return Response(
                    {'error': 'Invalid badge_id'},
                    status=http_status.HTTP_400_BAD_REQUEST,
                )

            # Verify user has earned this badge
            updated = UserBadge.objects.filter(
                profile=profile, badge_id=badge_id,
            ).update(is_displayed=True)

            if not updated:
                return Response(
                    {'error': 'Badge not found or not earned'},
                    status=http_status.HTTP_404_NOT_FOUND,
                )

        # Invalidate dashboard cache
        try:
            invalidate_dashboard_cache(profile.pk)
        except Exception:
            pass

        return Response({'status': 'ok'})


class ToggleShowcaseBadgeView(APIView):
    """
    POST /api/v1/badges/showcase/

    Toggle a badge in/out of the profile showcase (max 3, premium only).
    Send {"badge_id": <id>} to toggle.
    Returns the current showcase state.
    """
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication, TokenAuthentication]

    def post(self, request):
        from trophies.models import UserBadge, ProfileBadgeShowcase
        from trophies.services.dashboard_service import invalidate_dashboard_cache

        from trophies.services.dashboard_service import get_effective_premium

        profile = getattr(request.user, 'profile', None)
        if not profile:
            return Response({'error': 'No profile linked.'}, status=http_status.HTTP_400_BAD_REQUEST)

        if not get_effective_premium(request):
            return Response({'error': 'Premium subscription required.'}, status=http_status.HTTP_403_FORBIDDEN)

        badge_id = request.data.get('badge_id')
        try:
            badge_id = int(badge_id)
        except (ValueError, TypeError):
            return Response({'error': 'Invalid badge_id.'}, status=http_status.HTTP_400_BAD_REQUEST)

        # Verify earned + has custom artwork
        ub = (
            UserBadge.objects
            .filter(profile=profile, badge_id=badge_id)
            .select_related('badge', 'badge__base_badge')
            .first()
        )
        if not ub:
            return Response({'error': 'Badge not earned.'}, status=http_status.HTTP_404_NOT_FOUND)

        try:
            layers = ub.badge.get_badge_layers()
            if not layers.get('has_custom_image'):
                return Response({'error': 'Badge must have custom artwork.'}, status=http_status.HTTP_400_BAD_REQUEST)
        except Exception:
            return Response({'error': 'Unable to verify badge artwork.'}, status=http_status.HTTP_400_BAD_REQUEST)

        # Toggle within a transaction to prevent race conditions
        from django.db import transaction
        with transaction.atomic():
            existing = ProfileBadgeShowcase.objects.filter(profile=profile, badge_id=badge_id)
            if existing.exists():
                existing.delete()
                # Reorder remaining to close gaps
                remaining = list(
                    ProfileBadgeShowcase.objects.filter(profile=profile)
                    .select_for_update()
                    .order_by('display_order')
                )
                for i, item in enumerate(remaining, 1):
                    if item.display_order != i:
                        item.display_order = i
                        item.save(update_fields=['display_order'])
                action = 'removed'
            else:
                try:
                    ProfileBadgeShowcase.objects.create(profile=profile, badge_id=badge_id)
                    action = 'added'
                except ValueError:
                    return Response(
                        {'error': 'Maximum 5 showcase badges allowed.'},
                        status=http_status.HTTP_400_BAD_REQUEST,
                    )

        try:
            invalidate_dashboard_cache(profile.pk)
        except Exception:
            pass

        showcase_ids = list(
            ProfileBadgeShowcase.objects.filter(profile=profile)
            .order_by('display_order')
            .values_list('badge_id', flat=True)
        )
        return Response({'status': 'ok', 'action': action, 'showcase_badge_ids': showcase_ids})


class ReorderShowcaseBadgesView(APIView):
    """
    POST /api/v1/badges/showcase/reorder/

    Persist a new display order for the user's profile badge showcase.
    Send {"badge_ids": [<id>, <id>, ...]} with the new order. All ids must
    already belong to the user's showcase. Premium only.
    """
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication, TokenAuthentication]

    def post(self, request):
        from trophies.models import ProfileBadgeShowcase
        from trophies.services.dashboard_service import (
            invalidate_dashboard_cache, get_effective_premium,
        )
        from django.db import transaction

        profile = getattr(request.user, 'profile', None)
        if not profile:
            return Response({'error': 'No profile linked.'}, status=http_status.HTTP_400_BAD_REQUEST)

        if not get_effective_premium(request):
            return Response({'error': 'Premium subscription required.'}, status=http_status.HTTP_403_FORBIDDEN)

        badge_ids = request.data.get('badge_ids')
        if not isinstance(badge_ids, list) or not all(isinstance(b, int) for b in badge_ids):
            return Response(
                {'error': 'badge_ids must be a list of integers.'},
                status=http_status.HTTP_400_BAD_REQUEST,
            )
        if len(badge_ids) > 5:
            return Response(
                {'error': 'Cannot reorder more than 5 badges.'},
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            existing = list(
                ProfileBadgeShowcase.objects
                .select_for_update()
                .filter(profile=profile)
            )
            existing_ids = {sc.badge_id for sc in existing}
            if set(badge_ids) != existing_ids:
                return Response(
                    {'error': 'badge_ids must exactly match your current showcase.'},
                    status=http_status.HTTP_400_BAD_REQUEST,
                )

            for i, bid in enumerate(badge_ids, 1):
                ProfileBadgeShowcase.objects.filter(profile=profile, badge_id=bid).update(display_order=i)

        try:
            invalidate_dashboard_cache(profile.pk)
        except Exception:
            pass

        return Response({'status': 'ok', 'showcase_badge_ids': badge_ids})


# ---------------------------------------------------------------------------
# Public forum signature serving (no auth)
# ---------------------------------------------------------------------------

@ratelimit(key='ip', rate='120/m', method='GET', block=True)
def serve_profile_sig(request, token, ext):
    """
    GET /sig/<uuid:token>.png
    GET /sig/<uuid:token>.svg

    Serve pre-rendered forum signature images. No authentication required.
    """
    token_str = str(token)

    # Validate extension
    if ext not in ('png', 'svg'):
        raise Http404

    # Verify token exists and sig is enabled
    try:
        card_settings = ProfileCardSettings.objects.get(
            public_sig_token=token,
            public_sig_enabled=True,
        )
    except ProfileCardSettings.DoesNotExist:
        raise Http404

    # Serve the file
    from core.services.profile_card_renderer import PROFILE_SIGS_DIR
    file_path = PROFILE_SIGS_DIR / f"{token_str}.{ext}"

    if not file_path.exists():
        # File missing but sig is enabled: try rendering on the fly (SVG only, PNG too expensive)
        if ext == 'svg':
            try:
                from core.services.profile_card_renderer import render_sig_svg
                render_sig_svg(card_settings.profile)
            except Exception:
                logger.exception(f"[SIG-SERVE] On-demand SVG render failed for token {token_str}")
                raise Http404
            if not file_path.exists():
                raise Http404
        else:
            raise Http404

    content_type = 'image/png' if ext == 'png' else 'image/svg+xml'

    # FileResponse closes the file handle automatically after streaming
    fh = file_path.open('rb')
    response = FileResponse(fh, content_type=content_type)
    response['Cache-Control'] = 'public, max-age=3600'
    response['X-Content-Type-Options'] = 'nosniff'
    return response
