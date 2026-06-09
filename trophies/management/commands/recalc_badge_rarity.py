"""Recompute badge rarity (pct / class / rank) from current earner counts.

The earner base and linked-profile count drift slowly, so this runs on a schedule
rather than per-event. Pure recompute over a bounded set of badges; safe to re-run.
"""

from django.core.management.base import BaseCommand

from trophies.models import Badge


class Command(BaseCommand):
    help = "Recompute badge rarity (pct/class/rank) from current earner counts."

    def handle(self, *args, **options):
        result = Badge.recompute_rarity()
        self.stdout.write(self.style.SUCCESS(
            f"Recomputed rarity for {result['badges']} badges "
            f"({result['live_ranked']} live ranked) over "
            f"{result['linked_profiles']} PSN-linked profiles."
        ))
