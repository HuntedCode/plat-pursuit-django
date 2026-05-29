"""
REST API views for Platinum Calendar Challenge share card images.
Provides HTML preview, PNG download, and game background search endpoints.
"""
import logging
import os
import tempfile

import requests as http_requests
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
        'is_plus': getattr(challenge.profile, 'is_plus', False),
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

        # Game background support
        game_bg_concept_id = request.query_params.get('game_bg_concept_id', '')
        game_bg_image_url = request.query_params.get('game_bg_image_url', '').strip()
        concept_bg_path = None
        concept = None
        chosen_bg_url = None

        if game_bg_concept_id:
            try:
                concept_id = int(game_bg_concept_id)
                if game_bg_image_url:
                    # Image picker: concept may have no PSN bg_url (IGDB-only art).
                    concept = Concept.objects.select_related('igdb_match').get(id=concept_id)
                else:
                    concept = Concept.objects.get(id=concept_id, bg_url__isnull=False, bg_url__gt='')

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

                if game_bg_image_url:
                    # Guard against arbitrary URL injection.
                    if game_bg_image_url not in _concept_landscape_images(concept):
                        return Response(
                            {'error': 'That image is not available for this game'},
                            status=http_status.HTTP_400_BAD_REQUEST,
                        )
                    chosen_bg_url = game_bg_image_url
                else:
                    chosen_bg_url = concept.bg_url

                # Force the game art theme
                theme_key = 'gameArtConceptBg'

            except (ValueError, Concept.DoesNotExist):
                return Response(
                    {'error': 'Invalid game background'},
                    status=http_status.HTTP_400_BAD_REQUEST,
                )

        context = _build_calendar_template_context(challenge, format_type, show_us_holidays=show_us_holidays)
        html = render_to_string(TEMPLATE, context)

        # Download game background image to temp file if needed
        if concept and chosen_bg_url:
            try:
                concept_bg_path = _fetch_bg_to_tempfile(chosen_bg_url)
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

        # Track the download
        track_site_event('calendar_challenge_share_download', str(challenge_id), request)

        safe_name = "".join(
            c for c in challenge.name if c.isalnum() or c in (' ', '-', '_')
        ).strip() or 'challenge'
        filename = f"calendar-challenge-{safe_name}-{format_type}.png"

        response = HttpResponse(png_bytes, content_type='image/png')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response


def _concept_landscape_images(concept):
    """Ordered, de-duplicated landscape image URLs for a concept's picker.

    PSN GAMEHUB art first (real key art when present), then IGDB artworks,
    then IGDB screenshots, with the portrait cover as a last resort so every
    game offers at least one option even when no landscape art exists.
    """
    urls = []
    seen = set()

    def _add(url):
        if url and url not in seen:
            seen.add(url)
            urls.append(url)

    _add(concept.bg_url)

    match = getattr(concept, 'igdb_match', None)
    if match and match.is_trusted:
        for url in match.artwork_urls('1080p'):
            _add(url)
        for url in match.screenshot_urls('screenshot_big'):
            _add(url)

    _add(concept.cover_url)

    return urls


class GameBackgroundSearchView(APIView):
    """
    GET /api/v1/game-backgrounds/?q=<search_term>&require_bg=<0|1>

    Search the current user's platted/completed games. Used by the game
    picker widget shared by the share card and the profile banner picker.

    `require_bg` (default true) keeps only games that already have a PSN
    landscape image (`concept.bg_url`). Callers using the two-step image
    picker pass `require_bg=0` to surface every platted/100% game, since
    images are then sourced per-concept from IGDB as well.
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
        require_bg = request.query_params.get('require_bg', '1').lower() not in ('0', 'false', 'no')

        qs = ProfileGame.objects.filter(
            profile=profile,
        ).filter(
            Q(has_plat=True) | Q(progress=100)
        ).select_related('game__concept')

        if require_bg:
            qs = qs.filter(
                game__concept__bg_url__isnull=False,
            ).exclude(
                game__concept__bg_url=''
            )

        if query:
            qs = qs.filter(
                game__concept__unified_title__icontains=query
            ).order_by(Lower('game__concept__unified_title'))
            limit = 20
        else:
            # No query: show most recently played games first for browsing
            qs = qs.order_by('-last_played_date_time')
            limit = 24

        # Deduplicate by concept at the DB level
        concept_ids = list(
            qs.values_list('game__concept_id', flat=True)
            .distinct()[:limit]
        )

        # select_related igdb_match (deferring the heavy raw_response blob) so
        # the IGDB-first c.cover_url below doesn't N+1; anchored concepts have
        # an empty concept_icon_url and rely on the trusted IGDB cover.
        base = Concept.objects.select_related('igdb_match').defer('igdb_match__raw_response')
        if query:
            concepts = base.filter(id__in=concept_ids).order_by(Lower('unified_title'))
        else:
            # Preserve the recency order from ProfileGame
            concepts_map = {c.id: c for c in base.filter(id__in=concept_ids)}
            concepts = [concepts_map[cid] for cid in concept_ids if cid in concepts_map]

        results = [{
            'concept_id': c.id,
            'title_name': c.unified_title,
            'bg_url': c.bg_url,
            'icon_url': c.cover_url or '',
        } for c in concepts]

        return Response({'results': results})


class ConceptBannerImagesView(APIView):
    """
    GET /api/v1/game-backgrounds/<concept_id>/images/

    Return the landscape image options for a single concept the user has
    platted/100% completed. Powers the second step of the image picker
    (game -> pick exact image) for both the profile banner and share cards.
    """
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication, TokenAuthentication]

    @method_decorator(ratelimit(key='user', rate='60/m', method='GET', block=True))
    def get(self, request, concept_id):
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

        owns_concept = ProfileGame.objects.filter(
            profile=profile,
            game__concept_id=concept_id,
        ).filter(
            Q(has_plat=True) | Q(progress=100)
        ).exists()
        if not owns_concept:
            return Response(
                {'error': 'Game not found in your platinum/completed library'},
                status=http_status.HTTP_404_NOT_FOUND,
            )

        concept = (
            Concept.objects.select_related('igdb_match')
            .filter(id=concept_id)
            .first()
        )
        if concept is None:
            return Response(
                {'error': 'Concept not found'},
                status=http_status.HTTP_404_NOT_FOUND,
            )

        images = _concept_landscape_images(concept)
        return Response({
            'concept_id': concept.id,
            'title_name': concept.unified_title,
            'images': images,
        })
