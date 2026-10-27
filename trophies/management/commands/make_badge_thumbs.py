"""Backfill the 512px WebP display copies of badge art (SW-06; trophies/services/badge_thumbs.py).

New uploads get their copy from `BadgeSeries.save()` / `GroupBadge.save()`. This command makes the copies for
art that predates them, and repairs any row whose source changed without a save (a queryset `.update()`, a data
fix) or whose render failed. Until a row has a current copy its medallions draw the full upload, so running it is
a speed-up, never a correctness step.

Idempotent: a row whose copy is current is skipped without touching storage. Every copy it does write is
rendered from the source, never reused from an existing file. Run once after the deploy that adds the fields;
safe to rerun any time.

    python manage.py make_badge_thumbs            # make what is missing or stale
    python manage.py make_badge_thumbs --dry-run  # count only
    python manage.py make_badge_thumbs --force    # re-render every copy (after art was overwritten in storage)
"""
from django.core.management.base import BaseCommand

from trophies.models import BadgeSeries, GroupBadge
from trophies.services.badge_thumbs import sync_thumb, thumb_is_current

# (model, source field, display-copy field)
TARGETS = (
    (BadgeSeries, 'badge_image', 'badge_thumb'),
    (GroupBadge, 'badge_image_override', 'badge_override_thumb'),
)


class Command(BaseCommand):
    help = 'Make the display-size WebP copies of badge art that are missing or stale.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Count what would be made; write nothing.')
        parser.add_argument('--force', action='store_true',
                            help='Re-render every copy, current or not (art overwritten in storage outside the app).')

    def handle(self, *args, **options):
        dry, force = options['dry_run'], options['force']
        for model, source_attr, thumb_attr in TARGETS:
            rows = model.objects.exclude(**{source_attr: ''}).exclude(**{f'{source_attr}__isnull': True})
            todo = [r for r in rows.only('pk', source_attr, thumb_attr).iterator()
                    if force or not thumb_is_current(getattr(r, source_attr), getattr(r, thumb_attr))]
            label = model.__name__
            if dry:
                self.stdout.write(f'{label}: {len(todo)} to make')
                continue
            made = failed = 0
            for row in todo:
                sync_thumb(row, source_attr, thumb_attr, render=True)
                if thumb_is_current(getattr(row, source_attr), getattr(row, thumb_attr)):
                    made += 1
                else:
                    failed += 1
            self.stdout.write(f'{label}: {made} made, {failed} failed (served full size until fixed)')
