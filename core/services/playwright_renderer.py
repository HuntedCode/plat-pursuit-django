"""
Server-side share card PNG renderer using Playwright (headless Chromium).

Eliminates iOS Safari html2canvas bugs by rendering HTML to PNG on the server.
The same HTML/CSS templates used for preview are rendered here — only the
screenshot step moves server-side.

All images and fonts are embedded as base64 data URIs so Chromium can render
them from set_content() (which runs in about:blank origin with no file:// access).

Playwright runs in a dedicated daemon thread to avoid polluting Django's
event loop (sync_playwright starts an asyncio loop which triggers Django's
SynchronousOnlyOperation guard on subsequent ORM queries).
"""
import io
import re
import base64
import mimetypes
import logging
import concurrent.futures
from pathlib import Path

from django.conf import settings
from PIL import Image

from trophies.themes import GRADIENT_THEMES, _clean_css

logger = logging.getLogger(__name__)

# Paths
STATIC_ROOT = Path(settings.STATIC_ROOT) if settings.STATIC_ROOT else Path(settings.BASE_DIR) / 'static'
SHARE_TEMP_DIR = Path(settings.BASE_DIR) / 'share_temp_images'
FONTS_DIR = STATIC_ROOT / 'fonts'

# Card dimensions
DIMENSIONS = {
    'landscape': (1200, 630),
    'portrait': (1080, 1350),
}

# Playwright runs in a single dedicated thread to keep its asyncio loop
# isolated from Django's request threads. max_workers=1 means exactly one
# thread is created (reused for all renders) and renders are serialized.
_pw_thread_browser = None  # Only accessed from the Playwright thread
_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix='playwright')

# Cached font faces CSS string (built once per process, fonts never change)
_cached_font_faces = None


def _file_to_data_uri(file_path):
    """Read a local file and return a base64 data URI, or empty string on failure."""
    try:
        p = Path(file_path)
        if not p.exists():
            logger.warning(f"[PLAYWRIGHT] File not found: {file_path}")
            return ''
        data = p.read_bytes()
        mime, _ = mimetypes.guess_type(str(p))
        if not mime:
            ext = p.suffix.lower()
            mime = {
                '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
                '.webp': 'image/webp', '.svg': 'image/svg+xml',
                '.ttf': 'font/ttf', '.woff': 'font/woff', '.woff2': 'font/woff2',
            }.get(ext, 'application/octet-stream')
        b64 = base64.b64encode(data).decode('ascii')
        return f'data:{mime};base64,{b64}'
    except Exception:
        logger.exception(f"[PLAYWRIGHT] Failed to read file: {file_path}")
        return ''


def _file_to_data_uri_resized(file_path, max_size=200):
    """
    Read a local image file, resize if larger than max_size, and return
    a base64 data URI. Saves as JPEG (quality 85) for opaque images or
    PNG for images with alpha channel. Falls back to _file_to_data_uri
    on error.
    """
    try:
        p = Path(file_path)
        if not p.exists():
            logger.warning(f"[PLAYWRIGHT] File not found for resize: {file_path}")
            return ''

        img = Image.open(p)

        # Skip non-raster formats (SVG, fonts)
        if img.format not in ('PNG', 'JPEG', 'WEBP', 'GIF', None):
            return _file_to_data_uri(file_path)

        # Resize if either dimension exceeds max_size
        if img.width > max_size or img.height > max_size:
            img.thumbnail((max_size, max_size), Image.LANCZOS)

        buf = io.BytesIO()
        has_alpha = img.mode in ('RGBA', 'LA', 'PA')

        if has_alpha:
            img.save(buf, format='PNG', optimize=True)
            mime = 'image/png'
        else:
            if img.mode not in ('RGB',):
                img = img.convert('RGB')
            img.save(buf, format='JPEG', quality=85, optimize=True)
            mime = 'image/jpeg'

        b64 = base64.b64encode(buf.getvalue()).decode('ascii')
        return f'data:{mime};base64,{b64}'

    except Exception:
        logger.exception(f"[PLAYWRIGHT] Failed to resize image: {file_path}")
        return _file_to_data_uri(file_path)


def _ensure_browser():
    """Lazy-init a persistent Chromium browser. Called ONLY from the Playwright thread."""
    global _pw_thread_browser
    if _pw_thread_browser is not None and _pw_thread_browser.is_connected():
        return _pw_thread_browser

    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    _pw_thread_browser = pw.chromium.launch(
        headless=True,
        args=['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage'],
    )
    logger.info("[PLAYWRIGHT] Chromium browser launched in dedicated thread")
    return _pw_thread_browser


def _render_in_thread(full_html, width, height):
    """
    Perform the actual Playwright render. Runs in the dedicated Playwright thread.
    Returns PNG bytes.
    """
    browser = _ensure_browser()
    page = browser.new_page(viewport={'width': width, 'height': height})

    try:
        page.set_content(full_html, wait_until='load')

        # Screenshot the card element (or full page if element not found)
        card = page.query_selector('.share-image-content')
        if card:
            png_bytes = card.screenshot(type='png')
        else:
            png_bytes = page.screenshot(type='png', full_page=False)

        return png_bytes
    finally:
        page.close()


def _resolve_urls(html, resize_images=False):
    """
    Convert relative URLs in HTML to inline base64 data URIs.

    Handles:
    - /api/v1/share-temp/<file> -> data:image/...;base64,...
    - /static/<path> -> data:image/...;base64,...
    - data: URIs left unchanged

    When resize_images=True, share-temp images (external game icons/avatars)
    are resized to 200px max dimension and compressed before embedding.
    This dramatically reduces HTML size for cards with many images (e.g., A-Z
    card with 26 game icons goes from ~7MB to ~0.3MB).
    """
    # Replace /api/v1/share-temp/<filename> with base64 data URIs
    def replace_share_temp(match):
        filename = match.group(1)
        file_path = SHARE_TEMP_DIR / filename
        if resize_images:
            data_uri = _file_to_data_uri_resized(file_path)
        else:
            data_uri = _file_to_data_uri(file_path)
        return data_uri if data_uri else match.group(0)

    html = re.sub(
        r'/api/v1/share-temp/([a-f0-9]+\.\w+)',
        replace_share_temp,
        html
    )

    # Replace /static/... with base64 data URIs (never resized)
    def replace_static(match):
        path = match.group(1)
        file_path = STATIC_ROOT / path
        data_uri = _file_to_data_uri(file_path)
        return data_uri if data_uri else match.group(0)

    html = re.sub(
        r'/static/([^\s"\'<>]+)',
        replace_static,
        html
    )

    return html


def _build_font_faces():
    """Build @font-face CSS rules with base64-embedded fonts. Cached after first call."""
    global _cached_font_faces
    if _cached_font_faces is not None:
        return _cached_font_faces

    font_faces = []
    font_map = {
        'Inter_24pt-Regular.ttf': ('Inter', 'normal', '400'),
        'Poppins-SemiBold.ttf': ('Poppins', 'normal', '600'),
        'Poppins-Bold.ttf': ('Poppins', 'normal', '700'),
    }

    for filename, (family, style, weight) in font_map.items():
        font_path = FONTS_DIR / filename
        if font_path.exists():
            font_data_uri = _file_to_data_uri(font_path)
            if font_data_uri:
                font_faces.append(f"""
                    @font-face {{
                        font-family: '{family}';
                        src: url('{font_data_uri}') format('truetype');
                        font-style: {style};
                        font-weight: {weight};
                    }}
                """)

    _cached_font_faces = '\n'.join(font_faces)
    return _cached_font_faces


def _get_background_css(theme_key, game_image_path=None, concept_bg_path=None,
                        format_type='landscape'):
    """
    Build CSS background properties for a theme.

    For game art themes, embeds the image as a base64 data URI.
    Returns a CSS string ready to inject into a style block.
    """
    theme = GRADIENT_THEMES.get(theme_key)
    if not theme:
        theme = GRADIENT_THEMES.get('default', {})

    requires_game_image = theme.get('requires_game_image', False)
    game_image_source = theme.get('game_image_source', 'game_image')

    # Portrait cards use top-center positioning so wide game art images
    # show their upper portion (where logos/characters usually are)
    bg_position = 'center top' if format_type == 'portrait' else 'center'

    # All properties need !important to override the template's inline styles
    # (the .share-image-content div has a default background in its style="" attribute)

    # Game art themes need special handling
    if requires_game_image:
        image_path = None
        if game_image_source == 'game_image' and game_image_path:
            image_path = game_image_path
        elif game_image_source == 'concept_bg_url' and concept_bg_path:
            image_path = concept_bg_path

        if image_path:
            data_uri = _file_to_data_uri(image_path)
            if data_uri:
                if game_image_source == 'game_image':
                    # gameArtBlur: blurred, scaled-up game cover
                    css = f"""
                        background: linear-gradient(rgba(0, 0, 0, 0.55), rgba(0, 0, 0, 0.55)), url("{data_uri}") !important;
                        background-size: cover !important;
                        background-position: {bg_position} !important;
                        filter: none;
                    """
                else:
                    # gameArtConceptBg: wide concept art
                    css = f"""
                        background: linear-gradient(rgba(0, 0, 0, 0.45), rgba(0, 0, 0, 0.45)), url("{data_uri}") !important;
                        background-size: cover !important;
                        background-position: {bg_position} !important;
                    """
                return css

    # Standard gradient theme
    styles = [f"background: {_clean_css(theme['background'])} !important"]

    if 'background_size' in theme:
        styles.append(f"background-size: {theme['background_size']} !important")
    if 'background_position' in theme:
        styles.append(f"background-position: {theme['background_position']} !important")
    if 'background_repeat' in theme:
        styles.append(f"background-repeat: {theme['background_repeat']} !important")

    return '; '.join(styles)


def _get_banner_css(theme_key):
    """Build CSS for the platinum banner / recap header element."""
    theme = GRADIENT_THEMES.get(theme_key)
    if not theme:
        theme = GRADIENT_THEMES.get('default', {})

    banner_bg = theme.get('banner_background', '')
    border_color = theme.get('banner_border_color', '#67d1f8')

    return f"background: {banner_bg} !important; border-color: {border_color} !important;"


def _build_full_html(inner_html, width, height, theme_key='default',
                     game_image_path=None, concept_bg_path=None,
                     format_type='landscape'):
    """
    Wrap card HTML in a full HTML document with embedded fonts, reset, and theme CSS.
    All external resources are inlined as base64 data URIs.
    """
    font_faces = _build_font_faces()
    background_css = _get_background_css(
        theme_key, game_image_path, concept_bg_path, format_type=format_type,
    )
    banner_css = _get_banner_css(theme_key)

    # Convert all relative URLs in the card HTML to base64 data URIs.
    # Resize share-temp images (external game icons) to reduce HTML size.
    resolved_html = _resolve_urls(inner_html, resize_images=True)

    # Also resolve any URLs in background CSS (logoBackdrop theme uses /static/images/logo.png)
    # No resize here: background images need full resolution for cover display.
    background_css = _resolve_urls(background_css)

    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        {font_faces}
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            margin: 0;
            padding: 0;
            width: {width}px;
            height: {height}px;
            overflow: hidden;
            font-family: 'Inter', 'Poppins', -apple-system, BlinkMacSystemFont, sans-serif;
        }}
        img {{ display: inline-block; }}

        /* Theme background applied to the card container */
        .share-image-content {{
            {background_css};
            width: {width}px;
            height: {height}px;
        }}

        /* Theme banner styling */
        [data-element="platinum-banner"],
        [data-element="challenge-banner"],
        [data-element="recap-header"] {{
            {banner_css}
        }}
    </style>
</head>
<body>
    {resolved_html}
</body>
</html>"""


def render_png(html, format_type='landscape', theme_key='default',
               game_image_path=None, concept_bg_path=None):
    """
    Render share card HTML to PNG bytes using Playwright.

    All HTML preparation (base64 embedding, theme CSS) runs in the calling thread.
    Only the Playwright browser interaction runs in a dedicated thread to keep
    its asyncio event loop isolated from Django.

    Args:
        html: The inner card HTML (from render_to_string)
        format_type: 'landscape' or 'portrait'
        theme_key: Theme key from GRADIENT_THEMES
        game_image_path: Absolute path to game image file (for game art themes)
        concept_bg_path: Absolute path to concept bg file (for game art themes)

    Returns:
        bytes: PNG image data
    """
    width, height = DIMENSIONS.get(format_type, DIMENSIONS['landscape'])

    # Build the full HTML document (base64 embedding etc.) in the current thread
    full_html = _build_full_html(
        html, width, height,
        theme_key=theme_key,
        game_image_path=game_image_path,
        concept_bg_path=concept_bg_path,
        format_type=format_type,
    )

    # Submit the Playwright render to the dedicated thread and wait for result.
    # This keeps Playwright's asyncio event loop out of Django's request thread.
    future = _executor.submit(_render_in_thread, full_html, width, height)
    png_bytes = future.result(timeout=30)

    logger.info(f"[PLAYWRIGHT] Rendered {format_type} PNG ({len(png_bytes)} bytes)")
    return png_bytes
