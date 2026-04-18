import logging

from django.core.management.base import BaseCommand

from trophies.models import Concept, DeveloperBlacklist, Game
from trophies.services.shovelware_detection_service import ShovelwareDetectionService

logger = logging.getLogger("psn_api")


class Command(BaseCommand):
    help = (
        "Surgical shovelware reconciliation. Re-evaluates a targeted set of "
        "concepts (currently flagged, qualifying for rule 1, or tied to a "
        "blacklisted developer) and updates state only where it drifts from "
        "what the detection algorithm would compute. Idempotent: preserves "
        "shovelware_updated_at on unchanged games. For full wipe+rebuild use "
        "'backfill_shovelware' instead."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--verbose', action='store_true',
            help='Print per-concept decisions.',
        )

    def handle(self, *args, **options):
        verbose = options['verbose']
        threshold = ShovelwareDetectionService.FLAG_THRESHOLD

        # Build a deduplicated candidate set covering every concept where
        # state could plausibly drift. Three contributing categories:
        #   1. Concepts with any currently auto-flagged game.
        #      -> Catches concepts that should now be shielded / unflagged.
        #   2. Concepts with a platinum at >= FLAG_THRESHOLD.
        #      -> Catches concepts that should be rule-1 flagged but aren't.
        #   3. Concepts whose any developer is on an active blacklist entry.
        #      -> Catches missed rule-2 cascades and shielded transitions.
        # The candidate set is a superset of what actually needs updates;
        # evaluate_concept is idempotent, so over-inclusion costs queries
        # but never corrupts state.
        flagged_ids = set(
            Concept.objects
            .filter(games__shovelware_status='auto_flagged')
            .values_list('id', flat=True)
            .distinct()
        )
        rule1_ids = set(
            Concept.objects
            .filter(
                games__trophies__trophy_type='platinum',
                games__trophies__trophy_earn_rate__gte=threshold,
            )
            .values_list('id', flat=True)
            .distinct()
        )
        blacklisted_company_ids = list(
            DeveloperBlacklist.objects
            .filter(is_blacklisted=True)
            .values_list('company_id', flat=True)
        )
        if blacklisted_company_ids:
            rule2_ids = set(
                Concept.objects
                .filter(
                    concept_companies__company_id__in=blacklisted_company_ids,
                    concept_companies__is_developer=True,
                )
                .values_list('id', flat=True)
                .distinct()
            )
        else:
            rule2_ids = set()

        candidate_ids = flagged_ids | rule1_ids | rule2_ids

        self.stdout.write(
            f"Reconciliation candidate set: {len(candidate_ids)} concept(s) "
            f"({len(flagged_ids)} currently flagged, {len(rule1_ids)} rule-1 "
            f"qualifying, {len(rule2_ids)} tied to blacklisted developers)."
        )

        # Snapshot status counts before for a delta summary.
        before_counts = self._status_counts()

        evaluated = 0
        for concept in Concept.objects.filter(id__in=list(candidate_ids)).iterator():
            ShovelwareDetectionService.evaluate_concept(concept)
            evaluated += 1
            if verbose:
                self.stdout.write(f"  [EVAL] {concept.concept_id} ({concept.unified_title})")

        after_counts = self._status_counts()
        blacklisted_devs = DeveloperBlacklist.objects.filter(is_blacklisted=True).count()

        delta_flagged = after_counts['flagged'] - before_counts['flagged']
        delta_clean = after_counts['clean'] - before_counts['clean']

        self.stdout.write(self.style.SUCCESS(
            f"\nReconciliation complete!"
            f"\n  Concepts evaluated: {evaluated}"
            f"\n  Flagged games: {after_counts['flagged']} ({delta_flagged:+d})"
            f"\n  Clean games: {after_counts['clean']} ({delta_clean:+d})"
            f"\n  Locked games: {after_counts['locked']}"
            f"\n  Blacklisted developers: {blacklisted_devs}"
        ))

    @staticmethod
    def _status_counts():
        return {
            'flagged': Game.objects.filter(
                shovelware_status__in=['auto_flagged', 'manually_flagged'],
            ).count(),
            'clean': Game.objects.filter(shovelware_status='clean').count(),
            'locked': Game.objects.filter(shovelware_lock=True).count(),
        }
