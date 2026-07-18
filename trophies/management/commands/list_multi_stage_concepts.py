from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db.models import Count

from trophies.models import Concept, Stage


class Command(BaseCommand):
    help = (
        "List Concepts that appear in multiple badge Stages -- candidates for the "
        "contract_satisfier_only flag (a multi-game trophy list, e.g. a remaster "
        "collection). REVIEW AID ONLY: appearing in several Stages does NOT by itself "
        "mean a concept spans multiple games (a single game legitimately lives in many "
        "badges/Stages), so this NEVER sets the flag -- it surfaces candidates for a "
        "human to judge. Flag the real multi-game lists by hand in the Concept admin.\n\n"
        "Use --same-badge for the strongest signal: a concept in multiple stages of the "
        "SAME badge series (same series_slug) usually covers several games within one "
        "franchise -- exactly the multi-game-list case -- whereas a concept spread across "
        "DIFFERENT badges is just a game that earns several badges (noise)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--min-stages', type=int, default=2,
            help='Minimum number of Stages a concept must appear in (default 2).',
        )
        parser.add_argument(
            '--same-badge', action='store_true',
            help='Only list concepts appearing in --min-stages+ stages of the SAME badge '
                 'series (a much stronger multi-game-list signal than spanning different badges).',
        )
        parser.add_argument(
            '--unflagged-only', action='store_true',
            help='Show only concepts not yet flagged contract_satisfier_only.',
        )

    def handle(self, *args, **options):
        min_stages = options['min_stages']
        same_badge = options['same_badge']
        unflagged_only = options['unflagged_only']

        # repeats: concept_id -> [(series_slug, stage_count), ...] with count >= min_stages
        # (populated in same-badge mode so we can show WHICH badge repeats).
        repeats = None
        if same_badge:
            rows = (
                Stage.concepts.through.objects
                .values('concept_id', 'stage__series_slug')
                .annotate(n=Count('stage_id'))
                .filter(n__gte=min_stages)
            )
            repeats = defaultdict(list)
            for r in rows:
                repeats[r['concept_id']].append((r['stage__series_slug'], r['n']))
            qs = Concept.objects.filter(id__in=list(repeats)).prefetch_related('stages').order_by('unified_title')
        else:
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
        scope = f"{min_stages}+ stages of the same badge" if same_badge else f"{min_stages}+ stages"
        if not concepts:
            self.stdout.write(self.style.WARNING(f"No concepts appear in {scope}."))
            return

        heading = "SAME badge" if same_badge else "Stages"
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"Concepts in {min_stages}+ {heading} (candidates for contract_satisfier_only):\n"
        ))
        flagged_n = 0
        for c in concepts:
            flag = c.contract_satisfier_only
            flagged_n += int(flag)
            mark = '[x]' if flag else '[ ]'
            if same_badge:
                badges = ', '.join(f"{slug} (x{n})" for slug, n in sorted(repeats[c.id]))
                line = f"{mark} {c.unified_title}  ({c.concept_id})  -- repeats within: {badges}"
            else:
                line = f"{mark} {c.unified_title}  ({c.concept_id})  -- {c.stage_count} stages"
            self.stdout.write(self.style.SUCCESS(line) if flag else line)
            for s in sorted(c.stages.all(), key=lambda st: (st.series_slug, st.stage_number)):
                title = f" / {s.title}" if s.title else ""
                self.stdout.write(f"      {s.series_slug} / stage {s.stage_number}{title}")

        total = len(concepts)
        self.stdout.write("")
        scope_label = "same-badge " if same_badge else ""
        if unflagged_only:
            summary = f"{total} unflagged {scope_label}concept(s) in {min_stages}+ stages to review"
        else:
            summary = (f"{total} {scope_label}concept(s) in {min_stages}+ stages | "
                       f"{flagged_n} flagged | {total - flagged_n} to review")
        self.stdout.write(self.style.MIGRATE_HEADING(summary))
