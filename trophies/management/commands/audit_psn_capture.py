from django.core.management.base import BaseCommand
from django.db.models import Count

from trophies.models import PSNConceptData, PSNRawPayload

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
            '--sample', type=int, default=1,
            help='How many rows to print key-level detail for (default 1, 0 to skip).',
        )

    def handle(self, *args, **options):
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
