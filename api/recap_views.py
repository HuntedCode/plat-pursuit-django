"""
REST API views for Monthly Recap feature.
Provides endpoints for viewing, regenerating, and sharing monthly recaps.
"""
import calendar
import random
import logging

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status as http_status
from rest_framework.permissions import IsAuthenticated
from rest_framework.authentication import SessionAuthentication, TokenAuthentication
from django.template.loader import render_to_string
from django.http import HttpResponse
from django.utils.decorators import method_decorator
from core.services.tracking import track_site_event
from django_ratelimit.decorators import ratelimit

from trophies.models import MonthlyRecap
from trophies.services.monthly_recap_service import DECK_BY_TYPE, MonthlyRecapService
from trophies.recap_utils import (
    get_user_local_now, is_most_recent_completed_month, check_sync_freshness, MIN_RECAP_YEAR,
)
from core.services.share_image_cache import ShareImageCache

logger = logging.getLogger(__name__)

#: Platinum covers the share card's grid can hold. The builder must fill up to this, not fewer, or the
#: template's "+N more" badge is unreachable.
SHARE_CARD_PLATINUM_SLOTS = 8
#: Longest edge for images embedded in the card, matching the plat card's budget.
SHARE_CARD_IMAGE_MAX = 1000


def _check_profile_synced(request):
    """Returns a 403 Response if the user has no linked profile or hasn't finished syncing."""
    profile = getattr(request.user, 'profile', None)
    if not profile:
        return Response(
            {'error': 'No PSN profile linked.', 'sync_gate': 'no_profile'},
            status=http_status.HTTP_403_FORBIDDEN,
        )
    if profile.sync_status != 'synced':
        return Response(
            {'error': 'Profile sync not complete.', 'sync_gate': profile.sync_status},
            status=http_status.HTTP_403_FORBIDDEN,
        )
    return None


def _check_month_bounds(year, month, now_local):
    """Returns a 400 Response if year/month cannot describe a real past month. None otherwise.

    Shared because it was NOT shared: only RecapDetailView validated the year, and the premium 403 was
    incidentally doing the job on the other three -- `is_recent_or_current` is false for year 0, so a
    non-premium request was rejected before it could reach the date maths. Removing the gate exposed the
    crash: /html/, /png/ and /slide/ each 500'd with an unhandled ValueError on `/api/v1/recap/0/1/...`.
    """
    if year < MIN_RECAP_YEAR or not (1 <= month <= 12):
        return Response(
            {'error': 'Invalid year or month.'},
            status=http_status.HTTP_400_BAD_REQUEST,
        )
    if (year > now_local.year) or (year == now_local.year and month > now_local.month):
        return Response(
            {'error': 'Cannot view recap for future months.'},
            status=http_status.HTTP_400_BAD_REQUEST,
        )
    return None


def _check_sync_freshness_api(profile, year, month, now_local):
    """
    Returns a 403 Response if the requested month is the most recent completed
    month and the user hasn't synced this calendar month. Returns None otherwise.
    """
    if is_most_recent_completed_month(year, month, now_local):
        if not check_sync_freshness(profile, now_local):
            return Response(
                {
                    'error': 'Your trophy data needs a fresh sync before viewing this recap.',
                    'sync_gate': 'sync_stale',
                },
                status=http_status.HTTP_403_FORBIDDEN,
            )
    return None


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
    Every month the hunter earned a trophy in; no gating.
    """
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication, TokenAuthentication]

    def get(self, request):
        gate = _check_profile_synced(request)
        if gate:
            return gate
        profile = request.user.profile

        return Response({
            'months': MonthlyRecapService.get_available_months(profile),
        })


class RecapDetailView(APIView):
    """
    GET /api/v1/recap/<year>/<month>/

    Returns recap data for slides rendering.
    No gating: any past month with activity is viewable.
    """
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication, TokenAuthentication]

    @method_decorator(ratelimit(key='user', rate='60/m', method='GET', block=True))
    def get(self, request, year, month):
        gate = _check_profile_synced(request)
        if gate:
            return gate
        profile = request.user.profile
        now_local = get_user_local_now(request)

        bounds = _check_month_bounds(year, month, now_local)
        if bounds:
            return bounds

        # Check sync freshness for the most recent completed month
        stale_gate = _check_sync_freshness_api(profile, year, month, now_local)
        if stale_gate:
            return stale_gate

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
        gate = _check_profile_synced(request)
        if gate:
            return gate
        profile = request.user.profile
        now_local = get_user_local_now(request)

        # Only allow regeneration of current month
        is_current_month = (year == now_local.year and month == now_local.month)
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

    @method_decorator(ratelimit(key='user', rate='60/m', method='GET', block=True))
    def get(self, request, year, month):
        gate = _check_profile_synced(request)
        if gate:
            return gate
        profile = request.user.profile
        now_local = get_user_local_now(request)

        logger.info(f"[RECAP-HTML] Request for {profile.psn_username} - {year}/{month}")

        bounds = _check_month_bounds(year, month, now_local)
        if bounds:
            return bounds

        # Check sync freshness for the most recent completed month
        stale_gate = _check_sync_freshness_api(profile, year, month, now_local)
        if stale_gate:
            return stale_gate

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

        track_site_event('recap_share_generate', f"{year}-{month:02d}", request)

        # Build template context
        context = self._build_template_context(recap, profile, format_type)

        # Render the template
        html = render_to_string('recap/partials/recap_share_card.html', context)

        response_data = {'html': html}

        # Include avatar as same-origin URL if available
        if profile.avatar_url:
            avatar_cached = ShareImageCache.fetch_and_cache(profile.avatar_url)
            if avatar_cached:
                response_data['avatar_base64'] = avatar_cached

        return Response(response_data)

    def _build_template_context(self, recap, profile, format_type):
        """Build the context dict for the share image template."""
        month_name = calendar.month_name[recap.month]

        # Process rarest trophy image — cache as same-origin temp file
        rarest_icon = ''
        if recap.rarest_trophy_data:
            icon_url = recap.rarest_trophy_data.get('icon_url', '')
            if icon_url:
                rarest_icon = ShareImageCache.fetch_and_cache(icon_url)
                if icon_url and not rarest_icon:
                    logger.warning(f"[RECAP-SHARE] Failed to cache rarest trophy icon: {icon_url}")

        # Process avatar
        avatar_url = profile.avatar_url or ''
        avatar_data = ShareImageCache.fetch_and_cache(avatar_url) if avatar_url else ''
        if avatar_url and not avatar_data:
            logger.warning(f"[RECAP-SHARE] Failed to cache avatar: {avatar_url}")

        # The card's grid holds SHARE_CARD_PLATINUM_SLOTS. This capped at 3 regardless, so a six-platinum
        # month rendered three of them and the template's "+N more" badge -- which compares the rendered
        # count against the total -- could never fire, because the two were never allowed to differ.
        all_plats = recap.platinums_data or []
        platinums_with_images = []
        for plat in all_plats[:SHARE_CARD_PLATINUM_SLOTS]:
            plat_copy = dict(plat)
            if plat_copy.get('game_image'):
                original_url = plat_copy['game_image']
                plat_copy['game_image'] = ShareImageCache.fetch_and_cache(original_url)
                if original_url and not plat_copy['game_image']:
                    logger.warning(f"[RECAP-SHARE] Failed to cache platinum game image: {original_url}")
            platinums_with_images.append(plat_copy)

        return {
            'format': format_type,
            'year': recap.year,
            'month': recap.month,
            'month_name': month_name,
            'username': profile.display_psn_username or profile.psn_username,
            'avatar_url': avatar_data,
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
            'platinums_overflow': max(0, len(all_plats) - SHARE_CARD_PLATINUM_SLOTS),
            'rarest_trophy': recap.rarest_trophy_data or {},
            'rarest_trophy_icon': rarest_icon,
            'most_active_day': recap.most_active_day or {},
            'activity_calendar': recap.activity_calendar or {},
            # Badge stats
            'badge_xp': recap.badge_xp_earned,
            'badges_count': recap.badges_earned_count,
            # Identity
            'is_plus': getattr(profile, 'is_plus', False),
        }



class RecapShareImagePNGView(APIView):
    """
    GET /api/v1/recap/<year>/<month>/png/?image_format=landscape&theme=default

    Server-side PNG rendering via Playwright. Returns the finished PNG as a download.
    """
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication, TokenAuthentication]

    @method_decorator(ratelimit(key='user', rate='20/m', method='GET', block=True))
    def get(self, request, year, month):
        gate = _check_profile_synced(request)
        if gate:
            return gate
        profile = request.user.profile
        now_local = get_user_local_now(request)

        if not 1 <= month <= 12:
            return Response(
                {'error': 'Invalid month. Must be 1-12.'},
                status=http_status.HTTP_400_BAD_REQUEST
            )


        bounds = _check_month_bounds(year, month, now_local)
        if bounds:
            return bounds

        # Check sync freshness for the most recent completed month
        stale_gate = _check_sync_freshness_api(profile, year, month, now_local)
        if stale_gate:
            return stale_gate

        format_type = request.query_params.get('image_format', 'landscape')
        if format_type not in ['landscape', 'portrait']:
            return Response(
                {'error': 'Invalid format. Must be landscape or portrait.'},
                status=http_status.HTTP_400_BAD_REQUEST
            )

        # The picker offers `include_game_art=False`; this accepted anything, so a hand-typed key could
        # select a theme that expects a game image the recap card never supplies.
        theme_key = (request.query_params.get('theme') or 'default').strip()
        if theme_key != 'default':
            from trophies.themes import GRADIENT_THEMES
            if theme_key not in GRADIENT_THEMES or GRADIENT_THEMES[theme_key].get('requires_game_image'):
                return Response({'error': 'Invalid theme.'},
                                status=http_status.HTTP_400_BAD_REQUEST)

        recap = MonthlyRecapService.get_or_generate_recap(profile, year, month)
        if not recap:
            return Response(
                {'error': 'No activity found for this month.'},
                status=http_status.HTTP_404_NOT_FOUND
            )

        # Reuse the HTML view's context builder
        # Note: tracking is handled by the HTML view endpoint, not here (avoids double-counting)
        html_view = RecapShareImageHTMLView()
        context = html_view._build_template_context(recap, profile, format_type)

        html = render_to_string('recap/partials/recap_share_card.html', context)

        try:
            from core.services.playwright_renderer import render_png
            png_bytes = render_png(
                html,
                format_type=format_type,
                theme_key=theme_key,
                # Left at the 200px default, every cover and icon on the card was an upscaled thumbnail.
                # Same budget the plat card uses (api/shareable_views.CARD_IMAGE_MAX).
                image_max_size=SHARE_CARD_IMAGE_MAX,
            )
        except Exception as e:
            logger.exception(f"[RECAP-PNG] Playwright render failed for {year}/{month}: {e}")
            return Response(
                {'error': 'Failed to render share image'},
                status=http_status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        month_name = calendar.month_name[month]
        filename = f"recap-{month_name}-{year}-{format_type}.png"

        response = HttpResponse(png_bytes, content_type='image/png')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response


class RecapDeckView(APIView):
    """
    GET /api/v1/recap/<year>/<month>/deck/

    Every slide's rendered HTML in ONE response.

    The deck used to be fetched a slide at a time, in parallel -- which meant a single month view cost
    one request per beat. At 20 beats that is 20 requests, and DRF throttles at 60/min per user across
    the WHOLE API, so switching months a few times in quick succession exhausted the bucket and started
    429ing everything the page did: the remaining slides, the notification poll, the unread count. The
    deck was starving the rest of the site of its own request budget.

    Batching is the fix rather than retrying or backing off, because the requests were never independent:
    they all resolve the same recap, and `get_or_generate_recap` was being called once per slide for a
    month the first request had already generated.
    """
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication, TokenAuthentication]

    @method_decorator(ratelimit(key='user', rate='30/m', method='GET', block=True))
    def get(self, request, year, month):
        gate = _check_profile_synced(request)
        if gate:
            return gate
        profile = request.user.profile
        now_local = get_user_local_now(request)

        bounds = _check_month_bounds(year, month, now_local)
        if bounds:
            return bounds

        stale_gate = _check_sync_freshness_api(profile, year, month, now_local)
        if stale_gate:
            return stale_gate

        recap = MonthlyRecapService.get_or_generate_recap(profile, year, month)
        if not recap:
            return Response({'error': 'No activity found for this month.'},
                            status=http_status.HTTP_404_NOT_FOUND)

        partial = RecapSlidePartialView()
        slides = []
        for beat in MonthlyRecapService.build_slides_response(recap):
            slide_type = beat['type']
            template = partial.SLIDE_TEMPLATES.get(slide_type)
            if not template:
                continue
            context = partial._build_slide_context(slide_type, recap, profile, year, month)
            slides.append({
                'type': slide_type,
                'html': render_to_string(template, context, request=request),
            })

        return Response({'slides': slides})


class RecapSlidePartialView(APIView):
    """
    GET /api/v1/recap/<year>/<month>/slide/<slide_type>/

    Returns rendered HTML for a specific slide partial.

    NOTE: the deck no longer uses this -- it fetches every slide in one request from RecapDeckView, which
    reuses this class's SLIDE_TEMPLATES and _build_slide_context. The route is kept because it is a
    documented public endpoint (docs/reference/api-endpoints.md) and may have consumers outside this repo,
    but nothing in the repo calls it any more. It was also the one recap view with no rate limit; it has
    one now, since an unthrottled endpoint that renders templates is worth closing whether or not the
    frontend uses it.
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
        # Context beats
        'taste': 'recap/partials/slides/taste.html',
        'community': 'recap/partials/slides/community.html',
        'month_in_history': 'recap/partials/slides/month_in_history.html',
        # No server payload: the controller fills it from the answers actually given.
        'quiz_score': 'recap/partials/slides/quiz_score.html',
    }

    @method_decorator(ratelimit(key='user', rate='60/m', method='GET', block=True))
    def get(self, request, year, month, slide_type):
        gate = _check_profile_synced(request)
        if gate:
            return gate
        profile = request.user.profile
        now_local = get_user_local_now(request)

        # Validate slide type
        if slide_type not in self.SLIDE_TEMPLATES:
            return Response(
                {'error': f'Invalid slide type: {slide_type}'},
                status=http_status.HTTP_400_BAD_REQUEST
            )

        bounds = _check_month_bounds(year, month, now_local)
        if bounds:
            return bounds

        # Check sync freshness for the most recent completed month
        stale_gate = _check_sync_freshness_api(profile, year, month, now_local)
        if stale_gate:
            return stale_gate

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
        """Context for one slide partial.

        Delegates to the SAME `DECK` beat that `build_slides_response` used to order the deck. This used
        to be a parallel ~110-line if/elif chain rebuilding every payload a second time, and the two had
        already drifted -- the summary's highlight chips differed between them. One catalogue, one payload.

        Only genuinely view-level things are layered on here: flavour text (random per render, so it
        cannot be part of a persisted payload), the viewer's identity for the intro, and the year/month
        the summary's share links need.
        """
        beat = DECK_BY_TYPE.get(slide_type)
        if beat is None:
            return {}

        context = dict(beat.payload(recap, {'month_name': calendar.month_name[month], 'year': year}))

        if slide_type == 'activity_calendar':
            # A range, purely so the template can loop blank cells before day 1. It is built HERE and not
            # in the payload because the payload is JSON-serialised into the deck response, and a range is
            # not serialisable -- putting it there 500s every recap page.
            context['first_day_offset'] = range(context.get('first_day_weekday', 0))
        elif slide_type == 'intro':
            context.update({
                'username': profile.display_psn_username or profile.psn_username,
                'avatar_url': profile.avatar_url or '',
            })
        elif slide_type == 'summary':
            context.update({'year': year, 'month': month})

        flavor = get_flavor_text(slide_type)
        if flavor:
            context['flavor_text'] = flavor
        return context
