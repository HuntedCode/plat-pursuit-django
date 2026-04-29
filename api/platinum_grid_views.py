"""
REST API views for Platinum Grid share image generation.

Provides HTML preview and PNG download endpoints for the platinum grid wizard.
"""
import logging
import math

from django.http import HttpResponse
from django.template.loader import render_to_string
from django.utils.decorators import method_decorator
from django_ratelimit.decorators import ratelimit
from rest_framework import status as http_status
from rest_framework.authentication import SessionAuthentication
from rest_framework.response import Response
from rest_framework.views import APIView

from core.services.share_image_cache import ShareImageCache
from trophies.mixins import LoginRequiredAPIMixin
from trophies.models import EarnedTrophy

logger = logging.getLogger(__name__)

# Grid layout constants
CELL_WIDTH = 128                         # px per icon cell (width)
CELL_HEIGHT_GAME = CELL_WIDTH * 4 // 3   # 3:4 portrait aspect for game covers (matches IGDB ratio)
CELL_HEIGHT_TROPHY = CELL_WIDTH          # square for trophy icons (PSN trophy icons are always square)
CELL_GAP = 4                             # px gap between cells
HEADER_HEIGHT = 80                       # px for branding bar
FOOTER_HEIGHT = 40                       # px for footer
PADDING = 24                             # px outer padding
SECTION_GAP = 12                         # px margin between header/grid/footer


def _cell_height_for(icon_type):
    """Cell height depends on icon shape: game covers are 3:4 portrait, trophies are square."""
    return CELL_HEIGHT_TROPHY if icon_type == 'trophy' else CELL_HEIGHT_GAME


def _calculate_grid_dimensions(count, cols, cell_height):
    """Calculate pixel dimensions for a grid of icons."""
    rows = math.ceil(count / cols) if cols > 0 else 1
    width = PADDING * 2 + cols * CELL_WIDTH + (cols - 1) * CELL_GAP
    grid_height = rows * cell_height + (rows - 1) * CELL_GAP
    height = PADDING * 2 + HEADER_HEIGHT + FOOTER_HEIGHT + grid_height + SECTION_GAP * 2
    return width, height, rows


def _auto_columns(count):
    """
    Pick the best column count for a balanced grid.

    Prefers columns that evenly divide the count (no partial last row).
    Among ties, picks the option closest to a square aspect ratio.
    Falls back to the least-ragged option if no perfect divisor exists.
    """
    if count <= 5:
        return count

    # Candidate column counts based on grid size
    if count <= 20:
        candidates = range(4, 8)
    elif count <= 60:
        candidates = range(5, 11)
    elif count <= 120:
        candidates = range(8, 13)
    else:
        candidates = range(10, 15)

    best_cols = 8
    best_score = float('inf')

    for cols in candidates:
        if cols > count:
            continue
        rows = math.ceil(count / cols)
        remainder = count % cols
        # Penalize partial last rows (0 remainder = perfect)
        ragged_penalty = (cols - remainder) if remainder else 0
        # Prefer squarish: minimize difference between cols and rows
        aspect_penalty = abs(cols - rows) * 0.5
        score = ragged_penalty + aspect_penalty

        if score < best_score:
            best_score = score
            best_cols = cols

    return best_cols


def _parse_icon_ids(raw):
    """Parse comma-separated icon IDs string into list of ints."""
    if not raw:
        return []
    try:
        return [int(x.strip()) for x in raw.split(',') if x.strip()]
    except (ValueError, TypeError):
        return []


def _build_grid_context(request, profile, icon_ids, icon_type, cols, theme_key):
    """
    Build the template context for platinum_grid_card.html.

    Returns (context_dict, error_string). error_string is None on success.
    """
    is_premium = profile.user_is_premium
    max_icons = 500 if is_premium else 100
    if len(icon_ids) > max_icons:
        return None, f'Maximum {max_icons} icons allowed.'
    if len(icon_ids) == 0:
        return None, 'No icons selected.'

    # Fetch earned trophies in the order the user selected
    earned_trophies = (
        EarnedTrophy.objects
        .filter(id__in=icon_ids, profile=profile, earned=True, trophy__trophy_type='platinum')
        .select_related('trophy', 'trophy__game', 'trophy__game__concept', 'trophy__game__concept__igdb_match')
    )
    # Build lookup and preserve order
    et_map = {et.id: et for et in earned_trophies}
    ordered = [et_map[eid] for eid in icon_ids if eid in et_map]

    if not ordered:
        return None, 'No valid platinums found.'

    # Auto-calculate columns if not specified
    if not cols or cols < 1:
        cols = _auto_columns(len(ordered))
    cols = min(cols, len(ordered))  # Never more columns than items

    cell_height = _cell_height_for(icon_type)
    width, height, rows = _calculate_grid_dimensions(len(ordered), cols, cell_height)

    # Build icon data for template
    icons = []
    for et in ordered:
        if icon_type == 'trophy':
            img_url = et.trophy.trophy_icon_url or ''
        else:
            img_url = et.trophy.game.display_image_url
        # Cache external images as same-origin temp files
        cached_url = ShareImageCache.fetch_and_cache(img_url) if img_url else ''
        icons.append({
            'image_url': cached_url or img_url,
            'game_name': et.trophy.game.title_name,
        })

    context = {
        'icons': icons,
        'cols': cols,
        'rows': rows,
        'width': width,
        'height': height,
        'cell_width': CELL_WIDTH,
        'cell_height': cell_height,
        'cell_gap': CELL_GAP,
        'header_height': HEADER_HEIGHT,
        'footer_height': FOOTER_HEIGHT,
        'padding': PADDING,
        'section_gap': SECTION_GAP,
        'username': profile.display_psn_username or profile.psn_username,
        'total_plats': len(ordered),
        'icon_type': icon_type,
        'theme_key': theme_key or 'default',
    }
    return context, None


class PlatinumGridHTMLView(LoginRequiredAPIMixin, APIView):
    """
    GET /api/v1/shareables/platinum-grid/html/

    Returns rendered HTML preview for the platinum grid.
    Query params: icon_ids, icon_type (game|trophy), cols, theme
    """
    authentication_classes = [SessionAuthentication]

    @method_decorator(ratelimit(key='user', rate='60/m', method='GET', block=True))
    def get(self, request):
        profile = getattr(request.user, 'profile', None)
        if not profile:
            return Response({'error': 'No profile found.'}, status=http_status.HTTP_400_BAD_REQUEST)

        icon_ids = _parse_icon_ids(request.query_params.get('icon_ids', ''))
        icon_type = request.query_params.get('icon_type', 'game')
        if icon_type not in ('game', 'trophy'):
            icon_type = 'game'

        cols_raw = request.query_params.get('cols', '')
        cols = int(cols_raw) if cols_raw and cols_raw.isdigit() else 0

        theme_key = request.query_params.get('theme', 'default')

        context, error = _build_grid_context(request, profile, icon_ids, icon_type, cols, theme_key)
        if error:
            return Response({'error': error}, status=http_status.HTTP_400_BAD_REQUEST)

        html = render_to_string('shareables/partials/platinum_grid_card.html', context)

        return Response({
            'html': html,
            'width': context['width'],
            'height': context['height'],
        })


class PlatinumGridPNGView(LoginRequiredAPIMixin, APIView):
    """
    GET /api/v1/shareables/platinum-grid/png/

    Renders the platinum grid as a PNG via Playwright and returns it as a download.
    Query params: icon_ids, icon_type (game|trophy), cols, theme
    """
    authentication_classes = [SessionAuthentication]

    @method_decorator(ratelimit(key='user', rate='10/m', method='GET', block=True))
    def get(self, request):
        profile = getattr(request.user, 'profile', None)
        if not profile:
            return Response({'error': 'No profile found.'}, status=http_status.HTTP_400_BAD_REQUEST)

        icon_ids = _parse_icon_ids(request.query_params.get('icon_ids', ''))
        icon_type = request.query_params.get('icon_type', 'game')
        if icon_type not in ('game', 'trophy'):
            icon_type = 'game'

        cols_raw = request.query_params.get('cols', '')
        cols = int(cols_raw) if cols_raw and cols_raw.isdigit() else 0

        theme_key = request.query_params.get('theme', 'default')

        context, error = _build_grid_context(request, profile, icon_ids, icon_type, cols, theme_key)
        if error:
            return Response({'error': error}, status=http_status.HTTP_400_BAD_REQUEST)

        html = render_to_string('shareables/partials/platinum_grid_card.html', context)

        try:
            from core.services.playwright_renderer import render_png
            png_bytes = render_png(
                html,
                format_type='grid',
                theme_key=theme_key,
                grid_width=context['width'],
                grid_height=context['height'],
            )
        except Exception as e:
            logger.exception(f"[PLAT-GRID-PNG] Playwright render failed: {e}")
            return Response(
                {'error': 'Failed to render image. Please try again.'},
                status=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        username = profile.display_psn_username or profile.psn_username
        safe_name = "".join(c for c in username if c.isalnum() or c in (' ', '-', '_')).strip() or 'grid'
        filename = f"platpursuit_platinum_grid_{safe_name}.png"

        response = HttpResponse(png_bytes, content_type='image/png')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response
