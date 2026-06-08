"""Inject the active Badge Art Reveal banner payload for the site-wide banner.

The payload is a cache of plain primitives (see services.get_active_banner), so
this adds no per-request DB work after a warm cache and nothing at all when no
event is live.
"""

from .services import get_active_banner


def art_reveal_banner(request):
    payload = get_active_banner()
    if not payload:
        return {}
    return {'art_reveal_banner': payload}
