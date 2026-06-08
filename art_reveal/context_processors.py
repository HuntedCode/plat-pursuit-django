"""Inject the active Badge Art Reveal event + progress for the site-wide banner.

Mirrors plat_pursuit.context_processors.active_fundraiser: the active-event pk is
cached (60s) inside get_active_event(), and progress() reads the cheap stored
counter, so this adds at most a couple of light queries per render when an event
is live and nothing when it isn't.
"""

from .services import get_active_event


def art_reveal_banner(request):
    event = get_active_event()
    if not event or not event.show_banner():
        return {}
    latest = (
        event.items.filter(released=True)
        .select_related('badge').order_by('-order').first()
    )
    return {
        'art_reveal_event': event,
        'art_reveal_progress': event.progress(),
        'art_reveal_latest': latest,
    }
