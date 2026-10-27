from django.http import Http404
from django.views.generic import TemplateView

from .models import ArtRevealEvent


class ArtRevealEventView(TemplateView):
    """Public event page. Reads the cheap stored counter, never recomputes.

    WHILE AN EVENT RUNS: how it works, a carousel of the revealed artwork, progress to the next reveal, and the full
    grid (locked tiles for art not yet out). ONCE EVERY PIECE IS OUT the page is the event's gallery: the grid alone,
    with no rules or meter inviting a hunter to unlock what is already unlocked (owner, 2026-10-27, Odds and ends
    review OE-04). The most recent event is shown when none is active. With no event ever, there is no page (404):
    a page whose only content is "nothing is running" is absence shown as content (Page Principles rule 3)."""

    template_name = 'art_reveal/event.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        event = (
            ArtRevealEvent.objects.filter(is_active=True).order_by('-started_at').first()
            or ArtRevealEvent.objects.order_by('-started_at').first()
        )
        if not event:
            raise Http404('No badge art reveal event yet.')
        ctx['event'] = event

        items = list(
            event.items.select_related('series', 'series__funded_by').order_by('order')
        )
        released = [i for i in items if i.released]
        ctx.update({
            'progress': event.progress(),
            'items': items,
            'released_items': released,
        })
        return ctx
