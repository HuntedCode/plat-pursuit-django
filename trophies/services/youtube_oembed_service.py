"""
YouTube oEmbed lookup for roadmap video attribution.

Used by the roadmap merge service (on save) and the editor's live-preview
endpoint to resolve a YouTube URL to its channel name + URL. Results are
cached on the model rather than fetched per render. The endpoint requires
no API key and has no documented quota for reasonable use.
"""
import logging
import re

import requests

logger = logging.getLogger('psn_api')

OEMBED_ENDPOINT = 'https://www.youtube.com/oembed'
# Short timeout: the merge service blocks on this during a save and the
# editor's live preview is user-facing. Treat any oEmbed slowness as a
# miss rather than hanging the request.
OEMBED_TIMEOUT_SECONDS = 3.0

# Accepts watch URLs (?v=ID), youtu.be short links, embed/v paths, and
# Shorts. Doesn't try to validate the 11-char ID exhaustively, just enough
# to filter out obviously-non-YouTube URLs before we hit the network.
_YOUTUBE_HOST_RE = re.compile(
    r'^https?://'
    r'(?:www\.|m\.|music\.)?'
    r'(?:youtube\.com/(?:watch|embed/|v/|shorts/)|youtu\.be/)',
    re.IGNORECASE,
)


def is_youtube_url(url):
    """Cheap sanity check before spending a network round-trip."""
    if not url:
        return False
    return bool(_YOUTUBE_HOST_RE.search(url))


def fetch_attribution(url):
    """Resolve a YouTube URL to its channel attribution.

    Returns ``{'channel_name': str, 'channel_url': str}`` on success or
    ``None`` on any failure (non-YouTube URL, network error, non-2xx,
    malformed JSON, missing fields). Callers should treat ``None`` as
    "store the URL but skip attribution"; the embed still works without it.
    """
    if not is_youtube_url(url):
        return None

    try:
        response = requests.get(
            OEMBED_ENDPOINT,
            params={'url': url, 'format': 'json'},
            timeout=OEMBED_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as exc:
        logger.warning('YouTube oEmbed lookup failed for %s: %s', url, exc)
        return None
    except ValueError as exc:
        logger.warning('YouTube oEmbed returned invalid JSON for %s: %s', url, exc)
        return None

    channel_name = (data.get('author_name') or '').strip()
    channel_url = (data.get('author_url') or '').strip()
    if not channel_name:
        return None

    return {
        'channel_name': channel_name,
        'channel_url': channel_url,
    }
