"""
Print stats about the Franchise / Series taxonomy in the database.

User-facing vocabulary note: source_type='collection' rows are labeled
"Series" everywhere a user sees them (matches the badge system's
series_slug terminology). The DB column and IGDB API field keep
'collection' so this command uses both terms — "series" in UI-aligned
summaries, "collection" when discussing raw table state.

Useful for:
  - Sanity-checking enrichment coverage before/after a backfill
  - Counting franchise vs. series rows that will surface on browse
  - Finding cached IGDB data that didn't land as ConceptFranchise links
    (slug collisions, cross-namespace clashes, etc.)

No writes. Read-only inspection.

Usage:
    python manage.py franchise_stats
    python manage.py franchise_stats --samples 20    # show more example names
    python manage.py franchise_stats --samples 0     # numbers only, no names
"""
from django.core.management.base import BaseCommand
from django.db.models import Q

from trophies.models import (
    Concept, ConceptFranchise, Franchise, IGDBMatch,
)


class Command(BaseCommand):
    help = "Report franchise / series taxonomy coverage and browse-page surfacing."

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
        series_total = Franchise.objects.filter(source_type='collection').count()
        self.stdout.write(f"  source_type='franchise' (Franchises):  {franchise_total:>6}")
        self.stdout.write(f"  source_type='collection' (Series):     {series_total:>6}")
        self.stdout.write(f"  Total Franchise rows:                  {franchise_total + series_total:>6}")

        # =========================================================
        # 2. ConceptFranchise link counts
        # =========================================================
        self.stdout.write(H("\n=== ConceptFranchise links ==="))
        cf_total = ConceptFranchise.objects.count()
        cf_excluded = ConceptFranchise.objects.filter(is_excluded=True).count()
        cf_spinoff = ConceptFranchise.objects.filter(is_spinoff=True).count()
        cf_franchise_links = ConceptFranchise.objects.filter(
            franchise__source_type='franchise',
        ).count()
        cf_series_links = ConceptFranchise.objects.filter(
            franchise__source_type='collection',
        ).count()
        self.stdout.write(f"  Total links:                          {cf_total:>6}")
        self.stdout.write(f"  is_excluded=True (admin curated):     {cf_excluded:>6}")
        self.stdout.write(f"  is_spinoff=True (series only):        {cf_spinoff:>6}")
        self.stdout.write(f"  Links to franchise-type rows:         {cf_franchise_links:>6}")
        self.stdout.write(f"  Links to series-type rows:            {cf_series_links:>6}")

        # =========================================================
        # 3. Per-concept coverage
        # =========================================================
        self.stdout.write(H("\n=== Concept franchise / series coverage ==="))
        concepts_total = Concept.objects.count()
        concepts_with_any_link = Concept.objects.filter(
            concept_franchises__isnull=False,
        ).distinct().count()
        concepts_with_franchise_link = Concept.objects.filter(
            concept_franchises__franchise__source_type='franchise',
        ).distinct().count()
        concepts_with_series_link = Concept.objects.filter(
            concept_franchises__franchise__source_type='collection',
        ).distinct().count()
        concepts_no_taxonomy = (
            concepts_total - concepts_with_any_link
        )
        self.stdout.write(f"  Total concepts:                       {concepts_total:>6}")
        self.stdout.write(f"  ...with any franchise / series link:  {concepts_with_any_link:>6}")
        self.stdout.write(f"  ...with at least one franchise link:  {concepts_with_franchise_link:>6}")
        self.stdout.write(f"  ...with at least one series link:     {concepts_with_series_link:>6}")
        self.stdout.write(f"  ...with no taxonomy at all:           {concepts_no_taxonomy:>6}")

        # =========================================================
        # 4. Browse-page surfacing — the orphan-concept rule is gone:
        # every franchise / series with at least one non-excluded link
        # and version_count > 0 surfaces. We don't compute version_count
        # here because the surfacing rule mirrors the view: a franchise
        # row with at least one non-excluded link + at least one Game
        # appears. For diagnostic purposes the "has any non-excluded
        # link" count is a tight upper bound.
        # =========================================================
        self.stdout.write(H("\n=== Browse-page surfacing (upper bound) ==="))

        browse_franchises = Franchise.objects.filter(
            source_type='franchise',
            franchise_concepts__is_excluded=False,
        ).distinct()
        browse_franchise_count = browse_franchises.count()

        # Series row surfacing rule is the same as franchise rows since
        # the orphan-concept gate was removed: any series with at least
        # one non-spinoff, non-excluded link surfaces.
        browse_series = Franchise.objects.filter(
            source_type='collection',
            franchise_concepts__is_excluded=False,
            franchise_concepts__is_spinoff=False,
        ).distinct()
        browse_series_count = browse_series.count()

        self.stdout.write(
            f"  Franchises eligible for browse:        {browse_franchise_count:>6}"
        )
        self.stdout.write(
            OK(f"  Series eligible for browse:            {browse_series_count:>6}")
        )
        self.stdout.write(
            f"  Total eligible browse rows:            {browse_franchise_count + browse_series_count:>6}"
        )
        self.stdout.write(
            "  (Final view also applies version_count > 0 and the optional\n"
            "   show_solo filter; the counts above are an upper bound.)"
        )

        # =========================================================
        # 5. Samples (if requested)
        # =========================================================
        if sample_n > 0:
            self.stdout.write(H("\n=== Samples ==="))

            self.stdout.write(self.style.MIGRATE_LABEL(
                f"\nFranchises eligible for browse (showing up to {sample_n}):"
            ))
            for f in browse_franchises.order_by('name')[:sample_n]:
                self.stdout.write(f"  - {f.name}")

            self.stdout.write(self.style.MIGRATE_LABEL(
                f"\nSeries eligible for browse (showing up to {sample_n}):"
            ))
            for s in browse_series.order_by('name')[:sample_n]:
                self.stdout.write(f"  - {s.name}")

        # =========================================================
        # 6. Smoking-gun audit: concepts whose IGDB cache lists franchises
        #    or collections, but who have ZERO ConceptFranchise rows. These
        #    are the cases where the writer silently failed (slug collision,
        #    cross-namespace clash, race condition, etc.) and the user sees
        #    "this game's franchise is just missing."
        # =========================================================
        self.stdout.write(H("\n=== Audit: cached franchise data with no DB link ==="))
        # Postgres can ask "is this JSON array non-empty" via __len__gt=0 path;
        # iterate to be portable / explicit.
        candidates = (
            IGDBMatch.objects
            .exclude(raw_response={})
            .exclude(raw_response__isnull=True)
            .filter(
                Q(raw_response__franchises__isnull=False)
                | Q(raw_response__franchise__isnull=False)
                | Q(raw_response__collections__isnull=False),
            )
            .select_related('concept')
            .only('concept_id', 'concept__concept_id', 'concept__unified_title',
                  'concept__franchises_locked', 'raw_response')
        )
        missing = []
        for match in candidates.iterator(chunk_size=200):
            raw = match.raw_response or {}
            plural = raw.get('franchises') or []
            singular = raw.get('franchise') or {}
            collections_raw = raw.get('collections') or []
            cached_link_count = (
                len([f for f in plural if f.get('id')])
                + (1 if singular.get('id') else 0)
                + len([c for c in collections_raw if c.get('id')])
            )
            if cached_link_count == 0:
                continue
            actual_links = ConceptFranchise.objects.filter(
                concept_id=match.concept_id,
            ).count()
            if actual_links == 0:
                missing.append((match.concept, cached_link_count))

        if not missing:
            self.stdout.write(OK("  None — every cached franchise/collection has a DB link."))
        else:
            self.stdout.write(WARN(
                f"  {len(missing)} concept(s) have cached franchise data but ZERO links. "
                f"Likely candidates for `rebuild_franchises_from_cache`."
            ))
            if sample_n > 0:
                for concept, expected in sorted(missing, key=lambda x: -x[1])[:sample_n]:
                    locked = ' [LOCKED]' if concept.franchises_locked else ''
                    self.stdout.write(
                        f"  - {concept.unified_title} ({concept.concept_id!r}, "
                        f"cache lists {expected} link(s)){locked}"
                    )

        self.stdout.write("")  # trailing newline
