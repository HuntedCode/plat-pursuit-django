from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils.text import slugify
from trophies.models import Concept


class Command(BaseCommand):
    help = "Generate slugs for Concepts that don't have one yet"

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Preview how many concepts would be affected without making changes',
        )
        parser.add_argument(
            '--batch-size', type=int, default=100,
            help='Number of concepts to update per batch (default: 100)',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        batch_size = options['batch_size']

        missing = Concept.objects.filter(Q(slug__isnull=True) | Q(slug=''))
        total = missing.count()

        if total == 0:
            self.stdout.write("All concepts already have slugs. Nothing to do.")
            return

        if dry_run:
            self.stdout.write(f"[DRY RUN] {total} concept(s) missing slugs. No changes made.")
            return

        updated = 0
        skipped = 0
        errors = 0
        batch = []
        # Track slugs assigned in this run to prevent collisions within a batch.
        # The DB check alone misses conflicts between concepts in the same
        # uncommitted batch (e.g. two concepts both slugifying to "ps4-psvita").
        used_slugs = set(
            Concept.objects.filter(slug__isnull=False)
            .exclude(slug='')
            .values_list('slug', flat=True)
        )

        for concept in missing.iterator():
            try:
                if not concept.unified_title:
                    base_slug = f"concept-{concept.concept_id}"
                else:
                    base_slug = slugify(concept.unified_title)[:280]
                    if not base_slug:
                        base_slug = f"concept-{concept.concept_id}"

                slug = base_slug
                counter = 1
                while slug in used_slugs:
                    slug = f"{base_slug}-{counter}"
                    counter += 1

                concept.slug = slug
                used_slugs.add(slug)
                batch.append(concept)
                updated += 1
                self.stdout.write(f"  [{updated}/{total}] {concept.concept_id}: slug='{slug}'")

                if len(batch) >= batch_size:
                    Concept.objects.bulk_update(batch, ['slug'])
                    batch = []

            except Exception as e:
                errors += 1
                self.stdout.write(self.style.ERROR(
                    f"  Error processing concept {concept.id} ({concept.concept_id}): {e}"
                ))

        if batch:
            Concept.objects.bulk_update(batch, ['slug'])

        summary = f"Backfill complete. {updated} slug(s) generated"
        if skipped:
            summary += f", {skipped} skipped"
        if errors:
            summary += f", {errors} error(s)"
        self.stdout.write(self.style.SUCCESS(summary))
