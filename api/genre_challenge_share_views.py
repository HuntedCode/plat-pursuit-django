"""
REST API views for Genre Challenge share card images.
Provides HTML preview and PNG download endpoints.
"""
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status as http_status
from rest_framework.permissions import IsAuthenticated
from rest_framework.authentication import SessionAuthentication, TokenAuthentication
from django.template.loader import render_to_string
from django.http import HttpResponse
from django.utils.decorators import method_decorator
from django_ratelimit.decorators import ratelimit
from concurrent.futures import ThreadPoolExecutor, as_completed
from core.services.share_image_cache import ShareImageCache
from core.services.tracking import track_site_event
from trophies.models import Challenge, Game, ProfileGame
from trophies.services.challenge_service import get_subgenre_status
from trophies.util_modules.constants import (
    GENRE_CHALLENGE_SUBGENRES, SUBGENRE_DISPLAY_NAMES,
)
import logging

logger = logging.getLogger(__name__)

TEMPLATE = 'trophies/partials/genre_challenge_share_card.html'

# Abbreviated genre labels for share card cells
GENRE_SHARE_LABELS = {
    'ACTION': 'Action',
    'ADVENTURE': 'Advntr',
    'ARCADE': 'Arcade',
    'CASUAL': 'Casual',
    'FAMILY': 'Family',
    'FIGHTING': 'Fight',
    'HORROR': 'Horror',
    'MUSIC_RHYTHM': 'M/Rhy',
    'PUZZLE': 'Puzzle',
    'RACING': 'Racing',
    'ROLE_PLAYING_GAMES': 'RPG',
    'SHOOTER': 'Shoot',
    'SIMULATION': 'Sim',
    'SPORTS': 'Sports',
    'STRATEGY': 'Strat',
    'UNIQUE': 'Unique',
}


def _get_challenge_or_error(request, challenge_id):
    """
    Fetch challenge with ownership validation.
    Returns (challenge, None) on success or (None, Response) on error.
    """
    try:
        challenge = Challenge.objects.select_related('profile').get(
            id=challenge_id,
            challenge_type='genre',
        )
    except Challenge.DoesNotExist:
        return None, Response(
            {'error': 'Challenge not found'},
            status=http_status.HTTP_404_NOT_FOUND,
        )

    profile = getattr(request.user, 'profile', None)
    if not profile or challenge.profile_id != profile.id:
        return None, Response(
            {'error': 'You can only generate share cards for your own challenges'},
            status=http_status.HTTP_403_FORBIDDEN,
        )

    return challenge, None


def _build_template_context(challenge, format_type):
    """
    Build the template context for the Genre Challenge share card.

    Uses concurrent image fetching to cache all concept icons + avatar
    in parallel rather than sequentially.
    """
    slots = list(challenge.genre_slots.select_related('concept').order_by('genre'))

    # Batch-fetch trophy progress for assigned concepts (via their games)
    concept_ids = [s.concept_id for s in slots if s.concept_id]
    progress_map = {}
    if concept_ids:
        # Map concept -> game IDs
        concept_game_ids = {}
        for game_id, c_id in Game.objects.filter(
            concept_id__in=concept_ids
        ).values_list('id', 'concept_id'):
            concept_game_ids.setdefault(c_id, []).append(game_id)

        all_game_ids = []
        for gids in concept_game_ids.values():
            all_game_ids.extend(gids)

        if all_game_ids:
            pg_data = {}
            for pg in ProfileGame.objects.filter(
                profile_id=challenge.profile_id, game_id__in=all_game_ids,
            ).values('game_id', 'progress'):
                pg_data[pg['game_id']] = pg['progress'] or 0

            # For each concept, use best progress across its games
            for c_id, gids in concept_game_ids.items():
                best = 0
                for gid in gids:
                    p = pg_data.get(gid, 0)
                    if p > best:
                        best = p
                if best > 0:
                    progress_map[c_id] = best

    # Collect all image URLs to fetch concurrently
    urls_to_cache = {}
    for slot in slots:
        if slot.concept and slot.concept.concept_icon_url:
            urls_to_cache[f'slot_{slot.genre}'] = slot.concept.concept_icon_url
    if challenge.profile.avatar_url:
        urls_to_cache['avatar'] = challenge.profile.avatar_url

    # Fetch all images in parallel
    cached_results = {}
    if urls_to_cache:
        with ThreadPoolExecutor(max_workers=min(len(urls_to_cache), 8)) as executor:
            future_to_key = {
                executor.submit(ShareImageCache.fetch_and_cache, url): key
                for key, url in urls_to_cache.items()
            }
            for future in as_completed(future_to_key):
                key = future_to_key[future]
                try:
                    cached_results[key] = future.result()
                except Exception:
                    logger.warning(f"Failed to cache image for {key}", exc_info=True)
                    cached_results[key] = ''

    # Build slot data
    slot_data = []
    for slot in slots:
        if slot.is_completed:
            state = 'completed'
        elif slot.concept_id:
            state = 'assigned'
        else:
            state = 'empty'

        slot_data.append({
            'genre': slot.genre,
            'genre_label': GENRE_SHARE_LABELS.get(slot.genre, slot.genre),
            'state': state,
            'game_icon': cached_results.get(f'slot_{slot.genre}', ''),
            'progress': progress_map.get(slot.concept_id, 0) if slot.concept_id else 0,
            'game_name': slot.concept.unified_title if slot.concept else '',
        })

    # Build subgenre status
    subgenre_status = get_subgenre_status(challenge)
    subgenre_data = [
        {
            'display': SUBGENRE_DISPLAY_NAMES.get(sg, sg),
            'status': subgenre_status.get(sg, 'uncollected'),
        }
        for sg in GENRE_CHALLENGE_SUBGENRES
    ]

    avatar_url = cached_results.get('avatar', '')

    # Group slots into rows of 4 for the 4x4 grid template
    slot_rows = [slot_data[i:i + 4] for i in range(0, len(slot_data), 4)]

    context = {
        'format': format_type,
        'challenge_name': challenge.name,
        'username': challenge.profile.psn_username,
        'avatar_url': avatar_url,
        'completed_count': challenge.completed_count,
        'total_items': challenge.total_items,
        'progress_percentage': challenge.progress_percentage,
        'is_complete': challenge.is_complete,
        'completed_at': (
            challenge.completed_at.strftime('%b %d, %Y')
            if challenge.completed_at else ''
        ),
        'subgenre_count': challenge.subgenre_count,
        'subgenre_total': len(GENRE_CHALLENGE_SUBGENRES),
        'slot_rows': slot_rows,
        'subgenres': subgenre_data,
    }

    return context


class GenreChallengeShareHTMLView(APIView):
    """
    GET /api/v1/challenges/genre/<challenge_id>/share/html/

    Returns rendered HTML for Genre Challenge share card preview.
    Query params: image_format=landscape|portrait
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

        context = _build_template_context(challenge, format_type)
        html = render_to_string(TEMPLATE, context)

        return Response({'html': html})


class GenreChallengeSharePNGView(APIView):
    """
    GET /api/v1/challenges/genre/<challenge_id>/share/png/

    Server-side PNG rendering via Playwright. Returns the finished PNG as a download.
    Query params: image_format, theme
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

        context = _build_template_context(challenge, format_type)
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
                f"[GENRE-SHARE-PNG] Playwright render failed for challenge {challenge_id}: {e}"
            )
            return Response(
                {'error': 'Failed to render share image'},
                status=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # Track the download
        track_site_event('genre_challenge_share_download', str(challenge_id), request)

        safe_name = "".join(
            c for c in challenge.name if c.isalnum() or c in (' ', '-', '_')
        ).strip() or 'challenge'
        filename = f"genre-challenge-{safe_name}-{format_type}.png"

        response = HttpResponse(png_bytes, content_type='image/png')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response
