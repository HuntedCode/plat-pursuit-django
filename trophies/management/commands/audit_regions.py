"""Read-only report: how far stored trophy-list regions and regional flags have drifted from the
corrected rules in trophies/services/region_service.py. This command only prints.

    python manage.py audit_regions                          # whole catalogue
    python manage.py audit_regions --platform PS5
    python manage.py audit_regions --bucket korea_as_jp     # every list in one bucket

WHY A REPORT FIRST. The fix rebuilds every list's region from its title IDs, which rewrites chips
hunters already see. Run this before the rebuild for the baseline and again after it, so "fixed"
is a number rather than a feeling.

Read the samples by the `played` figure: they are ordered most-played first, so the top of each
section is the mistakes the most hunters are looking at.
"""
from django.core.management.base import BaseCommand

from trophies.services import region_service
from trophies.util_modules.constants import ALL_PLATFORMS, MODERN_PLATFORMS


def _codes(codes):
    return '/'.join(codes) if codes else '(none)'


def _lists(n):
    """'1 list' / '12 lists': a staff report reading "1 lists" looks unfinished."""
    return f"{n:,} list{'' if n == 1 else 's'}"


class Command(BaseCommand):
    help = "Report trophy lists whose region or regional flag disagrees with the corrected rules (read-only)."

    def add_arguments(self, parser):
        parser.add_argument('--bucket', choices=region_service.BUCKETS,
                            help='Drill into ONE bucket: prints every list in it, not a sample.')
        parser.add_argument('--sample', type=int, default=8,
                            help='Lists shown per bucket, most played first (default 8).')
        parser.add_argument('--platform', choices=ALL_PLATFORMS,
                            help='Only lists on this platform.')

    def handle(self, *args, **opts):
        w = self.stdout.write
        only = opts['bucket']
        rows, stats = region_service.scan(platform=opts['platform'])
        scope = f"{opts['platform']} lists" if opts['platform'] else 'all lists'

        w(self.style.MIGRATE_HEADING(f'Region audit -- stored regions vs the corrected rules ({scope})'))
        if not stats['scanned']:
            w(self.style.WARNING('  No trophy lists found.'))
            return
        w(f"  {_lists(stats['scanned'])} scanned; {stats['locked']:,} skipped because an admin locked "
          f"their region.")
        # Percentages are of the lists actually JUDGED: a locked list can never be in a bucket, so
        # counting it would dilute every figure.
        judged = stats['scanned'] - stats['locked']
        if not judged:
            w('  Every list is region-locked; nothing to judge.')
            return
        w(f"  {_lists(len(rows))} ({100 * len(rows) / judged:.1f}% of the {judged:,} judged) in at least "
          f"one bucket. A list can be in several.\n")

        by_bucket = {}
        for row in sorted(rows, key=lambda r: (-r['played'], r['pk'])):
            for bucket in row['buckets']:
                by_bucket.setdefault(bucket, []).append(row)

        self._summary(by_bucket, judged)
        self._coverage(stats['coverage'])
        self._sections(by_bucket, only, opts['sample'])
        w(self.style.MIGRATE_HEADING('Blind spot'))
        w("  Sync overwrites any title ID stored as 'IP' (unknown, including dataset rows with no region) "
          "with the US store's answer, so those title IDs hold the same wrong NA as their lists and the "
          "lists are not counted above. Run populate_title_ids first to reset the dataset ones.")

    # -- output ---------------------------------------------------------------------------

    def _summary(self, by_bucket, total):
        w = self.stdout.write
        w(self.style.MIGRATE_HEADING('Summary'))
        w(f'  {"bucket":<24}{"lists":>9}{"%":>7}{"plays":>12}')
        for bucket in region_service.BUCKETS:
            bucket_rows = by_bucket.get(bucket, [])
            plays = sum(r['played'] for r in bucket_rows)
            w(f'  {bucket:<24}{len(bucket_rows):>9,}{100 * len(bucket_rows) / total:>6.1f}%{plays:>12,}')
        missed = by_bucket.get(region_service.REGIONAL_MISSED, [])
        legacy = sum(1 for r in missed if not set(r['platforms']) & set(MODERN_PLATFORMS))
        if legacy:
            # Sync only ever checks PS4/PS5, so these were never going to be flagged. They are real
            # (the corrected rule covers every platform), but they are a coverage gap, not a mistake.
            w(f'  ({legacy:,} of the {region_service.REGIONAL_MISSED} lists are PS3/Vita/other, which '
              f'sync never checks.)')
        stale = by_bucket.get(region_service.REGIONAL_STALE, [])
        asian = sum(1 for r in stale if r['asian_only'])
        if asian:
            # The one bucket where some rows may be intended: a lone list labeled only JP/AS/KR/CN is
            # probably an Asia-only release, which a hunter may want flagged even with no sibling.
            w(f'  ({asian:,} of the {region_service.REGIONAL_STALE} lists are labeled only with Asian '
              f'regions, likely Asia-only releases.)')
        w('')

    def _coverage(self, coverage):
        w = self.stdout.write
        w(self.style.MIGRATE_HEADING('Region coverage by platform'))
        w(f'  {"platform":<10}{"lists":>9}{"with region":>14}{"%":>7}')
        order = {p: i for i, p in enumerate(ALL_PLATFORMS)}
        for plat in sorted(coverage, key=lambda p: (order.get(p, len(order)), p)):
            lists, with_region = coverage[plat]
            w(f'  {plat:<10}{lists:>9,}{with_region:>14,}{100 * with_region / lists:>6.1f}%')
        w('')

    def _sections(self, by_bucket, only, sample_n):
        w = self.stdout.write
        for bucket in [only] if only else region_service.BUCKETS:
            bucket_rows = by_bucket.get(bucket, [])
            if not bucket_rows and not only:
                continue
            w(self.style.MIGRATE_HEADING(f'{bucket} -- {region_service.BUCKET_LABELS[bucket]}'))
            if not bucket_rows:
                # An explicit drill-down that printed nothing reads as a broken command.
                w('  No lists in this bucket.\n')
                continue
            shown = bucket_rows if only else bucket_rows[:max(sample_n, 0)]
            for row in shown:
                w(f'  {self._line(row)}')
                w(f'      {self._detail(row)}')
            if len(bucket_rows) > len(shown):
                w(f'    ... and {len(bucket_rows) - len(shown):,} more (--bucket {bucket} to list them all).')
            w('')

    @staticmethod
    def _line(row):
        flag = 'regional' if row['is_regional'] else 'not regional'
        return (f"{row['name']} ({row['np_id']}, {'/'.join(row['platforms']) or '?'}) -- "
                f"{row['played']:,} plays, {flag}")

    @staticmethod
    def _detail(row):
        tids = ', '.join(f'{tid}={code or "?"}' for tid, code in row['title_ids']) or 'no title IDs'
        return (f"stored {_codes(row['stored'])}, title IDs say {_codes(row['expected'])} [{tids}]; "
                f"concept {row['concept_id'] or '-'}, unit {row['unit'] or '-'}")
