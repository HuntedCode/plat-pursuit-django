"""Backfill Concept.bg_url from IGDB art for concepts missing it.

bg_url is the landscape background image used by the profile banner
picker and share-card backdrops. PSN sets it from GAMEHUB art, but
IGDB-anchored concepts (created by anchor_concepts) never go through
that path, so their bg_url stays empty. That broke the profile banner
feature: the picker filters concepts to bg_url non-empty, so an
anchored concept can't be chosen, and a banner whose
selected_background migrated to an anchored concept renders blank.

The live fix is in `process_match` (fills bg_url from IGDB artwork /
screenshot when empty). This command backfills existing concepts:
any concept with an empty bg_url and a TRUSTED IGDBMatch that has
artwork or screenshot image ids gets bg_url set to a 1080p artwork
(preferred, 16:9 landscape) or a screenshot. Only fills empties;
never overwrites PSN-set art.

Idempotent. --dry-run reports without writing. --limit caps the run.
"""
import time

from django.core.management.base import BaseCommand
from django.db.models import Q

from trophies.models import Concept


class Command(BaseCommand):
    help = (
        "Backfill Concept.bg_url from IGDB artwork/screenshots for concepts "
        "that have none but carry a trusted IGDBMatch with art. Fixes the "
        "profile banner picker for anchored concepts. Idempotent."
    )

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Report counts without writing.')
        parser.add_argument('--limit', type=int, default=None,
                            help='Process at most N concepts.')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        limit = options['limit']
        start = time.time()

        # Art presence is checked in Python (the *_urls helpers handle empty
        # lists) rather than via a JSONField length lookup, which isn't a
        # reliable transform on JSONField.
        qs = (
            Concept.objects.filter(
                Q(bg_url__isnull=True) | Q(bg_url=''),
                igdb_match__status__in=['accepted', 'auto_accepted'],
            )
            .select_related('igdb_match')
            .order_by('pk')
        )

        total = qs.count()
        if total == 0:
            self.stdout.write(self.style.SUCCESS(
                'No concepts need a bg_url backfill.'
            ))
            return
        self.stdout.write(
            f'Scanning {total} concept(s) with no bg_url + a trusted IGDB match.'
        )
        if limit:
            qs = qs[:limit]

        updated = 0
        skipped = 0
        for concept in qs.iterator(chunk_size=200):
            match = concept.igdb_match
            bg = match.artwork_urls(size='1080p') or match.screenshot_urls()
            if not bg:
                skipped += 1
                continue
            if dry_run:
                updated += 1
                continue
            concept.bg_url = bg[0]
            concept.save(update_fields=['bg_url'])
            updated += 1

        elapsed = time.time() - start
        verb = 'Would set' if dry_run else 'Set'
        self.stdout.write(self.style.SUCCESS(
            f'\nDone in {elapsed:.1f}s. {verb} bg_url on {updated} concept(s). '
            f'Skipped {skipped} (no usable art).'
        ))
