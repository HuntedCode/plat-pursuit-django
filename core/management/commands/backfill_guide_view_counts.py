"""
One-time management command to backfill guide view counts.

The page_type rename from 'checklist' to 'guide' left a gap where
_increment_parent_view_count() was still checking for 'checklist',
so Checklist.view_count stopped incrementing even though PageView
records were still being created correctly.

This command reconciles Checklist.view_count from actual PageView data.
"""
from django.core.management.base import BaseCommand
from django.db.models import Count

from core.models import PageView
from trophies.models import Checklist


class Command(BaseCommand):
    help = "Backfill guide view counts from PageView records"

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be updated without making changes',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        if dry_run:
            self.stdout.write(self.style.WARNING("--- DRY RUN MODE ---\n"))

        # Count PageView records per guide
        counts = (
            PageView.objects
            .filter(page_type='guide')
            .values('object_id')
            .annotate(total=Count('id'))
        )

        updated = 0
        for entry in counts:
            try:
                checklist = Checklist.objects.get(id=int(entry['object_id']))
            except (Checklist.DoesNotExist, ValueError):
                self.stdout.write(self.style.WARNING(
                    f"  Skipping object_id={entry['object_id']} (no matching Checklist)"
                ))
                continue

            old_count = checklist.view_count
            new_count = entry['total']

            if old_count != new_count:
                self.stdout.write(
                    f"  {checklist.title} (id={checklist.id}): "
                    f"{old_count} -> {new_count}"
                )
                if not dry_run:
                    Checklist.objects.filter(id=checklist.id).update(view_count=new_count)
                updated += 1

        if dry_run:
            self.stdout.write(self.style.WARNING(f"\nWould update {updated} guide(s)"))
        else:
            self.stdout.write(self.style.SUCCESS(f"\nUpdated {updated} guide(s)"))
