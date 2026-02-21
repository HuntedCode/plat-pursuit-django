from django.core.management.base import BaseCommand
from django.db.models import Q

from trophies.models import Concept


class Command(BaseCommand):
    help = "Backfill concept_icon_url for existing PP_ stub concepts from their associated games"

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Preview how many concepts would be updated without making changes')
        parser.add_argument('--batch-size', type=int, default=100, help='Number of concepts to update per batch (default: 100)')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        batch_size = options['batch_size']

        stubs = Concept.objects.filter(
            concept_id__startswith='PP_',
        ).filter(
            Q(concept_icon_url__isnull=True) | Q(concept_icon_url=''),
        )
        total = stubs.count()

        if total == 0:
            self.stdout.write("All PP_ stub concepts already have icons. Nothing to do.")
            return

        if dry_run:
            self.stdout.write(f"[DRY RUN] {total} PP_ concept(s) missing icons. No changes made.")
            return

        updated = 0
        skipped = 0
        batch = []

        for concept in stubs.iterator():
            game = (
                concept.games
                .exclude(Q(title_icon_url__isnull=True) | Q(title_icon_url=''))
                .first()
            )

            if not game:
                skipped += 1
                self.stdout.write(f"  SKIP {concept.concept_id}: no game with icon data")
                continue

            concept.concept_icon_url = game.title_icon_url
            batch.append(concept)
            updated += 1
            self.stdout.write(f"  [{updated}/{total}] {concept.concept_id}: icon set")

            if len(batch) >= batch_size:
                Concept.objects.bulk_update(batch, ['concept_icon_url'])
                batch = []

        if batch:
            Concept.objects.bulk_update(batch, ['concept_icon_url'])

        self.stdout.write(self.style.SUCCESS(
            f"Backfill complete. {updated} concept(s) updated, {skipped} skipped (no game icon data)."
        ))
