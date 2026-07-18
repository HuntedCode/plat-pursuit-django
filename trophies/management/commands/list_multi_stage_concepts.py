from django.core.management.base import BaseCommand
from django.db.models import Count

from trophies.models import Concept


class Command(BaseCommand):
    help = (
        "List Concepts that appear in multiple badge Stages -- candidates for the "
        "contract_satisfier_only flag (a multi-game trophy list, e.g. a remaster "
        "collection). REVIEW AID ONLY: appearing in several Stages does NOT by itself "
        "mean a concept spans multiple games (a single game legitimately lives in many "
        "badges/Stages), so this NEVER sets the flag -- it surfaces candidates for a "
        "human to judge. Flag the real multi-game lists by hand in the Concept admin."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--min-stages', type=int, default=2,
            help='Minimum number of Stages a concept must appear in (default 2).',
        )
        parser.add_argument(
            '--unflagged-only', action='store_true',
            help='Show only concepts not yet flagged contract_satisfier_only.',
        )

    def handle(self, *args, **options):
        min_stages = options['min_stages']
        unflagged_only = options['unflagged_only']

        qs = (
            Concept.objects
            .annotate(stage_count=Count('stages'))
            .filter(stage_count__gte=min_stages)
            .prefetch_related('stages')
            .order_by('-stage_count', 'unified_title')
        )
        if unflagged_only:
            qs = qs.filter(contract_satisfier_only=False)

        concepts = list(qs)
        if not concepts:
            self.stdout.write(self.style.WARNING(f"No concepts appear in {min_stages}+ stages."))
            return

        self.stdout.write(self.style.MIGRATE_HEADING(
            f"Concepts in {min_stages}+ Stages (candidates for contract_satisfier_only):\n"
        ))
        flagged_n = 0
        for c in concepts:
            flag = c.contract_satisfier_only
            flagged_n += int(flag)
            mark = '[x]' if flag else '[ ]'
            line = f"{mark} {c.unified_title}  ({c.concept_id})  -- {c.stage_count} stages"
            self.stdout.write(self.style.SUCCESS(line) if flag else line)
            for s in sorted(c.stages.all(), key=lambda st: (st.series_slug, st.stage_number)):
                title = f" / {s.title}" if s.title else ""
                self.stdout.write(f"      {s.series_slug} / stage {s.stage_number}{title}")

        total = len(concepts)
        self.stdout.write("")
        if unflagged_only:
            summary = f"{total} unflagged concept(s) in {min_stages}+ stages to review"
        else:
            summary = (f"{total} concept(s) in {min_stages}+ stages | "
                       f"{flagged_n} flagged | {total - flagged_n} to review")
        self.stdout.write(self.style.MIGRATE_HEADING(summary))
