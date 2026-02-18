"""
REST API views for Platinum Calendar Challenge share card images.
Provides HTML preview, PNG download, and game background search endpoints.
"""
import base64
import logging
import os
import tempfile

import requests as http_requests
from django.core.cache import cache
from django.db.models import Q
from django.db.models.functions import Lower
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.utils.decorators import method_decorator
from django_ratelimit.decorators import ratelimit
from rest_framework import status as http_status
from rest_framework.authentication import SessionAuthentication, TokenAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.services.share_image_cache import ShareImageCache
from core.services.tracking import track_site_event
from trophies.models import Challenge, Concept, ProfileGame
from trophies.services.challenge_service import get_calendar_month_data, get_calendar_stats
from trophies.services.holiday_service import get_holidays_color_map

logger = logging.getLogger(__name__)

TEMPLATE = 'trophies/partials/calendar_challenge_share_card.html'


def _get_calendar_challenge_or_error(request, challenge_id):
    """
    Fetch calendar challenge with ownership validation.
    Returns (challenge, None) on success or (None, Response) on error.
    """
    try:
        challenge = Challenge.objects.select_related('profile').get(
            id=challenge_id,
            challenge_type='calendar',
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


def _annotate_holidays(month_data, show_us=False):
    """Add 'holiday_color' to each day dict in month_data for share card rendering."""
    holidays = get_holidays_color_map(include_us=show_us)
    for month in month_data:
        for day in month['days']:
            key = f"{month['month_num']}-{day['day']}"
            day['holiday_color'] = holidays.get(key, '')
    return month_data


def _build_calendar_template_context(challenge, format_type, show_us_holidays=False):
    """
    Build the template context for the Calendar challenge share card.
    Returns a context dict for template rendering.
    """
    month_data = get_calendar_month_data(challenge)
    stats = get_calendar_stats(challenge, month_data=month_data)

    # Cache the avatar (single image, no ThreadPoolExecutor needed)
    avatar_url = ''
    if challenge.profile.avatar_url:
        try:
            avatar_url = ShareImageCache.fetch_and_cache(challenge.profile.avatar_url)
        except Exception:
            avatar_url = ''

    context = {
        'format': format_type,
        'challenge_name': challenge.name,
        'username': challenge.profile.psn_username,
        'avatar_url': avatar_url,
        'total_filled': stats['total_filled'],
        'total_days': stats['total_days'],
        'progress_percentage': challenge.progress_percentage,
        'is_complete': challenge.is_complete,
        'completed_at': (
            challenge.completed_at.strftime('%b %d, %Y')
            if challenge.completed_at else ''
        ),
        'months': _annotate_holidays(month_data, show_us=show_us_holidays),
        'stats': stats,
    }

    return context


def _fetch_bg_to_tempfile(url):
    """Download a background image URL to a temporary file. Returns the file path."""
    resp = http_requests.get(url, timeout=10)
    resp.raise_for_status()
    suffix = '.png' if 'png' in resp.headers.get('content-type', '') else '.jpg'
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(resp.content)
    tmp.close()
    return tmp.name


class CalendarChallengeShareHTMLView(APIView):
    """
    GET /api/v1/challenges/calendar/<challenge_id>/share/html/

    Returns rendered HTML for Calendar challenge share card preview.
    Query params: image_format=landscape|portrait, show_us_holidays=true
    """
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication, TokenAuthentication]

    @method_decorator(ratelimit(key='user', rate='60/m', method='GET', block=True))
    def get(self, request, challenge_id):
        challenge, error = _get_calendar_challenge_or_error(request, challenge_id)
        if error:
            return error

        format_type = request.query_params.get('image_format', 'landscape')
        if format_type not in ('landscape', 'portrait'):
            return Response(
                {'error': 'Invalid format. Must be landscape or portrait'},
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        show_us_holidays = request.query_params.get('show_us_holidays') == 'true'
        context = _build_calendar_template_context(challenge, format_type, show_us_holidays=show_us_holidays)
        html = render_to_string(TEMPLATE, context)

        return Response({'html': html})


class CalendarChallengeSharePNGView(APIView):
    """
    GET /api/v1/challenges/calendar/<challenge_id>/share/png/

    Server-side PNG rendering via Playwright. Returns the finished PNG as a download.
    Query params: image_format, theme, show_us_holidays, game_bg_concept_id
    """
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication, TokenAuthentication]

    @method_decorator(ratelimit(key='user', rate='20/m', method='GET', block=True))
    def get(self, request, challenge_id):
        challenge, error = _get_calendar_challenge_or_error(request, challenge_id)
        if error:
            return error

        format_type = request.query_params.get('image_format', 'landscape')
        if format_type not in ('landscape', 'portrait'):
            return Response(
                {'error': 'Invalid format. Must be landscape or portrait'},
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        theme_key = request.query_params.get('theme', 'default')
        show_us_holidays = request.query_params.get('show_us_holidays') == 'true'
        holidays_flag = '1' if show_us_holidays else '0'

        # Game background support
        game_bg_concept_id = request.query_params.get('game_bg_concept_id', '')
        concept_bg_path = None
        concept = None

        if game_bg_concept_id:
            try:
                concept_id = int(game_bg_concept_id)
                concept = Concept.objects.get(id=concept_id, bg_url__isnull=False)

                # Validate user has platted or 100% a game with this concept
                has_access = ProfileGame.objects.filter(
                    profile=challenge.profile,
                    game__concept=concept,
                ).filter(Q(has_plat=True) | Q(progress=100)).exists()

                if not has_access:
                    return Response(
                        {'error': 'You do not have access to this game background'},
                        status=http_status.HTTP_403_FORBIDDEN,
                    )

                # Force the game art theme
                theme_key = 'gameArtConceptBg'

            except (ValueError, Concept.DoesNotExist):
                return Response(
                    {'error': 'Invalid game background'},
                    status=http_status.HTTP_400_BAD_REQUEST,
                )

        # Check Redis cache for a previously rendered PNG
        bg_flag = game_bg_concept_id or '0'
        cache_key = f"calendar_share_png:{challenge_id}:{format_type}:{theme_key}:{holidays_flag}:{bg_flag}"
        cached = cache.get(cache_key)
        png_bytes = base64.b64decode(cached) if cached else None

        if not png_bytes:
            context = _build_calendar_template_context(challenge, format_type, show_us_holidays=show_us_holidays)
            html = render_to_string(TEMPLATE, context)

            # Download game background image to temp file if needed
            if game_bg_concept_id and concept:
                try:
                    concept_bg_path = _fetch_bg_to_tempfile(concept.bg_url)
                except Exception:
                    logger.exception(
                        f"[CALENDAR-SHARE-PNG] Failed to download game bg for concept {concept.id}"
                    )

            try:
                from core.services.playwright_renderer import render_png
                png_bytes = render_png(
                    html,
                    format_type=format_type,
                    theme_key=theme_key,
                    concept_bg_path=concept_bg_path,
                )
            except Exception as e:
                logger.exception(
                    f"[CALENDAR-SHARE-PNG] Playwright render failed for challenge {challenge_id}: {e}"
                )
                return Response(
                    {'error': 'Failed to render share image'},
                    status=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
                )
            finally:
                # Clean up temp file
                if concept_bg_path:
                    try:
                        os.unlink(concept_bg_path)
                    except OSError:
                        pass

            # Cache the rendered PNG for 10 minutes (base64-encoded for JSON serializer)
            cache.set(cache_key, base64.b64encode(png_bytes).decode('ascii'), timeout=600)

        # Track the download
        track_site_event('calendar_challenge_share_download', str(challenge_id), request)

        safe_name = "".join(
            c for c in challenge.name if c.isalnum() or c in (' ', '-', '_')
        ).strip()
        filename = f"calendar-challenge-{safe_name}-{format_type}.png"

        response = HttpResponse(png_bytes, content_type='image/png')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response


class GameBackgroundSearchView(APIView):
    """
    GET /api/v1/game-backgrounds/?q=<search_term>

    Search the current user's platted/completed games that have a background image.
    Used by the game background picker widget on both the share card and settings page.
    """
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication, TokenAuthentication]

    @method_decorator(ratelimit(key='user', rate='60/m', method='GET', block=True))
    def get(self, request):
        if not hasattr(request.user, 'profile'):
            return Response(
                {'error': 'No linked PSN profile'},
                status=http_status.HTTP_400_BAD_REQUEST,
            )
        profile = request.user.profile
        if not profile.user_is_premium:
            return Response(
                {'error': 'Premium required'},
                status=http_status.HTTP_403_FORBIDDEN,
            )

        query = request.query_params.get('q', '').strip()

        qs = ProfileGame.objects.filter(
            profile=profile,
            game__concept__bg_url__isnull=False,
        ).filter(
            Q(has_plat=True) | Q(progress=100)
        ).exclude(
            game__concept__bg_url=''
        ).select_related('game__concept').order_by(
            Lower('game__concept__unified_title')
        )

        if query:
            qs = qs.filter(game__concept__unified_title__icontains=query)

        qs = qs[:50]

        results = []
        seen_concepts = set()
        for pg in qs:
            concept = pg.game.concept
            if concept.id in seen_concepts:
                continue
            seen_concepts.add(concept.id)
            results.append({
                'concept_id': concept.id,
                'title_name': concept.unified_title,
                'bg_url': concept.bg_url,
                'icon_url': concept.concept_icon_url or '',
            })
            if len(results) >= 20:
                break

        return Response({'results': results})
