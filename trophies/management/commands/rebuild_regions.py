"""Rebuild every trophy list's region label and regional flag from the rules in
trophies/services/region_service.py.

    python manage.py rebuild_regions --dry-run     # counts only
    python manage.py rebuild_regions

Labels come from where each list's title IDs are sold (the store walk), falling back to the dataset
prefix until the walk reaches them. Flags come from the regional rule and only act on walked title IDs,
so a list's flag changes once the walk has answered for it. Region-locked lists are never written.

Runs nightly (core/management/commands/nightly.py), because sync and the store walk only rebuild the
units they touch, while concept merges, re-anchors and IGDB match changes move lists between units
without either.
"""
from django.core.management.base import BaseCommand

from trophies.services import region_service


class Command(BaseCommand):
    help = "Rebuild trophy-list region labels and regional flags from the region rules."

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Count the changes without writing.')

    def handle(self, *args, **opts):
        counts = region_service.rebuild(dry_run=opts['dry_run'])
        verb = 'would change' if opts['dry_run'] else 'changed'
        self.stdout.write(
            f"{counts['lists']:,} lists checked: {verb} {counts['regions']:,} region labels, "
            f"flagged {counts['flagged']:,} regional, unflagged {counts['unflagged']:,}."
        )
