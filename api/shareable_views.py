"""
REST API views for shareable images.
Provides endpoints to generate share images directly from EarnedTrophy records.
"""
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status as http_status
from rest_framework.permissions import IsAuthenticated
from rest_framework.authentication import SessionAuthentication, TokenAuthentication
from django.template.loader import render_to_string
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils.decorators import method_decorator
from django_ratelimit.decorators import ratelimit
from core.services.tracking import track_site_event
from trophies.models import EarnedTrophy
from notifications.services.shareable_data_service import ShareableDataService
from core.services.share_image_cache import ShareImageCache
from core.services.share_card_utils import to_int, format_share_date, process_badge_images, resolve_temp_path
import logging

logger = logging.getLogger(__name__)


class ShareableImageHTMLView(APIView):
    """
    GET /api/v1/shareables/platinum/<earned_trophy_id>/html/

    Returns rendered HTML for share image card - works directly with EarnedTrophy.
    Query params: image_format=landscape|portrait

    Returns: { "html": "<rendered html>" }
    """
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication, TokenAuthentication]

    @method_decorator(ratelimit(key='user', rate='60/m', method='GET', block=True))
    def get(self, request, earned_trophy_id):
        logger.info(f"[SHAREABLE-HTML] Request received for earned_trophy {earned_trophy_id}")

        # Get the earned trophy and validate ownership
        profile = getattr(request.user, 'profile', None)
        if not profile:
            return Response(
                {'error': 'No profile linked to this account'},
                status=http_status.HTTP_400_BAD_REQUEST,
            )
        earned_trophy = get_object_or_404(
            EarnedTrophy.objects.select_related('trophy__game__concept', 'profile'),
            id=earned_trophy_id,
            profile=profile,
            earned=True,
            trophy__trophy_type='platinum'
        )

        # Get format from query params
        format_type = request.query_params.get('image_format', 'landscape')
        if format_type not in ['landscape', 'portrait']:
            return Response(
                {'error': 'Invalid format. Must be landscape or portrait'},
                status=http_status.HTTP_400_BAD_REQUEST
            )

        # Get share data using centralized service
        metadata = ShareableDataService.get_platinum_share_data(earned_trophy)

        # Build template context (matching NotificationShareImageHTMLView pattern)
        context = self._build_template_context(metadata, format_type)

        # Render the template
        html = render_to_string('notifications/partials/share_image_card.html', context)

        # Cache background images as same-origin temp files for JS game art themes
        game_image_url = metadata.get('game_image', '')
        concept_bg_url = metadata.get('concept_bg_url', '')

        response_data = {'html': html}

        # Include same-origin URLs for background images
        if game_image_url:
            game_image_cached = ShareImageCache.fetch_and_cache(game_image_url)
            if game_image_cached:
                response_data['game_image_base64'] = game_image_cached

        if concept_bg_url:
            concept_bg_cached = ShareImageCache.fetch_and_cache(concept_bg_url)
            if concept_bg_cached:
                response_data['concept_bg_base64'] = concept_bg_cached

        return Response(response_data)

    def _build_template_context(self, metadata, format_type):
        """Build the context dict for the share image template."""
        # Calculate playtime string
        playtime = ''
        play_duration_seconds = metadata.get('play_duration_seconds')
        if play_duration_seconds:
            try:
                seconds = float(play_duration_seconds)
                hours = int(seconds // 3600)
                minutes = int((seconds % 3600) // 60)
                if hours > 0:
                    playtime = f"{hours}h {minutes}m"
                else:
                    playtime = f"{minutes}m"
            except (ValueError, TypeError):
                pass

        # Format earn rate
        earn_rate = metadata.get('trophy_earn_rate')
        if earn_rate:
            try:
                earn_rate = round(float(earn_rate), 2)
            except (ValueError, TypeError):
                earn_rate = None

        # Cache external images as same-origin temp files (fixes iOS Safari intermittent failures with data URIs)
        game_image_url = metadata.get('game_image', '')
        trophy_icon_url = metadata.get('trophy_icon_url', '')

        game_image_data = ShareImageCache.fetch_and_cache(game_image_url)
        if game_image_url and not game_image_data:
            logger.warning(f"[SHARE] Failed to cache game image: {game_image_url}")

        trophy_icon_data = ShareImageCache.fetch_and_cache(trophy_icon_url)
        if trophy_icon_url and not trophy_icon_data:
            logger.warning(f"[SHARE] Failed to cache trophy icon: {trophy_icon_url}")

        # Extract badge data
        badge_xp = to_int(metadata.get('badge_xp', 0))
        tier1_badges = metadata.get('tier1_badges', [])

        # Process badge images - convert to base64 or use default
        processed_badges = process_badge_images(tier1_badges)

        # Format date strings for display
        first_played_date_time = format_share_date(metadata.get('first_played_date_time'))
        earned_date_time = format_share_date(metadata.get('earned_date_time'))

        return {
            'format': format_type,
            'game_name': metadata.get('game_name', 'Unknown Game'),
            'username': metadata.get('username', 'Player'),
            'total_plats': to_int(metadata.get('user_total_platinums', 0)),
            'progress': to_int(metadata.get('progress_percentage', 0)),
            'earned_trophies': to_int(metadata.get('earned_trophies_count', 0)),
            'total_trophies': to_int(metadata.get('total_trophies_count', 0)),
            'game_image': game_image_data,
            'trophy_icon': trophy_icon_data,
            'rarity_label': metadata.get('rarity_label', ''),
            'earn_rate': earn_rate,
            'playtime': playtime,
            'title_platform': metadata.get('title_platform', []),
            'region': metadata.get('region', []),
            'is_regional': metadata.get('is_regional', False),
            'first_played_date_time': first_played_date_time,
            'earned_date_time': earned_date_time,
            'yearly_plats': to_int(metadata.get('yearly_plats', 0)),
            'earned_year': to_int(metadata.get('earned_year', 0)),
            'badge_xp': badge_xp,
            'tier1_badges': processed_badges,
            'user_rating': metadata.get('user_rating'),
        }



class ShareableImagePNGView(APIView):
    """
    GET /api/v1/shareables/platinum/<earned_trophy_id>/png/?image_format=landscape&theme=default

    Server-side PNG rendering via Playwright. Returns the finished PNG as a download.
    """
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication, TokenAuthentication]

    @method_decorator(ratelimit(key='user', rate='20/m', method='GET', block=True))
    def get(self, request, earned_trophy_id):
        profile = getattr(request.user, 'profile', None)
        if not profile:
            return Response(
                {'error': 'No profile linked to this account'},
                status=http_status.HTTP_400_BAD_REQUEST,
            )
        earned_trophy = get_object_or_404(
            EarnedTrophy.objects.select_related('trophy__game__concept', 'profile'),
            id=earned_trophy_id,
            profile=profile,
            earned=True,
            trophy__trophy_type='platinum'
        )

        format_type = request.query_params.get('image_format', 'landscape')
        if format_type not in ['landscape', 'portrait']:
            return Response(
                {'error': 'Invalid format. Must be landscape or portrait'},
                status=http_status.HTTP_400_BAD_REQUEST
            )

        theme_key = request.query_params.get('theme', 'default')

        # Reuse existing HTML view's context builder
        html_view = ShareableImageHTMLView()
        metadata = ShareableDataService.get_platinum_share_data(earned_trophy)
        context = html_view._build_template_context(metadata, format_type)

        html = render_to_string('notifications/partials/share_image_card.html', context)

        # Use the cached image paths from the context (already fetched by _build_template_context)
        game_image_path = resolve_temp_path(context.get('game_image', ''))

        # Concept bg is not in the template context: cache it separately for game art themes
        concept_bg_url = metadata.get('concept_bg_url', '')
        concept_bg_cached = ShareImageCache.fetch_and_cache(concept_bg_url) if concept_bg_url else ''
        concept_bg_path = resolve_temp_path(concept_bg_cached)

        try:
            from core.services.playwright_renderer import render_png
            png_bytes = render_png(
                html,
                format_type=format_type,
                theme_key=theme_key,
                game_image_path=game_image_path,
                concept_bg_path=concept_bg_path,
            )
        except Exception as e:
            logger.exception(f"[SHARE-PNG] Playwright render failed for shareable {earned_trophy_id}: {e}")
            return Response(
                {'error': 'Failed to render share image'},
                status=http_status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        game_name = earned_trophy.trophy.game.title_name or 'share-card'
        safe_name = "".join(c for c in game_name if c.isalnum() or c in (' ', '-', '_')).strip() or 'share-card'
        filename = f"{safe_name}-{format_type}.png"

        response = HttpResponse(png_bytes, content_type='image/png')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response


