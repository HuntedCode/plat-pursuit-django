import re

from django.http import FileResponse, Http404

from core.services.share_image_cache import SHARE_TEMP_DIR


def serve_share_temp_image(request, filename):
    """
    Serve a temporary cached share image by UUID filename.
    Images are created by ShareImageCache when generating share card HTML.

    SECURITY NOTE: This endpoint is intentionally public (no authentication).
    Playwright's headless browser fetches these images via localhost when
    rendering share card PNGs server-side, and cannot pass session cookies.
    The security model relies on:
      1. UUID filenames (32 hex chars): unguessable, providing secrecy-based access control
      2. Strict regex validation: only [a-f0-9]{32}.(png|jpg|webp), preventing path traversal
      3. Short-lived files: temporary cache entries, cleaned up periodically
      4. Scoped directory: only serves from SHARE_TEMP_DIR, not arbitrary filesystem paths
    """
    # Validate filename: UUID hex + extension only (prevents path traversal)
    if not re.match(r'^[a-f0-9]{32}\.(png|jpg|webp)$', filename):
        raise Http404

    filepath = SHARE_TEMP_DIR / filename
    if not filepath.exists():
        raise Http404

    content_types = {
        '.png': 'image/png',
        '.jpg': 'image/jpeg',
        '.webp': 'image/webp',
    }
    content_type = content_types.get(filepath.suffix, 'image/png')

    return FileResponse(open(filepath, 'rb'), content_type=content_type)
