"""Recompute Game.played_count from actual ProfileGame counts.

played_count is a denormalized "how many profiles have played this game" counter,
maintained incrementally by the post_save / post_delete signals on ProfileGame.
A historical double-increment in the sync path (now removed from
PsnApiService.create_or_update_profile_game) inflated it, so this one-time
backfill resets every Game to its true ProfileGame count.

Idempotent and safe to re-run. Use --dry-run to preview the number of
corrections. Runs as a single DB-side UPDATE (no per-row Python iteration).
"""
from django.core.management.base import BaseCommand
from django.db.models import Count, F, IntegerField, OuterRef, Subquery, Value
from django.db.models.functions import Coalesce

from trophies.models import Game, ProfileGame


def _actual_count_subquery():
    """Correlated subquery: number of ProfileGame rows for the outer Game."""
    return (
        ProfileGame.objects
        .filter(game=OuterRef("pk"))
        .values("game")
        .annotate(c=Count("id"))
        .values("c")
    )


class Command(BaseCommand):
    help = "Recompute Game.played_count from actual ProfileGame counts."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report how many games would change, without writing.",
        )

    def handle(self, *args, **options):
        actual = _actual_count_subquery()
        true_count = Coalesce(Subquery(actual, output_field=IntegerField()), Value(0))

        total = Game.objects.count()
        mismatched = (
            Game.objects.annotate(actual_count=true_count)
            .exclude(played_count=F("actual_count"))
            .count()
        )

        if options["dry_run"]:
            self.stdout.write(
                f"[dry-run] {mismatched} of {total} games have an incorrect "
                f"played_count and would be corrected."
            )
            return

        Game.objects.update(played_count=true_count)
        self.stdout.write(
            self.style.SUCCESS(
                f"Recomputed played_count for {total} games "
                f"({mismatched} were incorrect)."
            )
        )
