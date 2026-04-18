import logging

from django.core.management.base import BaseCommand
from django.utils import timezone

from trophies.models import Concept, DeveloperBlacklist, Game
from trophies.services.shovelware_detection_service import ShovelwareDetectionService

logger = logging.getLogger("psn_api")


class Command(BaseCommand):
    help = (
        "One-shot backfill: wipe all auto-flagged shovelware state and the "
        "DeveloperBlacklist, then rebuild from scratch. Use after schema "
        "migrations or major data corrections. For routine drift correction "
        "use 'update_shovelware' instead."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Report what would change without writing to the database.',
        )
        parser.add_argument(
            '--verbose', action='store_true',
            help='Print per-concept decisions.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        verbose = options['verbose']

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN: no changes will be saved.\n"))

        # Step 1: Reset auto-flagged games (respect shovelware_lock + manual statuses).
        self.stdout.write("Step 1: Resetting auto-flagged games to clean...")
        reset_qs = Game.objects.filter(
            shovelware_status='auto_flagged',
            shovelware_lock=False,
        )
        reset_count = reset_qs.count()
        if not dry_run:
            reset_qs.update(shovelware_status='clean', shovelware_updated_at=None)
        self.stdout.write(f"  {reset_count} game(s) reset to clean.")

        # Step 2: Wipe DeveloperBlacklist so it rebuilds from current evidence.
        self.stdout.write("\nStep 2: Wiping DeveloperBlacklist...")
        dev_count = DeveloperBlacklist.objects.count()
        if not dry_run:
            DeveloperBlacklist.objects.all().delete()
        self.stdout.write(f"  {dev_count} developer blacklist entr(ies) cleared.")

        if dry_run:
            self.stdout.write(self.style.WARNING(
                "\nDRY RUN: skipping re-evaluation pass. Run without --dry-run for the full rebuild."
            ))
            return

        now = timezone.now()

        # Step 3 (pass 1): Rule-1 sweep. Evaluate every concept that contains an
        # 80%+ platinum. This flags the concept AND seeds DeveloperBlacklist
        # entries for primary developers (with the in-built cascade, which is
        # harmless at this stage since every other concept will be visited).
        threshold = ShovelwareDetectionService.FLAG_THRESHOLD
        self.stdout.write(
            f"\nStep 3: Flagging concepts with any game at >= {threshold:.1f}% plat rate..."
        )
        rule1_concept_ids = list(
            Concept.objects.filter(
                games__trophies__trophy_type='platinum',
                games__trophies__trophy_earn_rate__gte=threshold,
            ).values_list('id', flat=True).distinct()
        )
        rule1_qs = Concept.objects.filter(id__in=rule1_concept_ids).only(
            'id', 'concept_id', 'unified_title',
        )
        for concept in rule1_qs.iterator(chunk_size=500):
            ShovelwareDetectionService.evaluate_concept(concept)
            if verbose:
                self.stdout.write(f"  [RULE-1] {concept.concept_id} ({concept.unified_title})")
        self.stdout.write(f"  {len(rule1_concept_ids)} concept(s) flagged via rule 1.")

        # Step 4 (pass 2): Rule-2 sweep. For every concept whose primary
        # developer is now blacklisted but wasn't visited above, evaluate it
        # so the developer-blacklist rule (and shield) applies.
        self.stdout.write("\nStep 4: Applying developer-blacklist rule to remaining concepts...")
        blacklisted_company_ids = list(
            DeveloperBlacklist.objects.filter(is_blacklisted=True).values_list('company_id', flat=True)
        )
        if not blacklisted_company_ids:
            self.stdout.write("  No active developer blacklist entries; nothing to do.")
        else:
            rule1_seen = set(rule1_concept_ids)
            candidate_ids = set(
                Concept.objects.filter(
                    concept_companies__company_id__in=blacklisted_company_ids,
                    concept_companies__is_developer=True,
                ).values_list('id', flat=True)
            ) - rule1_seen
            rule2_qs = Concept.objects.filter(id__in=list(candidate_ids)).only(
                'id', 'concept_id', 'unified_title',
            )
            for concept in rule2_qs.iterator(chunk_size=500):
                ShovelwareDetectionService.evaluate_concept(concept)
                if verbose:
                    self.stdout.write(f"  [RULE-2] {concept.concept_id} ({concept.unified_title})")
            self.stdout.write(f"  {len(candidate_ids)} concept(s) re-evaluated via rule 2.")

        # Summary
        total_flagged = Game.objects.filter(
            shovelware_status__in=['auto_flagged', 'manually_flagged'],
        ).count()
        total_clean = Game.objects.filter(shovelware_status='clean').count()
        total_locked = Game.objects.filter(shovelware_lock=True).count()
        total_blacklisted = DeveloperBlacklist.objects.filter(is_blacklisted=True).count()

        self.stdout.write(self.style.SUCCESS(
            f"\nRebuild complete!"
            f"\n  Flagged games: {total_flagged}"
            f"\n  Clean games: {total_clean}"
            f"\n  Locked games: {total_locked}"
            f"\n  Blacklisted developers: {total_blacklisted}"
        ))
