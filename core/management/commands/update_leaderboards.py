import logging

from django.core.management.base import BaseCommand
from trophies.models import Badge
from trophies.services.redis_leaderboard_service import (
    rebuild_xp_leaderboard,
    rebuild_global_progress_leaderboard,
    rebuild_series_leaderboards,
)

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Rebuild Redis sorted set leaderboard data (reconciliation).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--series',
            type=str,
            help='Rebuild leaderboards for a specific series slug only.',
        )

    def handle(self, *args, **options):
        target_series = options.get('series')

        if target_series:
            self._rebuild_single_series(target_series)
        else:
            self._rebuild_all()

    def _rebuild_single_series(self, slug):
        """Rebuild all leaderboards for a single series."""
        try:
            earners_count, progress_count = rebuild_series_leaderboards(slug)
            self.stdout.write(
                f"Rebuilt sorted sets for {slug}: "
                f"{earners_count} earners, {progress_count} progress"
            )
        except Exception as e:
            logger.exception(f"Failed rebuilding sorted sets for {slug}")
            self.stdout.write(self.style.ERROR(f"Failed rebuilding sorted sets for {slug}: {e}"))

        self.stdout.write(self.style.SUCCESS(f"Rebuilt leaderboards for series: {slug}"))

    def _rebuild_all(self):
        """Full rebuild of all leaderboards (reconciliation)."""
        unique_slugs = list(
            Badge.objects.filter(is_live=True).values_list('series_slug', flat=True).distinct().order_by('series_slug')
        )
        if not unique_slugs:
            self.stdout.write(self.style.ERROR("No series found to update leaderboards."))
            return

        # Global leaderboards
        try:
            count = rebuild_xp_leaderboard()
            self.stdout.write(f"Rebuilt XP sorted set: {count} entries")
        except Exception as e:
            logger.exception("Failed rebuilding XP sorted set")
            self.stdout.write(self.style.ERROR(f"Failed rebuilding XP sorted set: {e}"))

        try:
            count = rebuild_global_progress_leaderboard()
            self.stdout.write(f"Rebuilt global progress sorted set: {count} entries")
        except Exception as e:
            logger.exception("Failed rebuilding global progress sorted set")
            self.stdout.write(self.style.ERROR(f"Failed rebuilding global progress sorted set: {e}"))

        # Per-series leaderboards (earners + progress + community XP)
        success_count = 0
        for slug in unique_slugs:
            try:
                earners_count, progress_count = rebuild_series_leaderboards(slug)
                self.stdout.write(
                    f"Rebuilt sorted sets for {slug}: "
                    f"{earners_count} earners, {progress_count} progress"
                )
                success_count += 1
            except Exception as e:
                logger.exception(f"Failed rebuilding sorted sets for {slug}")

        self.stdout.write(self.style.SUCCESS(
            f"Processed {len(unique_slugs)} series ({success_count} fully successful)."
        ))
