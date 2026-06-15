from django.core.management.base import BaseCommand
from trophies.models import Badge
from trophies.services.badge_refresh_service import refresh_badge_series_awards


class Command(BaseCommand):
    help = (
        "Refresh badge awards + leaderboards for a single badge series (--series <slug>) "
        "or for every badge series in the DB (--all)."
    )

    def add_arguments(self, parser):
        parser.add_argument('--series', type=str, help='Refresh a single badge series by slug.')
        parser.add_argument(
            '--all', action='store_true', dest='all_series',
            help='Refresh every distinct badge series in the DB.',
        )

    def handle(self, *args, **options):
        series_slug = options['series']
        all_series = options['all_series']

        if all_series:
            if series_slug:
                self.stdout.write(self.style.WARNING("--all overrides --series; ignoring --series."))
            slugs = list(
                Badge.objects
                .exclude(series_slug__isnull=True).exclude(series_slug='')
                .values_list('series_slug', flat=True)
                .distinct().order_by('series_slug')
            )
            self.stdout.write(self.style.MIGRATE_HEADING(f"Refreshing ALL {len(slugs)} badge series."))
            for i, slug in enumerate(slugs, 1):
                try:
                    self._refresh_series(slug, position=f"[{i}/{len(slugs)}]")
                except Exception as e:
                    # One bad series shouldn't abort the whole sweep.
                    self.stdout.write(self.style.ERROR(f"Series '{slug}' failed: {e}"))
            self.stdout.write(self.style.SUCCESS(f"Finished refreshing {len(slugs)} badge series."))
            return

        if not series_slug:
            self.stdout.write(self.style.ERROR("Provide --series <slug> or --all."))
            return

        self._refresh_series(series_slug)

    def _refresh_series(self, series_slug, position=''):
        prefix = f"{position} " if position else ''
        self.stdout.write(self.style.MIGRATE_HEADING(f"{prefix}--- BEGIN series '{series_slug}' ---"))

        # Shared with DLC detection -- see trophies/services/badge_refresh_service.py.
        processed, profiles_changed, earners_count, progress_count = refresh_badge_series_awards(series_slug)

        if processed == 0:
            self.stdout.write(self.style.WARNING(f"Nothing to process for series '{series_slug}' (no badges or no earners)."))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"Processed {processed} badge-profile pairs for series '{series_slug}' "
                f"({profiles_changed} profiles changed)."
            ))
            self.stdout.write(f"Rebuilt leaderboards for {series_slug}: {earners_count} earners, {progress_count} progress")

        self.stdout.write(self.style.SUCCESS(f"{prefix}--- END series '{series_slug}' ---"))
