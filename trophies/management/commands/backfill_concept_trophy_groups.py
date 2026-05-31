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
        parser.add_argument(
            '--audit-missing-groups', action='store_true',
            help='Find games with defined_trophies data but no TrophyGroup records (sync_trophy_groups failed). Outputs np_communication_id for re-sync.',
        )
        parser.add_argument(
            '--audit-orphaned-groups', action='store_true',
            help='Find games whose Trophy rows reference a trophy_group_id with no matching TrophyGroup record (corrupted/missing DLC groups while trophies are intact). Reports the missing group ids and whether the concept is anchored.',
        )
        parser.add_argument(
            '--fix', action='store_true',
            help='With --audit-orphaned-groups: re-queue a sync_trophy_groups job for each affected game to rebuild the missing groups. Requires the TokenKeeper worker to be running to drain the queue.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        check_mismatches = options['check_mismatches']

        if options['audit_missing_trophies']:
            self._handle_audit_missing_trophies()
            return

        if options['audit_missing_groups']:
            self._handle_audit_missing_groups()
            return

        if options['audit_orphaned_groups']:
            self._handle_audit_orphaned_groups(fix=options['fix'])
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

        # Fetch all TrophyGroups with their game info in one query.
        # order_by game_id aligns with the groupby key for stable grouping.
        all_trophy_groups = (
            TrophyGroup.objects
            .select_related('game')
            .order_by('game_id', 'trophy_group_id')
        )

        games_with_issues = 0
        total_missing_groups = 0

        for _, tg_iter in groupby(all_trophy_groups, key=lambda tg: tg.game_id):
            tg_list = list(tg_iter)
            game = tg_list[0].game
            game_has_issue = False
            group_lines = []

            for tg in tg_list:
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

    def _handle_audit_orphaned_groups(self, fix=False):
        """Find games whose Trophy rows reference a trophy_group_id that has no
        matching TrophyGroup record.

        This catches the corruption where a game keeps all its Trophy rows but
        loses one or more TrophyGroup rows (typically DLC groups). The game-level
        defined_trophies total still matches PSN and at least the base group
        survives, so neither the slow-path drift check nor the zero-group
        completeness backstop in sync ever flags it.

        With fix=True, re-queues a sync_trophy_groups job per affected game to
        rebuild the missing groups (idempotent get_or_create; never touches
        Trophy/EarnedTrophy rows).
        """
        from collections import defaultdict
        from django.db.models import OuterRef, Exists
        from trophies.models import Game, Trophy, TrophyGroup
        from trophies.psn_manager import PSNManager

        self.stdout.write("Auditing games for orphaned trophy groups...\n")

        # Distinct (game_id, trophy_group_id) pairs on Trophy rows that have no
        # matching TrophyGroup row. Catalog-level (not per-user), so the distinct
        # set is bounded by the number of trophy groups across the whole catalog.
        group_exists = TrophyGroup.objects.filter(
            game_id=OuterRef('game_id'),
            trophy_group_id=OuterRef('trophy_group_id'),
        )
        orphan_rows = (
            Trophy.objects
            .annotate(_has_group=Exists(group_exists))
            .filter(_has_group=False)
            .values_list('game_id', 'trophy_group_id')
            .distinct()
        )

        missing_by_game = defaultdict(list)
        for game_id, group_id in orphan_rows:
            missing_by_game[game_id].append(group_id)

        if not missing_by_game:
            self.stdout.write(self.style.SUCCESS(
                "No orphaned trophy groups found. Every trophy_group_id on a "
                "Trophy row has a matching TrophyGroup record."
            ))
            return

        # Fetch games (with concept for title + anchored flag) in one query.
        games = (
            Game.objects
            .filter(id__in=missing_by_game.keys())
            .select_related('concept')
            .order_by('title_name')
        )

        total_groups = 0
        queued = 0
        skipped = 0
        for game in games:
            group_ids = sorted(missing_by_game[game.id])
            total_groups += len(group_ids)

            anchored = bool(
                game.concept and game.concept.anchor_migration_completed_at is not None
            )
            anchor_tag = self.style.HTTP_INFO("  [ANCHORED]") if anchored else ""

            self.stdout.write(self.style.WARNING(
                f"\n  {game.title_name} (np_communication_id={game.np_communication_id}){anchor_tag}"
            ))
            self.stdout.write(
                f"    missing TrophyGroup rows: {', '.join(group_ids)}"
            )

            if fix:
                driver_profile_id = (
                    game.played_by.values_list('profile_id', flat=True).first()
                )
                if driver_profile_id is None:
                    skipped += 1
                    self.stdout.write(self.style.ERROR(
                        "    SKIP: no profile has played this game, cannot drive a PSN re-sync"
                    ))
                    continue

                # Resolve platform exactly as the in-sync re-queue loops do:
                # PSPC titles report platform[0]='PSPC', so fall back to [1].
                platform = (
                    game.title_platform[0]
                    if game.title_platform[0] != 'PSPC'
                    else game.title_platform[1]
                )
                # high_priority is intentionally NOT a counted queue, so this
                # repair never touches the driver profile's sync job counter or
                # trips a spurious sync_complete (medium_priority would).
                PSNManager.assign_job(
                    'sync_trophy_groups',
                    [game.np_communication_id, platform],
                    driver_profile_id,
                    priority_override='high_priority',
                )
                queued += 1
                self.stdout.write(self.style.SUCCESS(
                    f"    queued sync_trophy_groups (driver profile {driver_profile_id})"
                ))

        self.stdout.write("")
        self.stdout.write(self.style.WARNING(
            f"Found {total_groups} orphaned trophy group(s) across {len(missing_by_game)} game(s)."
        ))
        if fix:
            self.stdout.write(self.style.SUCCESS(
                f"Queued {queued} re-sync job(s)" + (f", skipped {skipped}" if skipped else "")
                + ". Ensure the TokenKeeper worker is running to drain the queue."
            ))
        else:
            self.stdout.write(
                "Re-run with --fix to re-queue sync_trophy_groups for these games, "
                "or use the np_communication_id values above to trigger re-syncs manually."
            )

    def _handle_audit_missing_groups(self):
        """Find games with defined_trophies data but no TrophyGroup records."""
        from trophies.models import Game

        self.stdout.write("Auditing games for missing TrophyGroup records...\n")

        games_missing_groups = (
            Game.objects.filter(
                defined_trophies__has_key='bronze',
            ).exclude(
                trophy_groups__isnull=False,
            ).order_by('title_name')
        )

        count = 0
        for game in games_missing_groups.iterator():
            count += 1
            dt = game.defined_trophies or {}
            bronze = dt.get('bronze', 0)
            silver = dt.get('silver', 0)
            gold = dt.get('gold', 0)
            platinum = dt.get('platinum', 0)

            self.stdout.write(self.style.WARNING(
                f"\n  {game.title_name} (np_communication_id={game.np_communication_id})"
            ))
            self.stdout.write(
                f"    defined_trophies: {bronze}B {silver}S {gold}G {platinum}P "
                f"(0 TrophyGroup records)"
            )

        self.stdout.write("")
        if count == 0:
            self.stdout.write(self.style.SUCCESS(
                "All games with defined_trophies have TrophyGroup records."
            ))
        else:
            self.stdout.write(self.style.WARNING(
                f"Found {count} game(s) with missing TrophyGroup records."
            ))
            self.stdout.write(
                "Use the np_communication_id values above to trigger re-syncs "
                "for these games."
            )
