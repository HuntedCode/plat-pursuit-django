from django.core.management.base import BaseCommand
from trophies.models import Concept
from trophies.services.concept_trophy_group_service import ConceptTrophyGroupService


class Command(BaseCommand):
    help = "Create ConceptTrophyGroup records for all Concepts from their game-level TrophyGroups"

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Preview how many concepts would be synced without making changes',
        )
        parser.add_argument(
            '--check-mismatches', action='store_true',
            help='Scan for trophy group mismatches across game stacks (name differences, trophy count differences, missing groups)',
        )
        parser.add_argument(
            '--collections-only', action='store_true',
            help='With --check-mismatches: only show concepts where the base game (default) group has a trophy count mismatch, indicating bundled collections that need concept splitting',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        check_mismatches = options['check_mismatches']

        if check_mismatches:
            self._handle_mismatch_check(
                collections_only=options['collections_only'],
            )
            return

        total = Concept.objects.count()

        if total == 0:
            self.stdout.write("No concepts found. Nothing to do.")
            return

        if dry_run:
            self.stdout.write(f"[DRY RUN] Would sync trophy groups for {total} concept(s). No changes made.")
            return

        synced = 0
        errors = 0

        self.stdout.write(f"Syncing trophy groups for {total} concepts...")

        for concept in Concept.objects.all().iterator():
            try:
                ConceptTrophyGroupService.sync_for_concept(concept)
                synced += 1
                if synced % 50 == 0 or synced == total:
                    self.stdout.write(f"  [{synced}/{total}] synced")
            except Exception as e:
                errors += 1
                self.stdout.write(self.style.ERROR(
                    f"  Error syncing concept {concept.id} ({concept.concept_id}): {e}"
                ))

        summary = f"Sync complete. {synced} concept(s) synced"
        if errors:
            summary += f", {errors} error(s)"
        self.stdout.write(self.style.SUCCESS(summary))

    def _handle_mismatch_check(self, collections_only=False):
        """Scan all multi-stack concepts for trophy group mismatches."""
        from django.db.models import Count

        # Only check concepts with 2+ games (single-stack can't have mismatches)
        concepts = (
            Concept.objects
            .annotate(game_count=Count('games'))
            .filter(game_count__gte=2)
            .order_by('unified_title')
        )
        total = concepts.count()

        if collections_only:
            self.stdout.write(f"Checking {total} multi-stack concepts for bundled collections...\n")
        else:
            self.stdout.write(f"Checking {total} multi-stack concepts for trophy group mismatches...\n")

        concepts_with_issues = 0
        total_mismatches = 0

        for concept in concepts.iterator():
            mismatches = ConceptTrophyGroupService.detect_mismatches(concept)
            if not mismatches:
                continue

            if collections_only:
                # Only keep concepts where the default group has a count mismatch
                has_default_count_issue = any(
                    m['type'] == 'trophy_count_mismatch' and m['group_id'] == 'default'
                    for m in mismatches
                )
                if not has_default_count_issue:
                    continue
                # Show only the default group count mismatch + game titles
                mismatches = [
                    m for m in mismatches
                    if m['group_id'] == 'default'
                ]

            concepts_with_issues += 1
            total_mismatches += len(mismatches)

            self.stdout.write(self.style.WARNING(
                f"\n  {concept.unified_title} (concept_id={concept.concept_id})"
            ))

            for m in mismatches:
                icon = {
                    'trophy_count_mismatch': 'COUNT',
                    'missing_group': 'MISSING',
                    'structure_mismatch': 'STRUCT',
                }.get(m['type'], m['type'])
                self.stdout.write(f"    [{icon}] {m['detail']}")

        self.stdout.write("")
        if concepts_with_issues == 0:
            if collections_only:
                self.stdout.write(self.style.SUCCESS(
                    f"No bundled collections found among {total} multi-stack concepts."
                ))
            else:
                self.stdout.write(self.style.SUCCESS(
                    f"All {total} multi-stack concepts have consistent trophy groups."
                ))
        else:
            self.stdout.write(self.style.WARNING(
                f"Found {total_mismatches} mismatch(es) across {concepts_with_issues} concept(s)."
            ))
            if collections_only:
                self.stdout.write(
                    "These concepts likely contain bundled collections (multiple different "
                    "games under one concept). Consider splitting into separate concepts."
                )
            else:
                self.stdout.write(
                    "Review these concepts. Mismatches may indicate games that should "
                    "not share a concept, or DLC packs that differ between platforms."
                )
