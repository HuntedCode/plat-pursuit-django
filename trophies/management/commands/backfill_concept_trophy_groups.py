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
        parser.add_argument(
            '--audit-missing-trophies', action='store_true',
            help='Find games with TrophyGroup records but missing Trophy records (sync gaps). Outputs np_communication_id for re-sync.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        check_mismatches = options['check_mismatches']

        if options['audit_missing_trophies']:
            self._handle_audit_missing_trophies()
            return

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
        from django.db.models import Count, Q

        # Only check concepts with 2+ games (single-stack can't have mismatches).
        # Skip concepts where ALL games are shovelware.
        concepts = (
            Concept.objects
            .annotate(
                game_count=Count('games'),
                non_shovelware_count=Count(
                    'games',
                    filter=~Q(games__shovelware_status__in=['auto_flagged', 'manually_flagged']),
                ),
            )
            .filter(game_count__gte=2, non_shovelware_count__gt=0)
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

    def _handle_audit_missing_trophies(self):
        """Find games where TrophyGroup records exist but Trophy records are missing."""
        from itertools import groupby
        from django.db.models import Count
        from trophies.models import TrophyGroup, Trophy

        self.stdout.write("Auditing games for missing trophy records...\n")

        # Batch: actual Trophy counts per (game_id, trophy_group_id)
        actual_counts = {}
        for row in (
            Trophy.objects
            .values('game_id', 'trophy_group_id')
            .annotate(count=Count('id'))
        ):
            actual_counts[(row['game_id'], row['trophy_group_id'])] = row['count']

        # Fetch all TrophyGroups with their game info in one query
        all_trophy_groups = (
            TrophyGroup.objects
            .select_related('game')
            .order_by('game__title_name', 'trophy_group_id')
        )

        games_with_issues = 0
        total_missing_groups = 0

        for game, tg_iter in groupby(all_trophy_groups, key=lambda tg: tg.game):
            game_has_issue = False
            group_lines = []

            for tg in tg_iter:
                dt = tg.defined_trophies or {}
                expected = (
                    dt.get('bronze', 0) + dt.get('silver', 0)
                    + dt.get('gold', 0) + dt.get('platinum', 0)
                )
                actual = actual_counts.get((game.id, tg.trophy_group_id), 0)

                if expected > 0 and actual == 0:
                    game_has_issue = True
                    total_missing_groups += 1
                    group_lines.append(
                        f'    TrophyGroup "{tg.trophy_group_id}": '
                        f'expected {expected}, actual {actual}'
                    )
                elif expected > 0 and actual < expected:
                    game_has_issue = True
                    total_missing_groups += 1
                    group_lines.append(self.style.WARNING(
                        f'    TrophyGroup "{tg.trophy_group_id}": '
                        f'expected {expected}, actual {actual} (partial)'
                    ))

            if game_has_issue:
                games_with_issues += 1
                self.stdout.write(self.style.WARNING(
                    f"\n  {game.title_name} (np_communication_id={game.np_communication_id})"
                ))
                for line in group_lines:
                    self.stdout.write(line)

        self.stdout.write("")
        if games_with_issues == 0:
            self.stdout.write(self.style.SUCCESS(
                "All games with TrophyGroup records have matching Trophy records."
            ))
        else:
            self.stdout.write(self.style.WARNING(
                f"Found {total_missing_groups} trophy group(s) with missing records "
                f"across {games_with_issues} game(s)."
            ))
            self.stdout.write(
                "Use the np_communication_id values above to trigger re-syncs "
                "for these games."
            )
