"""Celebration moments on My Pursuit pages (trophies/services/moment_service.py).

`{% moments_overlay %}` (base.html) renders the player and its queued moments; `{% moments_pill %}` (the My
Pursuit strip) renders the button that opens the platinum reel, or everything when auto-play is off. Both read
`moment_service.for_request`, one indexed read memoised on the request, and render nothing when nothing waits.
base.html calls them only on My Pursuit pages, so every other page pays nothing at all.
"""
from django import template

from trophies.models import Moment
from trophies.services import moment_service

register = template.Library()


def _autoplay(request):
    return bool(getattr(request.user, 'play_celebrations', True))


@register.inclusion_tag('partials/moments/_overlay.html', takes_context=True)
def moments_overlay(context):
    request = context.get('request')
    moments = moment_service.for_request(request) if request is not None else []
    if not moments:
        return {'view': None}
    return {'view': moment_service.build_view(request.user.profile, moments, autoplay=_autoplay(request))}


@register.inclusion_tag('partials/moments/_pill.html', takes_context=True)
def moments_pill(context, variant='desktop'):
    """What the pill says and opens. With auto-play on, only the reel waits for a tap (badges play by
    themselves), so the pill counts platinums. With it off, everything waits, so the pill counts it all."""
    request = context.get('request')
    moments = moment_service.for_request(request) if request is not None else []
    platinums = sum(1 for m in moments if m.kind == Moment.KIND_PLATINUM)
    reel = sum(1 for m in moments if m.kind in moment_service.REEL_KINDS)
    if not moments:
        return {'pill': None}
    if _autoplay(request):
        if not reel:
            return {'pill': None}
        if platinums:
            label = f'{platinums} new platinum{"" if platinums == 1 else "s"}'
        else:
            label = 'New milestone'
        return {'pill': {'label': label, 'opens': 'reel'}, 'variant': variant}
    return {'pill': {'label': f'{len(moments)} new', 'opens': 'all'}, 'variant': variant}
