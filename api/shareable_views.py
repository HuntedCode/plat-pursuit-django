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
from django.shortcuts import get_object_or_404
from django.utils.decorators import method_decorator
from django_ratelimit.decorators import ratelimit
from core.services.tracking import track_site_event
from trophies.models import EarnedTrophy
from notifications.services.shareable_data_service import ShareableDataService
from core.services.share_image_cache import ShareImageCache
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
        earned_trophy = get_object_or_404(
            EarnedTrophy.objects.select_related('trophy__game__concept', 'profile'),
            id=earned_trophy_id,
            profile=request.user.profile,
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
        badge_xp = self._to_int(metadata.get('badge_xp', 0))
        tier1_badges = metadata.get('tier1_badges', [])

        # Process badge images - convert to base64 or use default
        processed_badges = self._process_badge_images(tier1_badges)

        # Format date strings for display
        first_played_date_time = self._format_date(metadata.get('first_played_date_time'))
        earned_date_time = self._format_date(metadata.get('earned_date_time'))

        return {
            'format': format_type,
            'game_name': metadata.get('game_name', 'Unknown Game'),
            'username': metadata.get('username', 'Player'),
            'total_plats': self._to_int(metadata.get('user_total_platinums', 0)),
            'progress': self._to_int(metadata.get('progress_percentage', 0)),
            'earned_trophies': self._to_int(metadata.get('earned_trophies_count', 0)),
            'total_trophies': self._to_int(metadata.get('total_trophies_count', 0)),
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
            'yearly_plats': self._to_int(metadata.get('yearly_plats', 0)),
            'earned_year': self._to_int(metadata.get('earned_year', 0)),
            'badge_xp': badge_xp,
            'tier1_badges': processed_badges,
            'user_rating': metadata.get('user_rating'),
        }

    @staticmethod
    def _to_int(value, default=0):
        """Safely convert value to int."""
        if value is None:
            return default
        try:
            return int(value)
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _format_date(iso_string):
        """Format an ISO date string to a readable format."""
        if not iso_string:
            return ''
        try:
            from datetime import datetime
            if 'T' in iso_string:
                dt = datetime.fromisoformat(iso_string.replace('Z', '+00:00'))
            else:
                dt = datetime.fromisoformat(iso_string)
            return dt.strftime('%b %d, %Y')
        except (ValueError, TypeError):
            return ''

    def _process_badge_images(self, badges):
        """
        Process badge images for share image rendering.
        Converts badge image URLs to same-origin temp files or base64 data URLs.
        """
        if not badges:
            return []

        processed = []
        default_badge_image = None

        for badge in badges:
            badge_copy = dict(badge)
            badge_image_url = badge_copy.get('badge_image_url', '')

            if badge_image_url:
                if badge_image_url.startswith(('http://', 'https://')):
                    cached_url = ShareImageCache.fetch_and_cache(badge_image_url)
                    if cached_url:
                        badge_copy['badge_image_url'] = cached_url
                elif badge_image_url.startswith('/media/'):
                    from django.conf import settings
                    relative_path = badge_image_url[len('/media/'):]
                    file_path = settings.MEDIA_ROOT / relative_path
                    data_uri = ShareImageCache.local_file_to_base64(str(file_path))
                    if data_uri:
                        badge_copy['badge_image_url'] = data_uri
                else:
                    if default_badge_image is None:
                        from django.contrib.staticfiles import finders
                        default_path = finders.find(badge_image_url)
                        if default_path:
                            default_badge_image = ShareImageCache.local_file_to_base64(default_path)
                        else:
                            default_badge_image = ''
                    badge_copy['badge_image_url'] = default_badge_image
            else:
                if default_badge_image is None:
                    from django.contrib.staticfiles import finders
                    default_path = finders.find('images/badges/default.png')
                    if default_path:
                        default_badge_image = ShareImageCache.local_file_to_base64(default_path)
                    else:
                        default_badge_image = ''
                badge_copy['badge_image_url'] = default_badge_image

            processed.append(badge_copy)

        return processed

