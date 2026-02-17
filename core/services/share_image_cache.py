import uuid
import hashlib
import time
import random
import base64
import logging
import threading
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

    Uses deterministic filenames (MD5 hash of URL) so files cached by
    one Gunicorn worker can be reused by another without shared state.
    """

    # In-memory URL deduplication: url -> (cached_path, timestamp)
    _url_cache = {}
    _url_cache_lock = threading.Lock()
    _cache_ttl = 1800  # 30 minutes

    @staticmethod
    def _deterministic_filename(url, ext):
        """Generate a deterministic filename from a URL using MD5 hash."""
        url_hash = hashlib.md5(url.encode()).hexdigest()
        return f"{url_hash}{ext}"

    @staticmethod
    def _maybe_cleanup():
        """Opportunistic cleanup: ~2% chance per fetch, runs in-process on the web server."""
        if random.random() < 0.02:
            ShareImageCache.cleanup(max_age_seconds=14400)

    @staticmethod
    def fetch_and_cache(url):
        """
        Fetch an external image URL and save to temp directory.
        Returns the serve path (e.g., '/api/v1/share-temp/<hash>.png')
        or empty string on failure.

        Uses deterministic filenames so cached files persist across
        Gunicorn workers. Also uses an in-memory cache as a fast path
        for same-worker requests within the TTL window.
        """
        if not url:
            return ''

        # Check in-memory cache first (fast path for same-worker)
        with ShareImageCache._url_cache_lock:
            if url in ShareImageCache._url_cache:
                cached_path, ts = ShareImageCache._url_cache[url]
                if time.time() - ts < ShareImageCache._cache_ttl:
                    filename = cached_path.split('/')[-1]
                    if (SHARE_TEMP_DIR / filename).exists():
                        return cached_path
                # Expired or file missing: remove stale entry
                del ShareImageCache._url_cache[url]

        try:
            parsed = urlparse(url)
            if parsed.scheme not in ('http', 'https'):
                logger.warning(f"[SHARE-CACHE] Invalid URL scheme: {url}")
                return ''

            # Determine extension from URL path for deterministic filename
            url_path = parsed.path.lower()
            if 'jpeg' in url_path or 'jpg' in url_path:
                ext = '.jpg'
            elif 'webp' in url_path:
                ext = '.webp'
            else:
                ext = '.png'

            filename = ShareImageCache._deterministic_filename(url, ext)
            filepath = SHARE_TEMP_DIR / filename
            serve_path = f"/api/v1/share-temp/{filename}"

            # Check if file already exists on disk (cross-worker cache hit)
            if filepath.exists():
                # Touch the file to refresh its mtime (prevents cleanup)
                filepath.touch()
                with ShareImageCache._url_cache_lock:
                    ShareImageCache._url_cache[url] = (serve_path, time.time())
                return serve_path

            # File not on disk: download from external URL
            response = requests.get(url, timeout=10, headers={
                'User-Agent': 'Mozilla/5.0 (compatible; PlatPursuit/1.0)'
            })
            response.raise_for_status()

            # Re-check extension from Content-Type header (more reliable)
            content_type = response.headers.get('Content-Type', 'image/png')
            if 'jpeg' in content_type or 'jpg' in content_type:
                actual_ext = '.jpg'
            elif 'webp' in content_type:
                actual_ext = '.webp'
            else:
                actual_ext = '.png'

            # If Content-Type gives a different ext, use that instead
            if actual_ext != ext:
                filename = ShareImageCache._deterministic_filename(url, actual_ext)
                filepath = SHARE_TEMP_DIR / filename
                serve_path = f"/api/v1/share-temp/{filename}"

            SHARE_TEMP_DIR.mkdir(parents=True, exist_ok=True)
            filepath.write_bytes(response.content)

            # Store in cache for future lookups
            with ShareImageCache._url_cache_lock:
                ShareImageCache._url_cache[url] = (serve_path, time.time())

            ShareImageCache._maybe_cleanup()
            return serve_path

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
    def cleanup(max_age_seconds=14400):
        """Delete temp files older than max_age_seconds (default 4 hours). Returns count of deleted files."""
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
