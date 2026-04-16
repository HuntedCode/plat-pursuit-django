"""
Print stats about the Franchise / Collection taxonomy in the database.

Useful for:
  - Sanity-checking enrichment coverage before/after a backfill
  - Deciding whether the "show collections with orphan games" rule on the
    browse page is doing what we expect
  - Identifying which specific collections will newly appear on browse

No writes. Read-only inspection.

Usage:
    python manage.py franchise_stats
    python manage.py franchise_stats --samples 20    # show more example names
    python manage.py franchise_stats --samples 0     # numbers only, no names
"""
from django.core.management.base import BaseCommand
from django.db.models import Count, Exists, OuterRef, Q

from trophies.models import (
    Concept, ConceptFranchise, Franchise, Game,
)


class Command(BaseCommand):
    help = "Report franchise / collection taxonomy coverage and browse-page surfacing."

    def add_arguments(self, parser):
        parser.add_argument(
            '--samples', type=int, default=10,
            help='How many example names to print per category (default 10, 0 to skip).',
        )

    def handle(self, *args, **options):
        sample_n = options['samples']

        H = self.style.MIGRATE_HEADING
        OK = self.style.SUCCESS
        WARN = self.style.WARNING

        # =========================================================
        # 1. Raw Franchise table counts
        # =========================================================
        self.stdout.write(H("\n=== Franchise table totals ==="))
        franchise_total = Franchise.objects.filter(source_type='franchise').count()
        collection_total = Franchise.objects.filter(source_type='collection').count()
        self.stdout.write(f"  source_type='franchise':   {franchise_total:>6}")
        self.stdout.write(f"  source_type='collection':  {collection_total:>6}")
        self.stdout.write(f"  Total Franchise rows:      {franchise_total + collection_total:>6}")

        # =========================================================
        # 2. ConceptFranchise link counts
        # =========================================================
        self.stdout.write(H("\n=== ConceptFranchise links ==="))
        cf_total = ConceptFranchise.objects.count()
        cf_main = ConceptFranchise.objects.filter(is_main=True).count()
        cf_franchise_links = ConceptFranchise.objects.filter(
            franchise__source_type='franchise',
        ).count()
        cf_collection_links = ConceptFranchise.objects.filter(
            franchise__source_type='collection',
        ).count()
        self.stdout.write(f"  Total links:                  {cf_total:>6}")
        self.stdout.write(f"  is_main=True:                 {cf_main:>6}")
        self.stdout.write(f"  Links to franchise-type rows: {cf_franchise_links:>6}")
        self.stdout.write(f"  Links to collection-type rows:{cf_collection_links:>6}")

        # =========================================================
        # 3. Per-concept coverage (the key question for browse logic)
        # =========================================================
        self.stdout.write(H("\n=== Concept franchise coverage ==="))
        concepts_total = Concept.objects.count()
        concepts_with_any_link = Concept.objects.filter(
            concept_franchises__isnull=False,
        ).distinct().count()
        concepts_with_franchise_link = Concept.objects.filter(
            concept_franchises__franchise__source_type='franchise',
        ).distinct().count()
        # Concepts with at least one collection link AND no franchise link.
        # These are the "orphan concepts" — their only taxonomy is via collections.
        concepts_collection_only = Concept.objects.filter(
            concept_franchises__franchise__source_type='collection',
        ).exclude(
            concept_franchises__franchise__source_type='franchise',
        ).distinct()
        concepts_collection_only_count = concepts_collection_only.count()
        concepts_no_taxonomy = (
            concepts_total - concepts_with_any_link
        )
        self.stdout.write(f"  Total concepts:                       {concepts_total:>6}")
        self.stdout.write(f"  ...with any franchise/collection link:{concepts_with_any_link:>6}")
        self.stdout.write(f"  ...with at least one franchise link:  {concepts_with_franchise_link:>6}")
        self.stdout.write(
            f"  ...with collection link(s) only       {concepts_collection_only_count:>6}  "
            + WARN("← these only surface via collections")
        )
        self.stdout.write(f"  ...with no taxonomy at all:           {concepts_no_taxonomy:>6}")

        # =========================================================
        # 4. Browse-page surfacing — what the new rules actually show
        # =========================================================
        self.stdout.write(H("\n=== Browse page surfacing (what users will see) ==="))

        # Franchise-type rows that are at least one game's main.
        browse_franchises = Franchise.objects.filter(
            source_type='franchise',
            franchise_concepts__is_main=True,
        ).distinct()
        browse_franchise_count = browse_franchises.count()

        # Collection-type rows that contain at least one orphan concept
        # (matches the new browse query in FranchiseListView.get_queryset).
        orphan_concept_exists = Exists(
            ConceptFranchise.objects.filter(
                franchise=OuterRef('pk'),
            ).exclude(
                concept__concept_franchises__franchise__source_type='franchise',
            )
        )
        browse_collections = Franchise.objects.filter(
            source_type='collection',
        ).annotate(
            has_orphan_concept=orphan_concept_exists,
        ).filter(has_orphan_concept=True)
        browse_collection_count = browse_collections.count()

        # Hidden collections (have at least one game but no orphans).
        hidden_collections = Franchise.objects.filter(
            source_type='collection',
            franchise_concepts__isnull=False,
        ).annotate(
            has_orphan_concept=orphan_concept_exists,
        ).filter(has_orphan_concept=False).distinct()
        hidden_collection_count = hidden_collections.count()

        self.stdout.write(
            f"  Franchises shown on browse:               {browse_franchise_count:>6}"
        )
        self.stdout.write(
            OK(f"  Collections shown on browse (NEW):        {browse_collection_count:>6}")
        )
        self.stdout.write(
            f"  Collections hidden (covered by franchise):{hidden_collection_count:>6}"
        )
        self.stdout.write(
            f"  Total browse rows:                        {browse_franchise_count + browse_collection_count:>6}"
        )

        # =========================================================
        # 5. Samples (if requested)
        # =========================================================
        if sample_n > 0:
            self.stdout.write(H("\n=== Samples ==="))

            self.stdout.write(self.style.MIGRATE_LABEL(
                f"\nCollections that WILL surface on browse (showing up to {sample_n}):"
            ))
            for c in browse_collections.annotate(
                game_count=Count('franchise_concepts__concept__games', distinct=True),
            ).order_by('-game_count', 'name')[:sample_n]:
                self.stdout.write(f"  - {c.name}  ({c.game_count} games)")

            self.stdout.write(self.style.MIGRATE_LABEL(
                f"\nCollections that stay HIDDEN (showing up to {sample_n}):"
            ))
            for c in hidden_collections.annotate(
                game_count=Count('franchise_concepts__concept__games', distinct=True),
            ).order_by('-game_count', 'name')[:sample_n]:
                self.stdout.write(f"  - {c.name}  ({c.game_count} games)")

            # Spot-check the orphan concepts themselves
            self.stdout.write(self.style.MIGRATE_LABEL(
                f"\nConcepts only reachable via collections (showing up to {sample_n}):"
            ))
            for c in concepts_collection_only.order_by('unified_title')[:sample_n]:
                # Surface which collection(s) cover them
                collection_names = list(
                    c.concept_franchises.filter(
                        franchise__source_type='collection',
                    ).values_list('franchise__name', flat=True)
                )
                self.stdout.write(
                    f"  - {c.unified_title}  →  {', '.join(collection_names)}"
                )

        self.stdout.write("")  # trailing newline
