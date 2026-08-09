"""Shared helper for the share-image pipeline.

to_int / format_share_date / process_badge_images lived here until the 2026-08 plat card rebuild.
The rebuilt card takes typed values straight from completion_card_service and formats dates in the
template, and its badge line reads the new grouping-badge system rather than compositing legacy
tier images, so all three lost their last caller. (api/profile_card_views.py still mirrors the old
badge-image logic inline for the parked profile card.)
"""
from core.services.share_image_cache import SHARE_TEMP_DIR


def resolve_temp_path(serve_path):
    """Convert a /api/v1/share-temp/<file> path to an absolute filesystem path."""
    if not serve_path or not serve_path.startswith('/api/v1/share-temp/'):
        return None
    filename = serve_path.split('/')[-1]
    full_path = SHARE_TEMP_DIR / filename
    return str(full_path) if full_path.exists() else None
