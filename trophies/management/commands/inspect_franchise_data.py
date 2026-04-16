"""
Diagnostic: print raw IGDB franchise/collection data for a concept (or set
of concepts) so we can audit whether bad ConceptFranchise links are coming
from upstream (IGDB returned bad data), from our enrichment logic, or from
data corruption between the two.

Shows three layers per concept:

    1. The raw IGDB API response we stored in IGDBMatch.raw_response
       (`franchise`, `franchises`, `collections` fields). This is the
       authoritative upstream record.

    2. The Franchise records currently in our DB linked to this concept.
       (Their igdb_id should match something in #1.)

    3. The ConceptFranchise rows joining concept ↔ franchise, with is_main.

If #1 and #2 disagree, enrichment dropped/added something it shouldn't.
If #2 looks fine but other concepts are linked to the SAME Franchise row
when they shouldn't be, the issue is upstream collection-tagging on IGDB.

Usage:
    python manage.py inspect_franchise_data --search "College Football"
    python manage.py inspect_franchise_data --concept-id 12345
    python manage.py inspect_franchise_data --franchise-name "Army of Two"
        # → lists every concept currently linked to that franchise
"""
import json

from django.core.management.base import BaseCommand

from trophies.models import (
    Concept, ConceptFranchise, Franchise, IGDBMatch,
)


class Command(BaseCommand):
    help = "Inspect raw IGDB franchise/collection data and stored links for a concept."

    def add_arguments(self, parser):
        parser.add_argument(
            '--search', type=str,
            help='Find concepts whose unified_title contains this string (case-insensitive).',
        )
        parser.add_argument(
            '--concept-id', type=str,
            help='Look up a single concept by its concept_id (PSN identifier).',
        )
        parser.add_argument(
            '--franchise-name', type=str,
            help='Show every concept currently linked to a franchise with this exact name.',
        )

    def handle(self, *args, **options):
        if options['franchise_name']:
            self._inspect_franchise(options['franchise_name'])
            return

        if options['concept_id']:
            concepts = Concept.objects.filter(concept_id=options['concept_id'])
        elif options['search']:
            concepts = Concept.objects.filter(unified_title__icontains=options['search'])
        else:
            self.stdout.write(self.style.ERROR(
                "Provide one of: --search, --concept-id, or --franchise-name."
            ))
            return

        count = concepts.count()
        if count == 0:
            self.stdout.write(self.style.WARNING("No matching concepts found."))
            return
        self.stdout.write(f"Found {count} matching concept(s).\n")

        for concept in concepts.iterator():
            self._inspect_concept(concept)

    def _inspect_concept(self, concept):
        H = self.style.MIGRATE_HEADING
        WARN = self.style.WARNING

        self.stdout.write(H(
            f"\n{'=' * 70}\n"
            f"Concept: {concept.unified_title}  "
            f"(concept_id={concept.concept_id}, pk={concept.pk})\n"
            f"{'=' * 70}"
        ))

        # 1. Raw IGDB response
        try:
            match = concept.igdb_match
        except IGDBMatch.DoesNotExist:
            self.stdout.write(WARN("\n  No IGDB match for this concept."))
            match = None

        if match:
            self.stdout.write(self.style.MIGRATE_LABEL(
                "\n[1] Raw IGDB response (what IGDB actually returned):"
            ))
            self.stdout.write(f"  IGDB game id: {match.igdb_id}")
            self.stdout.write(f"  IGDB name:    {match.igdb_name}")
            self.stdout.write(f"  IGDB slug:    {match.igdb_slug}")
            self.stdout.write(f"  Last synced:  {match.last_synced_at}")
            rr = match.raw_response or {}
            self.stdout.write(f"  raw franchise (singular): {self._fmt(rr.get('franchise'))}")
            self.stdout.write(f"  raw franchises (plural):  {self._fmt(rr.get('franchises'))}")
            self.stdout.write(f"  raw collections:          {self._fmt(rr.get('collections'))}")

        # 2. Franchise records the concept is linked to in our DB
        cfs = ConceptFranchise.objects.filter(concept=concept).select_related('franchise')
        self.stdout.write(self.style.MIGRATE_LABEL(
            f"\n[2] Stored ConceptFranchise links ({cfs.count()}):"
        ))
        if not cfs:
            self.stdout.write("  (none)")
        for cf in cfs:
            f = cf.franchise
            self.stdout.write(
                f"  -> {f.name}  "
                f"(franchise.pk={f.pk}, igdb_id={f.igdb_id}, "
                f"source_type={f.source_type}, is_main={cf.is_main})"
            )

        # 3. Cross-check: which IGDB IDs are in raw response but NOT linked,
        # and vice versa? Catches drift between upstream and our DB.
        if match:
            raw_ids = set()
            singular = (rr.get('franchise') or {}).get('id')
            if singular:
                raw_ids.add(singular)
            for f in (rr.get('franchises') or []):
                if f.get('id'):
                    raw_ids.add(f['id'])
            for c in (rr.get('collections') or []):
                if c.get('id'):
                    raw_ids.add(c['id'])

            stored_ids = {cf.franchise.igdb_id for cf in cfs}

            missing_from_db = raw_ids - stored_ids
            extra_in_db = stored_ids - raw_ids
            if missing_from_db or extra_in_db:
                self.stdout.write(self.style.MIGRATE_LABEL("\n[3] Drift detected:"))
                if missing_from_db:
                    self.stdout.write(WARN(
                        f"  In IGDB response but NOT linked locally: {sorted(missing_from_db)}"
                    ))
                if extra_in_db:
                    self.stdout.write(WARN(
                        f"  Linked locally but NOT in IGDB response: {sorted(extra_in_db)}  "
                        "← stale enrichment, manual edit, or true upstream drift"
                    ))
            else:
                self.stdout.write(self.style.SUCCESS("\n[3] No drift — DB matches IGDB response."))

    def _inspect_franchise(self, name):
        H = self.style.MIGRATE_HEADING

        franchises = Franchise.objects.filter(name__iexact=name)
        if not franchises.exists():
            self.stdout.write(self.style.WARNING(
                f"No Franchise row with exact name '{name}'. Try a partial match:"
            ))
            for f in Franchise.objects.filter(name__icontains=name)[:20]:
                self.stdout.write(
                    f"  - {f.name}  (pk={f.pk}, igdb_id={f.igdb_id}, source_type={f.source_type})"
                )
            return

        for f in franchises:
            self.stdout.write(H(
                f"\n{'=' * 70}\n"
                f"Franchise: {f.name}  "
                f"(pk={f.pk}, igdb_id={f.igdb_id}, source_type={f.source_type})\n"
                f"{'=' * 70}"
            ))
            cfs = ConceptFranchise.objects.filter(franchise=f).select_related('concept')
            self.stdout.write(f"\nLinked concepts ({cfs.count()}):")
            for cf in cfs:
                self.stdout.write(
                    f"  -> {cf.concept.unified_title}  "
                    f"(concept_id={cf.concept.concept_id}, is_main={cf.is_main})"
                )

    @staticmethod
    def _fmt(value):
        if value is None:
            return "(none)"
        if value == [] or value == {}:
            return f"{value} (empty)"
        return json.dumps(value, indent=None, default=str)
