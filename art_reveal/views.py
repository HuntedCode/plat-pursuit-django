from django.views.generic import TemplateView

from .models import ArtRevealEvent


class ArtRevealEventView(TemplateView):
    """Public event page: hero of the most recent reveal, a carousel of all
    revealed artwork, progress to the next reveal, and a full grid (locked tiles
    for art not yet revealed). Reads the cheap stored counter, never recomputes."""

    template_name = 'art_reveal/event.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        event = (
            ArtRevealEvent.objects.filter(is_active=True).order_by('-started_at').first()
            or ArtRevealEvent.objects.order_by('-started_at').first()
        )
        ctx['event'] = event
        if not event:
            return ctx

        items = list(
            event.items.select_related(
                'badge', 'badge__base_badge',
                'badge__funded_by', 'badge__base_badge__funded_by',
            ).order_by('order')
        )
        released = [i for i in items if i.released]
        ctx.update({
            'progress': event.progress(),
            'items': items,
            'released_items': released,
            'latest': released[-1] if released else None,
            'next_item': next((i for i in items if not i.released), None),
        })
        return ctx
