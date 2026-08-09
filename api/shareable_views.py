"""Plat card endpoints -- preview HTML and downloadable PNG for a game completion.

Cards are keyed on the game's default `TrophyGroup`; ownership is the eligibility predicate in
`completion_card_service` (default trophy group at 100%), so a deep link can never render a card the
browse page wouldn't list. Two variants share one pipeline: `platinum` when the game defines a platinum,
`full` when it doesn't.

Landscape only (1200x630). Portrait was dropped in the 2026-08 rebuild: these cards live in link
previews and timeline embeds, and one format the design is actually tuned for beats two it isn't.

The legacy `/platinum/<earned_trophy_id>/` pair is kept as a thin alias -- platinum notifications
already in the wild deep-link by EarnedTrophy id, and those endpoints carry TokenAuthentication, so
assume external consumers.
"""
import logging

from django.http import HttpResponse
from django.template.loader import render_to_string
from django.utils.decorators import method_decorator
from django_ratelimit.decorators import ratelimit
from rest_framework import status as http_status
from rest_framework.authentication import SessionAuthentication, TokenAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.services import completion_card_service as cards
from core.services.share_card_utils import resolve_temp_path
from core.services.share_image_cache import ShareImageCache
from trophies.themes import PLAT_CARD_DEFAULT_THEME, PLAT_CARD_THEME_KEYS

logger = logging.getLogger(__name__)

CARD_TEMPLATE = 'shareables/plat_card.html'

#: The card carries a large cover slot, so share-temp images must not be downscaled and re-upscaled
#: during the render. See the renderer's image_max_size note.
CARD_IMAGE_MAX = 1000


def _format_playtime(seconds):
    if not seconds:
        return ''
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        return ''
    hours, minutes = int(seconds // 3600), int((seconds % 3600) // 60)
    return f"{hours}h {minutes}m" if hours else f"{minutes}m"


def build_card_context(profile, standing):
    """Card data plus the locally-cached image paths the renderer needs.

    External images are cached to same-origin temp files rather than embedded as data URIs at this
    stage: iOS Safari fails intermittently on data URIs in the browser preview, and the renderer does
    its own base64 pass for the PNG.
    """
    data = cards.get_card_data(profile, standing)

    for key, source in (
        ('avatar_image', data['user_avatar_url']),
        ('game_image', data['game_image']),
        ('trophy_image', data['trophy_icon_url']),
    ):
        cached = ShareImageCache.fetch_and_cache(source) if source else ''
        if source and not cached:
            logger.warning("[PLAT-CARD] failed to cache %s: %s", key, source)
        data[key] = cached or ''

    # Badge medallion art. Unlike the four images above, these are NOT all remote: group_medallion_layers
    # returns `static(...)` paths for the backdrop fallback and the default subject art, and FileField
    # urls that are /media/ under DEBUG. ShareImageCache hard-rejects any non-http(s) scheme, so routing
    # everything through it dropped every static layer in every environment -- a badge with no custom
    # image rendered with no medallion at all, silently.
    #
    # The renderer already resolves /static/ itself, so those pass through untouched. Only genuinely
    # remote URLs need caching.
    for line in data['badge_lines']:
        cached_layers = []
        for url in line.get('medallion_layers') or []:
            if not url:
                continue
            if url.startswith(('http://', 'https://')):
                resolved = ShareImageCache.fetch_and_cache(url)
                if not resolved:
                    logger.warning("[PLAT-CARD] failed to cache medallion layer: %s", url)
            else:
                resolved = url          # /static/... (or /media/... in dev) -- the renderer handles it
            if resolved:
                cached_layers.append(resolved)
        line['medallion_cached'] = cached_layers

    data['playtime'] = _format_playtime(data['play_duration_seconds'])
    return data


def _art_path(context, request):
    """Local filesystem path for the CHOSEN art ground, or None.

    Only the selected image is cached, and only on the download. Caching all of them up front turned
    opening the modal -- the thing a hunter actually waits on -- into up to four extra synchronous
    `requests.get(timeout=10)` calls on a cold cache, on top of the avatar, cover and trophy icon. The
    picker doesn't need local copies anyway: its swatches are ordinary <img> backgrounds the browser
    fetches itself.

    `?art=<i>` INDEXES the card's own list rather than naming a URL, so a request can only select art
    the card already offers -- the query string can't drive an arbitrary remote fetch.
    """
    options = context.get('art_urls') or []
    if not options:
        return None
    try:
        index = int(request.query_params.get('art', 0))
    except (TypeError, ValueError):
        index = 0
    if not 0 <= index < len(options):
        index = 0
    return resolve_temp_path(ShareImageCache.fetch_and_cache(options[index]))


class _CardViewBase(APIView):
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication, TokenAuthentication]

    def resolve(self, request, **kwargs):
        """Return (profile, standing) or an error Response."""
        profile = getattr(request.user, 'profile', None)
        if not profile:
            return None, None, Response(
                {'error': 'No profile linked to this account'},
                status=http_status.HTTP_400_BAD_REQUEST,
            )
        standing = self.get_standing(profile, **kwargs)
        if not standing:
            return None, None, Response(
                {'error': 'No completion found for this game'},
                status=http_status.HTTP_404_NOT_FOUND,
            )
        return profile, standing, None


class PlatCardHTMLView(_CardViewBase):
    """GET /api/v1/shareables/completion/<trophy_group_id>/html/ -- preview markup for the modal."""

    def get_standing(self, profile, trophy_group_id=None, **_):
        return cards.get_completion(profile, trophy_group_id)

    @method_decorator(ratelimit(key='user', rate='60/m', method='GET', block=True))
    def get(self, request, **kwargs):
        profile, standing, error = self.resolve(request, **kwargs)
        if error:
            return error

        context = build_card_context(profile, standing)
        return Response({
            'html': render_to_string(CARD_TEMPLATE, context),
            'variant': context['variant'],
            'concept_id': context['concept_id'],
            'trophy_group_id': context['trophy_group_id'],
            'has_rating': context['user_rating'] is not None,
            # The picker builds its art swatches from these, as remote URLs the browser loads itself
            # -- so a game with no usable art simply isn't offered the art ground, instead of being
            # offered one that silently falls back. Only the CHOSEN image is cached, at download time.
            'art_options': context['art_urls'],
            'playtime': context['playtime'],
        })


class PlatCardPNGView(_CardViewBase):
    """GET /api/v1/shareables/completion/<trophy_group_id>/png/?theme=… -- the download."""

    def get_standing(self, profile, trophy_group_id=None, **_):
        return cards.get_completion(profile, trophy_group_id)

    @method_decorator(ratelimit(key='user', rate='20/m', method='GET', block=True))
    def get(self, request, **kwargs):
        profile, standing, error = self.resolve(request, **kwargs)
        if error:
            return error

        context = build_card_context(profile, standing)
        html = render_to_string(CARD_TEMPLATE, context)

        try:
            from core.services.playwright_renderer import render_png
            png_bytes = render_png(
                html,
                format_type='landscape',
                # Only the curated set renders here; anything else falls back to the house ground
                # rather than letting an arbitrary site gradient overwrite a designed card.
                theme_key=(
                    request.query_params.get('theme')
                    if request.query_params.get('theme') in PLAT_CARD_THEME_KEYS
                    else PLAT_CARD_DEFAULT_THEME
                ),
                game_image_path=resolve_temp_path(context['game_image']),
                concept_bg_path=_art_path(context, request),
                image_max_size=CARD_IMAGE_MAX,
            )
        except Exception:
            logger.exception("[PLAT-CARD] render failed for trophy group %s", context['trophy_group_id'])
            return Response(
                {'error': 'Failed to render share image'},
                status=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        safe_name = "".join(
            c for c in (context['game_name'] or '') if c.isalnum() or c in (' ', '-', '_')
        ).strip() or 'plat-card'
        suffix = 'platinum' if context['variant'] == cards.PLATINUM else '100'

        response = HttpResponse(png_bytes, content_type='image/png')
        response['Content-Disposition'] = f'attachment; filename="{safe_name}-{suffix}.png"'
        return response


class LegacyPlatinumCardHTMLView(PlatCardHTMLView):
    """GET /api/v1/shareables/platinum/<earned_trophy_id>/html/ -- pre-2026-08 key, same card."""

    def get_standing(self, profile, earned_trophy_id=None, **_):
        return cards.completion_for_earned_trophy(profile, earned_trophy_id)


class LegacyPlatinumCardPNGView(PlatCardPNGView):
    """GET /api/v1/shareables/platinum/<earned_trophy_id>/png/ -- pre-2026-08 key, same card."""

    def get_standing(self, profile, earned_trophy_id=None, **_):
        return cards.completion_for_earned_trophy(profile, earned_trophy_id)
