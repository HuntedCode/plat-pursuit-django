from django.core.management.base import BaseCommand
from django.db.models import Count, Q

from trophies.models import Concept, PSNConceptData, PSNRawPayload

#: (field, the value that means "we captured nothing here"). Every one of these is lifted from a
#: specific key of PSN's response, so a field that is empty on EVERY row means we are reading the
#: wrong key, not that PSN is withholding it.
_PARSED_FIELDS = [
    ('name', ''),
    ('name_en', ''),
    ('publisher_name', ''),
    ('genres', []),
    ('subgenres', []),
    ('descriptions', {}),
    ('content_rating', {}),
    ('media', {}),
]


class Command(BaseCommand):
    help = (
        "Report on what PSN metadata capture is actually storing. Read-only. "
        "The point is to catch a wrong key: the response shapes were inferred from how the sync "
        "code reads them, never from a recorded fixture, so the first real rows are the first "
        "genuine test of whether each field is being read correctly. A field empty on 100% of rows "
        "is the signature of that bug -- it is what the `media` field looked like before it was "
        "found to be a dict rather than a list."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--gap', action='store_true',
            help='Classify the concepts that have NO PSN row, so the shortfall after a sweep can '
                 'be split into "we never asked" and "PSN had nothing to give". Pure DB work, no '
                 'PSN calls.',
        )
        parser.add_argument(
            '--sample', type=int, default=1,
            help='How many rows to print key-level detail for (default 1, 0 to skip).',
        )

    def handle(self, *args, **options):
        if options['gap']:
            return self._report_gap()

        total = PSNConceptData.objects.count()
        payloads = PSNRawPayload.objects.count()

        self.stdout.write(f"PSNConceptData rows: {total}")
        self.stdout.write(f"PSNRawPayload rows:  {payloads}")

        if total == 0:
            self.stdout.write(self.style.WARNING(
                "Nothing captured yet. Either no title has been resolved since deploy, or "
                "PSN_METADATA_CAPTURE_ENABLED is off in the WORKER's environment (it is read "
                "per-service, so the web service having it on proves nothing)."
            ))
            return

        if payloads < total:
            self.stdout.write(self.style.WARNING(
                f"{total - payloads} row(s) have no raw payload. The two writes share a "
                f"transaction, so this should be 0."
            ))

        self.stdout.write("\nParsed fields (empty means the key produced nothing):")
        suspect = []
        for field, empty_value in _PARSED_FIELDS:
            empty = PSNConceptData.objects.filter(**{field: empty_value}).count()
            pct = empty * 100 // total
            style = self.style.ERROR if pct == 100 else (
                self.style.WARNING if pct >= 90 else self.style.SUCCESS
            )
            self.stdout.write(style(f"  {field:16} empty on {empty:>6}/{total} ({pct}%)"))
            if pct == 100:
                suspect.append(field)

        self.stdout.write("\nAnswering storefronts:")
        regions = (
            PSNConceptData.objects.values('country', 'language')
            .annotate(n=Count('id')).order_by('-n')
        )
        for row in regions:
            label = f"{row['country'] or '(blank)'}/{row['language'] or '(blank)'}"
            self.stdout.write(f"  {label:16} {row['n']}")
        if any(not r['country'] for r in regions):
            self.stdout.write(self.style.ERROR(
                "  Blank country: the answering region is not reaching capture, so rows cannot be "
                "interpreted and two regions of one concept will collide on the unique key."
            ))

        for row in PSNConceptData.objects.order_by('-last_seen_at')[:options['sample']]:
            self.stdout.write(f"\nSample: {row.psn_concept_id} ({row.country}/{row.language})")
            self.stdout.write(f"  name_en:     {row.name_en!r}")
            self.stdout.write(f"  name:        {row.name!r}")
            self.stdout.write(f"  media keys:  {sorted(row.media)}")
            self.stdout.write(f"    root:      {sorted((row.media.get('root') or {}))}")
            self.stdout.write(f"    default:   {sorted((row.media.get('default_product') or {}))}")
            self.stdout.write(f"  descriptions:{sorted(row.descriptions)}")
            # getattr, not row.raw: a missing OneToOne raises RelatedObjectDoesNotExist rather than
            # returning None, and a diagnostic must not crash on the very state it exists to report.
            raw = getattr(row, 'raw', None)
            if raw is None:
                self.stdout.write(self.style.ERROR("  raw payload: MISSING"))
            else:
                self.stdout.write(f"  raw keys:    {sorted(raw.payload)}")

        if suspect:
            self.stdout.write(self.style.ERROR(
                f"\n{len(suspect)} field(s) empty on every row: {', '.join(suspect)}. "
                f"Compare the key each one reads in psn_metadata_service against the sample's raw "
                f"keys above before backfilling further -- a wrong key is cheapest to fix now."
            ))
        else:
            self.stdout.write(self.style.SUCCESS("\nNo field is empty across the board."))

    def _report_gap(self):
        """Split the uncaptured concepts by CAUSE, using only the database.

        A sweep covers fewer concepts than exist, and the shortfall is not one thing. Some concepts
        the sweep could never reach (no game, no title_id, no resolvable platform); the rest it did
        reach and PSN answered sparsely. Those two need opposite responses -- the first is fixable
        by changing what we enqueue, the second is not fixable by re-running at all -- and re-running
        a catalogue sweep to find out costs thousands of PSN calls to learn nothing.

        Note the honest limit: we do not record capture ATTEMPTS, only successes. So "reachable but
        uncaptured" cannot distinguish "swept, PSN gave nothing" from "never swept". It is an upper
        bound on what a re-run could still win.
        """
        total = Concept.objects.count()
        captured = Concept.objects.filter(psn_data__isnull=False).distinct().count()
        uncaptured = Concept.objects.filter(psn_data__isnull=True)
        gap = uncaptured.count()

        self.stdout.write(f"Concepts: {total}")
        self.stdout.write(f"  with a PSN row:    {captured}")
        self.stdout.write(f"  without:           {gap}")
        if not gap:
            self.stdout.write(self.style.SUCCESS("Nothing to classify."))
            return

        no_games = uncaptured.filter(games__isnull=True).count()
        # Reachable means the sweep's own filters would let it through: AT LEAST ONE game carrying a
        # title_id. This has to be a filtered COUNT rather than `.exclude(games__title_ids=[])`,
        # because exclude() across a multi-valued relation drops the whole concept when ANY of its
        # games matches -- so a concept with one titleless PS3 entry alongside a perfectly sweepable
        # PS4 one was being reported as unreachable, and its platform vanished from the breakdown
        # below. That is the difference between "PSN had nothing for these" and "we never asked",
        # which is the entire question this report exists to answer.
        reachable = (
            uncaptured.annotate(n=Count('games', filter=~Q(games__title_ids=[])))
            .filter(n__gt=0).count()
        )
        no_title_ids = gap - no_games - reachable
        stubs = uncaptured.filter(concept_id__startswith='PP_').count()

        self.stdout.write("\nWhy each uncaptured concept has no row:")
        self.stdout.write(f"  no games at all:            {no_games:>6}  (the sweep walks Games; nothing to enqueue)")
        self.stdout.write(f"  games, but no title_id:     {no_title_ids:>6}  (excluded by the sweep; no id to ask PSN about)")
        self.stdout.write(f"  reachable, still uncaptured:{reachable:>6}  (asked, or not yet asked -- see below)")
        self.stdout.write(
            f"\n  of all uncaptured, PP_* stubs: {stubs}  "
            f"(created when PSN returned nothing usable, so these are evidence of a sparse answer)"
        )

        self.stdout.write("\nReachable-but-uncaptured, by platform:")
        for platform in ['PS5', 'PS4', 'PS3', 'PSVITA', 'PSPC']:
            n = (
                uncaptured.annotate(n=Count('games', filter=(
                    Q(games__title_platform__contains=platform) & ~Q(games__title_ids=[])
                )))
                .filter(n__gt=0).count()
            )
            self.stdout.write(f"  {platform:8} {n:>6}")

        self.stdout.write(self.style.WARNING(
            "\nPS3/PSVITA concentration here is the expected shape: the PS3 storefront is retired, "
            "so game_details answers sparsely and the sync falls through to a stub concept without "
            "capturing. Re-running the sweep cannot fix that -- only a different PSN endpoint or "
            "another data source could. Concepts with no games or no title_id are likewise not "
            "fixable by re-running; they need the sweep to enqueue something it currently cannot."
        ))
