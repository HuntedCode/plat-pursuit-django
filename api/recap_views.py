"""
REST API views for Monthly Recap feature.
Provides endpoints for viewing, regenerating, and sharing monthly recaps.
"""
import calendar
import base64
import random
import requests
import logging
from urllib.parse import urlparse

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status as http_status
from rest_framework.permissions import IsAuthenticated
from rest_framework.authentication import SessionAuthentication, TokenAuthentication
from django.template.loader import render_to_string
from django.utils.decorators import method_decorator
from django.utils import timezone
from django_ratelimit.decorators import ratelimit

from trophies.models import MonthlyRecap
from trophies.services.monthly_recap_service import MonthlyRecapService

logger = logging.getLogger(__name__)

# Flavor text for each slide type (randomly selected)
SLIDE_FLAVOR_TEXT = {
    'total_trophies': [
        "Every trophy tells a story.",
        "The grind never stops.",
        "One trophy at a time.",
        "Look at that collection grow!",
    ],
    'platinums': [
        "The sweetest victories.",
        "100% club member.",
        "These don't come easy.",
        "Platinum perfection.",
    ],
    'rarest_trophy': [
        "Not many can say they have this one.",
        "A true achievement.",
        "The elite club.",
        "Rarity at its finest.",
    ],
    'most_active_day': [
        "What a day that was!",
        "You were in the zone.",
        "Peak performance.",
        "A day for the books.",
    ],
    'activity_calendar': [
        "Consistency is key.",
        "Every day counts.",
        "Your trophy journey, visualized.",
        "A month of memories.",
    ],
    'games': [
        "New adventures await.",
        "Your gaming journey continues.",
        "So many worlds explored.",
        "The hunt goes on.",
    ],
    'badges': [
        "Level up!",
        "Badge hunting pays off.",
        "Building that collection.",
        "Recognition earned.",
    ],
    'comparison': [
        "Keep up the momentum!",
        "Every month is different.",
        "Steady progress.",
        "The journey continues.",
    ],
}


def get_flavor_text(slide_type):
    """Get random flavor text for a slide type."""
    texts = SLIDE_FLAVOR_TEXT.get(slide_type, [])
    return random.choice(texts) if texts else ''


class RecapAvailableView(APIView):
    """
    GET /api/v1/recap/available/

    Returns list of months with available recaps for the authenticated user.
    Respects premium gating - non-premium users only see current month.
    """
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication, TokenAuthentication]

    def get(self, request):
        profile = request.user.profile
        is_premium = profile.user_is_premium

        months = MonthlyRecapService.get_available_months(
            profile,
            include_premium_only=is_premium
        )

        return Response({
            'months': months,
            'is_premium': is_premium,
        })


class RecapDetailView(APIView):
    """
    GET /api/v1/recap/<year>/<month>/

    Returns recap data for slides rendering.
    Includes premium gating - past months require premium.
    """
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication, TokenAuthentication]

    @method_decorator(ratelimit(key='user', rate='60/m', method='GET', block=True))
    def get(self, request, year, month):
        profile = request.user.profile
        now = timezone.now()

        # Validate month
        if not 1 <= month <= 12:
            return Response(
                {'error': 'Invalid month. Must be 1-12.'},
                status=http_status.HTTP_400_BAD_REQUEST
            )

        # Check premium gating for past months
        is_current_month = (year == now.year and month == now.month)
        if not is_current_month and not profile.user_is_premium:
            return Response(
                {
                    'error': 'Premium subscription required to view past recaps.',
                    'is_premium_required': True,
                },
                status=http_status.HTTP_403_FORBIDDEN
            )

        # Don't allow future months
        if (year > now.year) or (year == now.year and month > now.month):
            return Response(
                {'error': 'Cannot view recap for future months.'},
                status=http_status.HTTP_400_BAD_REQUEST
            )

        # Get or generate the recap
        recap = MonthlyRecapService.get_or_generate_recap(profile, year, month)

        if not recap:
            return Response(
                {
                    'error': 'No activity found for this month.',
                    'no_activity': True,
                },
                status=http_status.HTTP_404_NOT_FOUND
            )

        # Build response with slides
        slides = MonthlyRecapService.build_slides_response(recap)

        return Response({
            'year': recap.year,
            'month': recap.month,
            'month_name': calendar.month_name[recap.month],
            'username': profile.display_psn_username or profile.psn_username,
            'avatar_url': profile.avatar_url or '',
            'is_finalized': recap.is_finalized,
            'is_premium_required': not is_current_month,
            'slides': slides,
            'generated_at': recap.generated_at.isoformat() if recap.generated_at else None,
            'updated_at': recap.updated_at.isoformat() if recap.updated_at else None,
        })


class RecapRegenerateView(APIView):
    """
    POST /api/v1/recap/<year>/<month>/regenerate/

    Force regenerate recap for current month.
    Only works for current month (finalized recaps cannot be regenerated).
    """
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication, TokenAuthentication]

    @method_decorator(ratelimit(key='user', rate='10/m', method='POST', block=True))
    def post(self, request, year, month):
        profile = request.user.profile
        now = timezone.now()

        # Validate month
        if not 1 <= month <= 12:
            return Response(
                {'error': 'Invalid month. Must be 1-12.'},
                status=http_status.HTTP_400_BAD_REQUEST
            )

        # Only allow regeneration of current month
        is_current_month = (year == now.year and month == now.month)
        if not is_current_month:
            return Response(
                {'error': 'Can only regenerate current month recap.'},
                status=http_status.HTTP_400_BAD_REQUEST
            )

        # Force regenerate
        recap = MonthlyRecapService.get_or_generate_recap(
            profile, year, month, force_regenerate=True
        )

        if not recap:
            return Response(
                {
                    'error': 'No activity found for this month.',
                    'no_activity': True,
                },
                status=http_status.HTTP_404_NOT_FOUND
            )

        slides = MonthlyRecapService.build_slides_response(recap)

        return Response({
            'message': 'Recap regenerated successfully.',
            'year': recap.year,
            'month': recap.month,
            'month_name': calendar.month_name[recap.month],
            'slides': slides,
            'updated_at': recap.updated_at.isoformat() if recap.updated_at else None,
        })


class RecapShareImageHTMLView(APIView):
    """
    GET /api/v1/recap/<year>/<month>/html/

    Returns rendered HTML for the monthly recap share image card.
    Query params: image_format=landscape|portrait

    Returns: { "html": "<rendered html>", ... }
    """
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication, TokenAuthentication]

    # Cache for base64 encoded images
    _image_cache = {}

    @method_decorator(ratelimit(key='user', rate='60/m', method='GET', block=True))
    def get(self, request, year, month):
        profile = request.user.profile
        now = timezone.now()

        logger.info(f"[RECAP-HTML] Request for {profile.psn_username} - {year}/{month}")

        # Validate month
        if not 1 <= month <= 12:
            return Response(
                {'error': 'Invalid month. Must be 1-12.'},
                status=http_status.HTTP_400_BAD_REQUEST
            )

        # Check premium gating for past months
        is_current_month = (year == now.year and month == now.month)
        if not is_current_month and not profile.user_is_premium:
            return Response(
                {'error': 'Premium subscription required to share past recaps.'},
                status=http_status.HTTP_403_FORBIDDEN
            )

        # Get format
        format_type = request.query_params.get('image_format', 'landscape')
        if format_type not in ['landscape', 'portrait']:
            return Response(
                {'error': 'Invalid format. Must be landscape or portrait.'},
                status=http_status.HTTP_400_BAD_REQUEST
            )

        # Get the recap
        recap = MonthlyRecapService.get_or_generate_recap(profile, year, month)

        if not recap:
            return Response(
                {'error': 'No activity found for this month.'},
                status=http_status.HTTP_404_NOT_FOUND
            )

        # Build template context
        context = self._build_template_context(recap, profile, format_type)

        # Render the template
        html = render_to_string('recap/partials/recap_share_card.html', context)

        response_data = {'html': html}

        # Include avatar as base64 if available
        if profile.avatar_url:
            avatar_base64 = self._fetch_image_as_base64(profile.avatar_url)
            if avatar_base64:
                response_data['avatar_base64'] = avatar_base64

        return Response(response_data)

    def _build_template_context(self, recap, profile, format_type):
        """Build the context dict for the share image template."""
        month_name = calendar.month_name[recap.month]

        # Process rarest trophy image
        rarest_icon = ''
        if recap.rarest_trophy_data:
            icon_url = recap.rarest_trophy_data.get('icon_url', '')
            if icon_url:
                rarest_icon = self._fetch_image_as_base64(icon_url) or icon_url

        # Process avatar
        avatar_url = profile.avatar_url or ''
        avatar_data = self._fetch_image_as_base64(avatar_url) if avatar_url else ''

        # Process platinum game images (first 3 for share card)
        platinums_with_images = []
        for plat in (recap.platinums_data or [])[:3]:
            plat_copy = dict(plat)
            if plat_copy.get('game_image'):
                plat_copy['game_image'] = (
                    self._fetch_image_as_base64(plat_copy['game_image'])
                    or plat_copy['game_image']
                )
            platinums_with_images.append(plat_copy)

        return {
            'format': format_type,
            'year': recap.year,
            'month': recap.month,
            'month_name': month_name,
            'username': profile.display_psn_username or profile.psn_username,
            'avatar_url': avatar_data or avatar_url,
            # Trophy counts
            'total_trophies': recap.total_trophies_earned,
            'bronzes': recap.bronzes_earned,
            'silvers': recap.silvers_earned,
            'golds': recap.golds_earned,
            'platinums': recap.platinums_earned,
            # Game stats
            'games_started': recap.games_started,
            'games_completed': recap.games_completed,
            # Highlights
            'platinums_data': platinums_with_images,
            'rarest_trophy': recap.rarest_trophy_data or {},
            'rarest_trophy_icon': rarest_icon,
            'most_active_day': recap.most_active_day or {},
            'activity_calendar': recap.activity_calendar or {},
            # Badge stats
            'badge_xp': recap.badge_xp_earned,
            'badges_count': recap.badges_earned_count,
        }

    def _fetch_image_as_base64(self, url):
        """Fetch an external image and convert it to a base64 data URL."""
        if not url:
            return ''

        # Check cache first
        if url in self._image_cache:
            return self._image_cache[url]

        try:
            parsed = urlparse(url)
            if parsed.scheme not in ('http', 'https'):
                return ''

            response = requests.get(url, timeout=10, headers={
                'User-Agent': 'Mozilla/5.0 (compatible; PlatPursuit/1.0)'
            })
            response.raise_for_status()

            content_type = response.headers.get('Content-Type', 'image/png')
            if not content_type.startswith('image/'):
                content_type = 'image/png'

            image_data = base64.b64encode(response.content).decode('utf-8')
            data_url = f"data:{content_type};base64,{image_data}"

            # Cache the result (limit cache size)
            if len(self._image_cache) < 100:
                self._image_cache[url] = data_url

            return data_url

        except requests.RequestException as e:
            logger.warning(f"[RECAP-HTML] Failed to fetch image {url}: {e}")
            return ''
        except Exception as e:
            logger.exception(f"[RECAP-HTML] Error encoding image {url}: {e}")
            return ''


class RecapSlidePartialView(APIView):
    """
    GET /api/v1/recap/<year>/<month>/slide/<slide_type>/

    Returns rendered HTML for a specific slide partial.
    Used by frontend to render slides from Django templates.
    """
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication, TokenAuthentication]

    # Map slide types to template paths
    SLIDE_TEMPLATES = {
        'intro': 'recap/partials/slides/intro.html',
        'total_trophies': 'recap/partials/slides/total_trophies.html',
        'platinums': 'recap/partials/slides/platinums.html',
        'rarest_trophy': 'recap/partials/slides/rarest_trophy.html',
        'most_active_day': 'recap/partials/slides/most_active_day.html',
        'activity_calendar': 'recap/partials/slides/activity_calendar.html',
        'games': 'recap/partials/slides/games.html',
        'badges': 'recap/partials/slides/badges.html',
        'comparison': 'recap/partials/slides/comparison.html',
        'summary': 'recap/partials/slides/summary.html',
        # Quiz slides
        'quiz_total_trophies': 'recap/partials/slides/quiz_total_trophies.html',
        'quiz_rarest_trophy': 'recap/partials/slides/quiz_rarest_trophy.html',
        'quiz_active_day': 'recap/partials/slides/quiz_active_day.html',
        'quiz_closest_badge': 'recap/partials/slides/quiz_closest_badge.html',
        # New stat slides
        'streak': 'recap/partials/slides/streak.html',
        'time_analysis': 'recap/partials/slides/time_analysis.html',
    }

    def get(self, request, year, month, slide_type):
        profile = request.user.profile
        now = timezone.now()

        # Validate slide type
        if slide_type not in self.SLIDE_TEMPLATES:
            return Response(
                {'error': f'Invalid slide type: {slide_type}'},
                status=http_status.HTTP_400_BAD_REQUEST
            )

        # Validate month
        if not 1 <= month <= 12:
            return Response(
                {'error': 'Invalid month. Must be 1-12.'},
                status=http_status.HTTP_400_BAD_REQUEST
            )

        # Check premium gating for past months
        is_current_month = (year == now.year and month == now.month)
        if not is_current_month and not profile.user_is_premium:
            return Response(
                {'error': 'Premium subscription required.'},
                status=http_status.HTTP_403_FORBIDDEN
            )

        # Get the recap
        recap = MonthlyRecapService.get_or_generate_recap(profile, year, month)

        if not recap:
            return Response(
                {'error': 'No activity found for this month.'},
                status=http_status.HTTP_404_NOT_FOUND
            )

        # Build context for this specific slide type
        context = self._build_slide_context(slide_type, recap, profile, year, month)

        # Render the template
        template_path = self.SLIDE_TEMPLATES[slide_type]
        html = render_to_string(template_path, context, request=request)

        return Response({'html': html, 'slide_type': slide_type})

    def _build_slide_context(self, slide_type, recap, profile, year, month):
        """Build context for a specific slide type."""
        month_name = calendar.month_name[month]

        if slide_type == 'intro':
            return {
                'month_name': month_name,
                'year': year,
                'username': profile.display_psn_username or profile.psn_username,
                'avatar_url': profile.avatar_url or '',
                'is_premium': profile.user_is_premium,
            }

        elif slide_type == 'total_trophies':
            return {
                'value': recap.total_trophies_earned,
                'breakdown': {
                    'bronze': recap.bronzes_earned,
                    'silver': recap.silvers_earned,
                    'gold': recap.golds_earned,
                    'platinum': recap.platinums_earned,
                },
                'flavor_text': get_flavor_text('total_trophies'),
            }

        elif slide_type == 'platinums':
            return {
                'count': recap.platinums_earned,
                'games': recap.platinums_data or [],
                'flavor_text': get_flavor_text('platinums'),
            }

        elif slide_type == 'rarest_trophy':
            data = recap.rarest_trophy_data or {}
            return {
                'name': data.get('name', ''),
                'game': data.get('game', ''),
                'earn_rate': data.get('earn_rate', 0),
                'icon_url': data.get('icon_url', ''),
                'trophy_type': data.get('trophy_type', ''),
                'flavor_text': get_flavor_text('rarest_trophy'),
            }

        elif slide_type == 'most_active_day':
            data = recap.most_active_day or {}
            return {
                'date': data.get('date', ''),
                'day_name': data.get('day_name', ''),
                'trophy_count': data.get('trophy_count', 0),
                'flavor_text': get_flavor_text('most_active_day'),
            }

        elif slide_type == 'activity_calendar':
            data = recap.activity_calendar or {}
            first_day_weekday = data.get('first_day_weekday', 0)
            return {
                'days': data.get('days', []),
                'max_count': data.get('max_count', 0),
                'total_active_days': data.get('total_active_days', 0),
                'first_day_weekday': first_day_weekday,
                'first_day_offset': range(first_day_weekday),
                'days_in_month': data.get('days_in_month', 30),
                'month_name': month_name,
                'year': year,
                'flavor_text': get_flavor_text('activity_calendar'),
            }

        elif slide_type == 'games':
            return {
                'started': recap.games_started,
                'completed': recap.games_completed,
                'flavor_text': get_flavor_text('games'),
            }

        elif slide_type == 'badges':
            return {
                'xp_earned': recap.badge_xp_earned,
                'badges_count': recap.badges_earned_count,
                'badges': recap.badges_data or [],
                'flavor_text': get_flavor_text('badges'),
            }

        elif slide_type == 'comparison':
            data = recap.comparison_data or {}
            return {
                'vs_prev_month': data.get('vs_prev_month_pct', '0%'),
                'personal_bests': data.get('personal_bests', []),
                'flavor_text': get_flavor_text('comparison'),
            }

        elif slide_type == 'summary':
            highlights = []
            if recap.platinums_earned > 0:
                highlights.append(f"{recap.platinums_earned} platinum{'s' if recap.platinums_earned != 1 else ''}")
            highlights.append(f"{recap.total_trophies_earned} trophies")
            if recap.games_started > 0:
                highlights.append(f"{recap.games_started} new game{'s' if recap.games_started != 1 else ''}")

            return {
                'highlights': highlights,
                'year': year,
                'month': month,
            }

        # Quiz slides (all denormalized from recap model)
        elif slide_type == 'quiz_total_trophies':
            return recap.quiz_total_trophies_data or {}

        elif slide_type == 'quiz_rarest_trophy':
            return recap.quiz_rarest_trophy_data or {}

        elif slide_type == 'quiz_active_day':
            return recap.quiz_active_day_data or {}

        elif slide_type == 'quiz_closest_badge':
            return recap.badge_progress_quiz_data or {}

        # New stat slides (denormalized from recap model)
        elif slide_type == 'streak':
            return recap.streak_data or {}

        elif slide_type == 'time_analysis':
            time_data = recap.time_analysis_data or {}
            if time_data and time_data.get('periods'):
                # Add max for template bar chart scaling
                time_data['max_period_count'] = max(time_data['periods'].values()) or 1
            return time_data

        return {}
