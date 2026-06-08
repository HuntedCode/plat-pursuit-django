"""Recount community badge-platinums and release any newly-unlocked artwork.

Run on a schedule (Render cron, ~every 10-15 min). Idempotent: it reconciles the
released set to the current count each run, so a missed run self-heals on the next.
"""

from django.core.management.base import BaseCommand

from art_reveal.models import ArtRevealEvent
from art_reveal.services import reconcile_event


class Command(BaseCommand):
    help = 'Recount community badge-platinums and release newly-unlocked badge artwork.'

    def handle(self, *args, **options):
        events = list(ArtRevealEvent.objects.filter(is_active=True))
        live = [e for e in events if e.is_live()]
        if not live:
            self.stdout.write('No live art reveal event. Nothing to do.')
            return

        for event in live:
            result = reconcile_event(event)
            released = result['released']
            msg = (
                f"[{event.slug}] count={result['count']} "
                f"target={result['target']} "
                f"released_now={len(released)}"
            )
            if released:
                msg += f" (orders {', '.join(str(o) for o in released)})"
            self.stdout.write(self.style.SUCCESS(msg))
