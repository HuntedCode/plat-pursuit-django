from django.core.management.base import BaseCommand
from django.db.models import Count, Q
from trophies.models import Concept, Comment


class Command(BaseCommand):
    help = 'Rebuild comment_count for all Concept records to exclude checklist comments'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be updated without making changes'
        )
        parser.add_argument(
            '--concept-id',
            type=int,
            help='Rebuild count for a specific concept ID only'
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        concept_id = options.get('concept_id')

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - No changes will be made'))

        # Get concepts to process
        if concept_id:
            concepts = Concept.objects.filter(id=concept_id)
            if not concepts.exists():
                self.stdout.write(self.style.ERROR(f'Concept with ID {concept_id} not found'))
                return
        else:
            concepts = Concept.objects.all()

        total_concepts = concepts.count()
        updated_count = 0
        unchanged_count = 0
        errors = []

        self.stdout.write(f'Processing {total_concepts} concepts...')

        for concept in concepts:
            try:
                # Calculate correct comment count (only concept-level comments)
                # Concept-level comments have both trophy_id=None AND checklist_id=None
                correct_count = Comment.objects.filter(
                    concept=concept,
                    trophy_id__isnull=True,
                    checklist_id__isnull=True,
                    is_deleted=False
                ).count()

                current_count = concept.comment_count or 0

                if current_count != correct_count:
                    if not dry_run:
                        concept.comment_count = correct_count
                        concept.save(update_fields=['comment_count'])

                    self.stdout.write(
                        f'  {concept.unified_title} (ID: {concept.id}): '
                        f'{current_count} -> {correct_count} '
                        f'{"(would update)" if dry_run else "(updated)"}'
                    )
                    updated_count += 1
                else:
                    unchanged_count += 1

            except Exception as e:
                error_msg = f'Error processing concept {concept.id}: {str(e)}'
                errors.append(error_msg)
                self.stdout.write(self.style.ERROR(f'  {error_msg}'))

        # Summary
        self.stdout.write('\n' + '=' * 60)
        self.stdout.write(self.style.SUCCESS(f'Processing complete!'))
        self.stdout.write(f'Total concepts processed: {total_concepts}')
        self.stdout.write(self.style.WARNING(f'Updated: {updated_count}'))
        self.stdout.write(f'Unchanged: {unchanged_count}')

        if errors:
            self.stdout.write(self.style.ERROR(f'Errors: {len(errors)}'))
            for error in errors:
                self.stdout.write(self.style.ERROR(f'  - {error}'))

        if dry_run and updated_count > 0:
            self.stdout.write(
                self.style.WARNING(
                    f'\nRun without --dry-run to apply {updated_count} changes'
                )
            )
