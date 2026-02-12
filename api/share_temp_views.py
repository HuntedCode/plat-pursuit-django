import re

from django.http import FileResponse, Http404

from core.services.share_image_cache import SHARE_TEMP_DIR


def serve_share_temp_image(request, filename):
    """
    Serve a temporary cached share image by UUID filename.
    Images are created by ShareImageCache when generating share card HTML.
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
