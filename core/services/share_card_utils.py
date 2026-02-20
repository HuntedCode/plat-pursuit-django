"""
Shared utilities for share card image generation.
Used by notification_views.py and shareable_views.py.
"""
from datetime import datetime

from core.services.share_image_cache import SHARE_TEMP_DIR, ShareImageCache


def to_int(value, default=0):
    """Safely convert value to int."""
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def format_share_date(iso_string):
    """
    Format an ISO date string to a readable format.
    Example: "2024-01-15T14:30:00" -> "Jan 15, 2024"
    """
    if not iso_string:
        return ''
    try:
        if 'T' in iso_string:
            dt = datetime.fromisoformat(iso_string.replace('Z', '+00:00'))
        else:
            dt = datetime.fromisoformat(iso_string)
        return dt.strftime('%b %d, %Y')
    except (ValueError, TypeError):
        return ''


def process_badge_images(badges):
    """
    Process badge images for share image rendering.
    Converts badge image URLs to same-origin temp files or base64 data URLs.

    Handles three types of paths:
    1. Full URLs (http/https): cached as same-origin temp files
    2. Media paths (/media/...): converted to base64
    3. Static paths (images/...): converted to base64
    """
    if not badges:
        return []

    processed = []
    default_badge_image = None

    for badge in badges:
        badge_copy = dict(badge)
        badge_image_url = badge_copy.get('badge_image_url', '')

        if badge_image_url:
            # Case 1: Full URL
            if badge_image_url.startswith(('http://', 'https://')):
                cached_url = ShareImageCache.fetch_and_cache(badge_image_url)
                if cached_url:
                    badge_copy['badge_image_url'] = cached_url
                else:
                    # Fetch failed: fall through to default badge image
                    if default_badge_image is None:
                        from django.contrib.staticfiles import finders
                        default_path = finders.find('images/badges/default.png')
                        if default_path:
                            default_badge_image = ShareImageCache.local_file_to_base64(default_path)
                        else:
                            default_badge_image = ''
                    badge_copy['badge_image_url'] = default_badge_image

            # Case 2: Media file path
            elif badge_image_url.startswith('/media/'):
                from django.conf import settings
                relative_path = badge_image_url[len('/media/'):]
                file_path = settings.MEDIA_ROOT / relative_path
                data_uri = ShareImageCache.local_file_to_base64(str(file_path))
                if data_uri:
                    badge_copy['badge_image_url'] = data_uri

            # Case 3: Static file path
            else:
                from django.contrib.staticfiles import finders
                static_path = finders.find(badge_image_url)
                if static_path:
                    badge_copy['badge_image_url'] = ShareImageCache.local_file_to_base64(static_path)
        else:
            # No image URL: use default badge image
            if default_badge_image is None:
                from django.contrib.staticfiles import finders
                default_path = finders.find('images/badges/default.png')
                if default_path:
                    default_badge_image = ShareImageCache.local_file_to_base64(default_path)
                else:
                    default_badge_image = ''
            badge_copy['badge_image_url'] = default_badge_image

        processed.append(badge_copy)

    return processed


def resolve_temp_path(serve_path):
    """Convert a /api/v1/share-temp/<file> path to an absolute filesystem path."""
    if not serve_path or not serve_path.startswith('/api/v1/share-temp/'):
        return None
    filename = serve_path.split('/')[-1]
    full_path = SHARE_TEMP_DIR / filename
    return str(full_path) if full_path.exists() else None
