import uuid
import time
import base64
import logging
import requests
from pathlib import Path
from urllib.parse import urlparse

from django.conf import settings

logger = logging.getLogger(__name__)

# Directory for temp share images (local filesystem, not in media/ to avoid S3)
SHARE_TEMP_DIR = Path(settings.BASE_DIR) / 'share_temp_images'


class ShareImageCache:
    """
    Fetches external images and saves them as temporary local files.
    Returns same-origin URLs that work reliably on iOS Safari
    (unlike base64 data URIs which intermittently fail in html2canvas).
    """

    @staticmethod
    def fetch_and_cache(url):
        """
        Fetch an external image URL and save to temp directory.
        Returns the serve path (e.g., '/api/v1/share-temp/<uuid>.png')
        or empty string on failure.
        """
        if not url:
            return ''

        try:
            parsed = urlparse(url)
            if parsed.scheme not in ('http', 'https'):
                logger.warning(f"[SHARE-CACHE] Invalid URL scheme: {url}")
                return ''

            response = requests.get(url, timeout=10, headers={
                'User-Agent': 'Mozilla/5.0 (compatible; PlatPursuit/1.0)'
            })
            response.raise_for_status()

            content_type = response.headers.get('Content-Type', 'image/png')
            ext = '.png'
            if 'jpeg' in content_type or 'jpg' in content_type:
                ext = '.jpg'
            elif 'webp' in content_type:
                ext = '.webp'

            SHARE_TEMP_DIR.mkdir(parents=True, exist_ok=True)

            filename = f"{uuid.uuid4().hex}{ext}"
            filepath = SHARE_TEMP_DIR / filename
            filepath.write_bytes(response.content)

            return f"/api/v1/share-temp/{filename}"

        except requests.RequestException as e:
            logger.warning(f"[SHARE-CACHE] Failed to fetch image {url}: {e}")
            return ''
        except Exception as e:
            logger.exception(f"[SHARE-CACHE] Error caching image {url}")
            return ''

    @staticmethod
    def cache_local_file(file_path):
        """
        Copy a local file (media/static) to the temp directory.
        Returns the serve path or empty string on failure.
        """
        try:
            source = Path(file_path)
            if not source.exists():
                return ''

            SHARE_TEMP_DIR.mkdir(parents=True, exist_ok=True)

            ext = source.suffix or '.png'
            filename = f"{uuid.uuid4().hex}{ext}"
            filepath = SHARE_TEMP_DIR / filename
            filepath.write_bytes(source.read_bytes())

            return f"/api/v1/share-temp/{filename}"

        except Exception as e:
            logger.exception(f"[SHARE-CACHE] Error caching local file {file_path}")
            return ''

    @staticmethod
    def local_file_to_base64(file_path):
        """
        Convert a local file to a base64 data URI.
        Used for small local files (badge images) where base64 is acceptable.
        """
        try:
            source = Path(file_path)
            if not source.exists():
                return ''

            ext = source.suffix.lower()
            mime_types = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp'}
            mime_type = mime_types.get(ext, 'image/png')

            image_data = base64.b64encode(source.read_bytes()).decode('utf-8')
            return f"data:{mime_type};base64,{image_data}"

        except Exception as e:
            logger.warning(f"[SHARE-CACHE] Failed to convert local file to base64: {file_path}: {e}")
            return ''

    @staticmethod
    def cleanup(max_age_seconds=3600):
        """Delete temp files older than max_age_seconds. Returns count of deleted files."""
        if not SHARE_TEMP_DIR.exists():
            return 0

        cutoff = time.time() - max_age_seconds
        count = 0
        for f in SHARE_TEMP_DIR.iterdir():
            if f.is_file() and f.name != '.gitkeep' and f.stat().st_mtime < cutoff:
                try:
                    f.unlink()
                    count += 1
                except OSError as e:
                    logger.warning(f"[SHARE-CACHE] Failed to delete {f}: {e}")
        return count
