"""
Management command to clean up temporary share card images.

Deletes temp files from share_temp_images/ that are older than the specified max age.
These files are created when users generate share card downloads. With deterministic
filenames, files on disk serve as a cross-worker cache, so a longer retention
(4 hours) reduces redundant PSN CDN downloads.

Recommended cron: Run hourly.
"""
from django.core.management.base import BaseCommand
from core.services.share_image_cache import ShareImageCache


class Command(BaseCommand):
    help = "Delete temporary share card images older than 4 hours"

    def add_arguments(self, parser):
        parser.add_argument(
            '--max-age',
            type=int,
            default=14400,
            help='Max age in seconds (default: 14400 = 4 hours)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be deleted without actually deleting',
        )

    def handle(self, *args, **options):
        max_age = options['max_age']

        if options['dry_run']:
            from core.services.share_image_cache import SHARE_TEMP_DIR
            import time

            if not SHARE_TEMP_DIR.exists():
                self.stdout.write("No temp directory found. Nothing to clean up.")
                return

            cutoff = time.time() - max_age
            count = 0
            for f in SHARE_TEMP_DIR.iterdir():
                if f.is_file() and f.name != '.gitkeep' and f.stat().st_mtime < cutoff:
                    count += 1
                    self.stdout.write(f"  Would delete: {f.name}")

            self.stdout.write(f"\nDry run: {count} files would be deleted.")
            return

        count = ShareImageCache.cleanup(max_age_seconds=max_age)
        self.stdout.write(self.style.SUCCESS(f"Cleaned up {count} temporary share images."))
