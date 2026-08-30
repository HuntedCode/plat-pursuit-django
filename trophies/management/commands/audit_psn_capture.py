from django.core.management.base import BaseCommand
from django.db.models import Count, F, Q, Value
from django.db.models.functions import Replace

from trophies.models import Concept, Game, PSNConceptData, PSNRawPayload, PSNTitleObservation

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
            '--names', action='store_true',
            help='Measure how often trophy-list names diverge from their concept title, classified '
                 'by kind. Sizes the Game-detail list-switcher work (see '
                 'docs/design/games-and-trophy-lists-ia.md). Pure DB work, no PSN calls.',
        )
        parser.add_argument(
            '--sample', type=int, default=1,
            help='How many rows to print key-level detail for (default 1, 0 to skip).',
        )

    def handle(self, *args, **options):
        if options['gap']:
            return self._report_gap()
        if options['names']:
            return self._report_names(options['sample'])

        total = PSNConceptData.objects.count()
        payloads = PSNRawPayload.objects.count()

        self.stdout.write(f"PSNConceptData rows: {total}")
        self.stdout.write(f"PSNRawPayload rows:  {payloads}")

        obs_total = PSNTitleObservation.objects.count()
        self.stdout.write("\nTitle observations (game level, append-on-change):")
        self.stdout.write(f"  rows:              {obs_total}")
        if obs_total:
            covered = PSNTitleObservation.objects.values('np_communication_id').distinct().count()
            games = Game.objects.count()
            # Same-source only: title_stats' name and trophy_titles' name disagree SYSTEMATICALLY
            # ((TM), suffixes), so counting across sources reads as 'every dual-source game was
            # renamed' -- the metric would be noise, and this report exists to be believed.
            renamed = (
                PSNTitleObservation.objects.filter(source='trophy_titles')
                .values('np_communication_id')
                .annotate(names=Count('title_name_raw', distinct=True)).filter(names__gt=1).count()
            )
            by_source = dict(
                PSNTitleObservation.objects.values_list('source')
                .annotate(n=Count('id')).order_by()
            )
            self.stdout.write(f"  titles covered:    {covered}/{games} games")
            self.stdout.write(f"  renamed (>1 name): {renamed}")
            self.stdout.write(f"  by source:         {by_source}")
        else:
            self.stdout.write(
                "  none yet -- fills from slow-path syncs and fast-path page 1; "
                "backfill_psn_game_observations front-loads it."
            )


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

    def _report_names(self, sample):
        """How often does a list's name differ from its concept's title, and HOW?

        The Games/Trophy Lists IA hangs a list switcher off the Game detail page, labeled by list
        names. This sizes that work: if 3% differ and most are list-suffix cases ("Additional
        Trophies"), the switcher UI is small; a bigger or weirder answer should be known BEFORE
        designing around a guess. All counts are DB-side; the sample is LIMIT-bounded.
        """
        from trophies.models import PSNTitleObservation

        # Lists whose concept has a real title to diverge from. PP_* stubs are excluded: their
        # unified_title IS the list name by construction, so they can only agree.
        base = (
            Game.objects.filter(concept__isnull=False)
            .exclude(concept__concept_id__startswith='PP_')
            .exclude(concept__unified_title='')
        )
        total = base.count()
        self.stdout.write(f"Trophy lists with a non-stub concept title: {total}")
        if not total:
            return

        differing_qs = base.exclude(title_name=F('concept__unified_title'))
        differing = differing_qs.count()
        case_only = differing - base.exclude(
            title_name__iexact=F('concept__unified_title')).count()

        # Kind classification, coarse on purpose (a sizing instrument, not a parser):
        # a list name that STARTS WITH the concept title and continues is the suffix class --
        # "Vampire Survivors Additional Trophies", "X Trophy Set", "Y PS5" -- exactly the cases the
        # switcher label exists to show. Everything else differs substantively (regional names,
        # renames, IGDB promotions).
        # Excluding iexact keeps the buckets a true partition: istartswith also matches a
        # whole-string case variant, which double-counted case-only rows in here (found by test).
        suffix = differing_qs.exclude(
            title_name__iexact=F('concept__unified_title')).filter(
            title_name__istartswith=F('concept__unified_title')).count()
        substantive = differing - case_only - suffix

        def line(label, n):
            pct = n * 100 // total
            self.stdout.write(f"  {label:34} {n:>7} ({pct}%)")

        self.stdout.write("\nList name vs concept title:")
        line('identical', total - differing)
        line('case-only difference', case_only)
        line('concept title + suffix', suffix)
        line('substantively different', substantive)

        # The helper-chain question: how often is Game.title_name NOT what PSN currently says?
        # Approximate cleaning DB-side (strip the marks clean_game_title strips); Roman-numeral and
        # suffix normalization are not reproduced here, so treat small counts as noise.
        obs_named = (
            PSNTitleObservation.objects.filter(source='trophy_titles', game__isnull=False)
            .exclude(title_name_raw='')
        )
        obs_total = obs_named.values('np_communication_id').distinct().count()
        approx_clean = Replace(Replace(F('title_name_raw'), Value('™'), Value('')),
                               Value('®'), Value(''))
        obs_differs = (
            obs_named.annotate(clean_raw=approx_clean)
            .exclude(game__title_name=F('clean_raw'))
            .values('np_communication_id').distinct().count()
        )
        self.stdout.write(
            f"\nObserved lists whose stored title_name != PSN's current name (approx-cleaned): "
            f"{obs_differs}/{obs_total}"
        )
        self.stdout.write(
            "  (IGDB promotions, merge renames, and un-reproduced normalization all land here; "
            "the display_list_name helper chain is what reconciles it.)"
        )

        if sample:
            self.stdout.write("\nSample of substantive differences (list name vs concept title):")
            rows = (
                differing_qs.exclude(title_name__iexact=F('concept__unified_title'))
                .exclude(title_name__istartswith=F('concept__unified_title'))
                .values_list('title_name', 'concept__unified_title')[:max(sample, 15)]
            )
            for list_name, concept_title in rows:
                self.stdout.write(f"  list: {list_name!r:60} concept: {concept_title!r}")

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

        # Both buckets get a platform breakdown. Only breaking down the reachable one was a real
        # blind spot: on the first prod run "games, but no title_id" was 3147 of a 3813 gap and went
        # entirely unclassified, while the platform table showed a modern-looking PS4/PS5 remainder.
        # Reading that table alone suggested the opposite conclusion to the one the data supports.
        no_ids = (
            uncaptured.filter(games__isnull=False)
            .annotate(n=Count('games', filter=~Q(games__title_ids=[]))).filter(n=0)
        )
        reachable_qs = (
            uncaptured.annotate(n=Count('games', filter=~Q(games__title_ids=[]))).filter(n__gt=0)
        )

        self.stdout.write("\nBy platform (a concept spanning platforms counts under each):")
        self.stdout.write(f"  {'':8} {'no title_id':>12} {'reachable':>12}")
        for platform in ['PS5', 'PS4', 'PS3', 'PSVITA', 'PSPC']:
            # pk__in against a queryset, not a materialised list: these buckets run to thousands of
            # rows on prod and the point is to keep every number a DB-side aggregate.
            without = Concept.objects.filter(
                pk__in=no_ids.values('pk'), games__title_platform__contains=platform
            ).distinct().count()
            within = Concept.objects.filter(
                pk__in=reachable_qs.values('pk'), games__title_platform__contains=platform
            ).distinct().count()
            self.stdout.write(f"  {platform:8} {without:>12} {within:>12}")

        # Data-driven, not a canned narrative. An earlier version asserted a PS3/Vita conclusion
        # unconditionally, which on the first real prod run contradicted the numbers above it.
        if no_title_ids > reachable:
            self.stdout.write(self.style.WARNING(
                f"\nLargest bucket is 'no title_id' ({no_title_ids}), not sparse PSN answers "
                f"({stubs} stubs). title_ids are only populated by the title_stats walk, which "
                f"covers modern titles, so a game that only ever arrived via the trophy list never "
                f"gets one and can never be asked about. Re-running the sweep cannot win these -- "
                f"they need a title_id from somewhere else. Read the platform split above: mostly "
                f"PS3/PSVITA is structural, mostly PS4/PS5 is a real hole in title_id collection."
            ))
        else:
            self.stdout.write(self.style.WARNING(
                f"\nLargest bucket is 'reachable' ({reachable}): asked, or not yet asked. Since "
                f"attempts are not recorded, re-running the sweep with --missing-only is the "
                f"cheapest way to find out how many are winnable."
            ))
