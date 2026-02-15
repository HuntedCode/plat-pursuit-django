"""
REST API views for A-Z Challenge share card images.
Provides HTML preview and PNG download endpoints.
"""
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status as http_status
from rest_framework.permissions import IsAuthenticated
from rest_framework.authentication import SessionAuthentication, TokenAuthentication
from django.core.cache import cache
from django.template.loader import render_to_string
from django.http import HttpResponse
from django.utils.decorators import method_decorator
from django_ratelimit.decorators import ratelimit
from concurrent.futures import ThreadPoolExecutor, as_completed
from core.services.share_image_cache import ShareImageCache
from core.services.tracking import track_site_event
from trophies.models import Challenge, ProfileGame
import base64
import logging

logger = logging.getLogger(__name__)

TEMPLATE = 'trophies/partials/az_challenge_share_card.html'


def _get_challenge_or_error(request, challenge_id):
    """
    Fetch challenge with ownership validation.
    Returns (challenge, None) on success or (None, Response) on error.
    """
    try:
        challenge = Challenge.objects.select_related('profile').get(
            id=challenge_id,
            challenge_type='az',
        )
    except Challenge.DoesNotExist:
        return None, Response(
            {'error': 'Challenge not found'},
            status=http_status.HTTP_404_NOT_FOUND,
        )

    if challenge.profile_id != request.user.profile.id:
        return None, Response(
            {'error': 'You can only generate share cards for your own challenges'},
            status=http_status.HTTP_403_FORBIDDEN,
        )

    return challenge, None


def _build_template_context(challenge, format_type, featured_letter=None):
    """
    Build the template context for the A-Z challenge share card.
    Returns (context_dict, in_progress_slots_list).

    Uses concurrent image fetching to cache all game icons + avatar
    in parallel rather than sequentially.
    """
    slots = list(challenge.az_slots.select_related('game').order_by('letter'))

    # Batch-fetch trophy progress for assigned games
    game_ids = [s.game_id for s in slots if s.game_id]
    progress_map = {}
    if game_ids:
        pg_qs = ProfileGame.objects.filter(
            profile_id=challenge.profile_id,
            game_id__in=game_ids,
        ).values('game_id', 'progress')
        for pg in pg_qs:
            progress_map[pg['game_id']] = pg['progress'] or 0

    # Collect all image URLs to fetch concurrently
    urls_to_cache = {}
    for slot in slots:
        if slot.game:
            icon_url = slot.game.title_icon_url or slot.game.title_image or ''
            if icon_url:
                urls_to_cache[f'slot_{slot.letter}'] = icon_url
    if challenge.profile.avatar_url:
        urls_to_cache['avatar'] = challenge.profile.avatar_url

    # Fetch all images in parallel
    cached_results = {}
    if urls_to_cache:
        with ThreadPoolExecutor(max_workers=10) as executor:
            future_to_key = {
                executor.submit(ShareImageCache.fetch_and_cache, url): key
                for key, url in urls_to_cache.items()
            }
            for future in as_completed(future_to_key):
                key = future_to_key[future]
                try:
                    cached_results[key] = future.result()
                except Exception:
                    cached_results[key] = ''

    # Build slot data using pre-fetched cached icons
    slot_data = []
    in_progress_slots = []

    for slot in slots:
        if slot.is_completed:
            state = 'completed'
        elif slot.game_id:
            state = 'assigned'
        else:
            state = 'empty'

        entry = {
            'letter': slot.letter,
            'state': state,
            'game_icon': cached_results.get(f'slot_{slot.letter}', ''),
            'progress': 0,
        }

        if slot.game:
            entry['progress'] = progress_map.get(slot.game_id, 0)

            if state == 'assigned':
                in_progress_slots.append({
                    'letter': slot.letter,
                    'game_name': slot.game.title_name,
                    'progress': entry['progress'],
                    'game_icon': entry['game_icon'],
                })

        slot_data.append(entry)

    # Featured game logic
    featured_game_name = ''
    featured_game_icon = ''
    featured_progress = 0
    resolved_featured_letter = ''

    if not challenge.is_complete:
        chosen = None
        if featured_letter:
            chosen = next(
                (s for s in in_progress_slots if s['letter'] == featured_letter.upper()),
                None,
            )

        if not chosen and in_progress_slots:
            chosen = max(in_progress_slots, key=lambda s: s['progress'])

        if chosen:
            featured_game_name = chosen['game_name']
            featured_game_icon = chosen['game_icon']
            featured_progress = chosen['progress']
            resolved_featured_letter = chosen['letter']

    avatar_url = cached_results.get('avatar', '')

    context = {
        'format': format_type,
        'challenge_name': challenge.name,
        'username': challenge.profile.psn_username,
        'avatar_url': avatar_url,
        'completed_count': challenge.completed_count,
        'progress_percentage': challenge.progress_percentage,
        'is_complete': challenge.is_complete,
        'completed_at': (
            challenge.completed_at.strftime('%b %d, %Y')
            if challenge.completed_at else ''
        ),
        'slots': slot_data,
        'featured_game_name': featured_game_name,
        'featured_game_icon': featured_game_icon,
        'featured_progress': featured_progress,
        'featured_letter': resolved_featured_letter,
    }

    return context, in_progress_slots


class AZChallengeShareHTMLView(APIView):
    """
    GET /api/v1/challenges/az/<challenge_id>/share/html/

    Returns rendered HTML for A-Z challenge share card preview.
    Query params: image_format=landscape|portrait, featured_letter=A-Z
    """
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication, TokenAuthentication]

    @method_decorator(ratelimit(key='user', rate='60/m', method='GET', block=True))
    def get(self, request, challenge_id):
        challenge, error = _get_challenge_or_error(request, challenge_id)
        if error:
            return error

        format_type = request.query_params.get('image_format', 'landscape')
        if format_type not in ('landscape', 'portrait'):
            return Response(
                {'error': 'Invalid format. Must be landscape or portrait'},
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        featured_letter = request.query_params.get('featured_letter', '')

        context, in_progress_slots = _build_template_context(
            challenge, format_type, featured_letter,
        )

        html = render_to_string(TEMPLATE, context)

        return Response({
            'html': html,
            'in_progress_slots': in_progress_slots,
            'featured_letter': context['featured_letter'],
        })


class AZChallengeSharePNGView(APIView):
    """
    GET /api/v1/challenges/az/<challenge_id>/share/png/

    Server-side PNG rendering via Playwright. Returns the finished PNG as a download.
    Query params: image_format, theme, featured_letter
    """
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication, TokenAuthentication]

    @method_decorator(ratelimit(key='user', rate='20/m', method='GET', block=True))
    def get(self, request, challenge_id):
        challenge, error = _get_challenge_or_error(request, challenge_id)
        if error:
            return error

        format_type = request.query_params.get('image_format', 'landscape')
        if format_type not in ('landscape', 'portrait'):
            return Response(
                {'error': 'Invalid format. Must be landscape or portrait'},
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        theme_key = request.query_params.get('theme', 'default')
        featured_letter = request.query_params.get('featured_letter', '')

        # Check Redis cache for a previously rendered PNG
        # PNG bytes are base64-encoded for JSON serializer compatibility
        cache_key = f"az_share_png:{challenge_id}:{format_type}:{theme_key}:{featured_letter}"
        cached = cache.get(cache_key)
        png_bytes = base64.b64decode(cached) if cached else None

        if not png_bytes:
            context, _ = _build_template_context(
                challenge, format_type, featured_letter,
            )

            html = render_to_string(TEMPLATE, context)

            try:
                from core.services.playwright_renderer import render_png
                png_bytes = render_png(
                    html,
                    format_type=format_type,
                    theme_key=theme_key,
                )
            except Exception as e:
                logger.exception(
                    f"[AZ-SHARE-PNG] Playwright render failed for challenge {challenge_id}: {e}"
                )
                return Response(
                    {'error': 'Failed to render share image'},
                    status=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

            # Cache the rendered PNG for 10 minutes (base64-encoded for JSON serializer)
            cache.set(cache_key, base64.b64encode(png_bytes).decode('ascii'), timeout=600)

        # Track the download
        track_site_event('az_challenge_share_download', str(challenge_id), request)

        safe_name = "".join(
            c for c in challenge.name if c.isalnum() or c in (' ', '-', '_')
        ).strip()
        filename = f"az-challenge-{safe_name}-{format_type}.png"

        response = HttpResponse(png_bytes, content_type='image/png')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response
