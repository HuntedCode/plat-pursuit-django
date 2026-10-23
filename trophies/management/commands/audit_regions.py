"""Read-only report: how far stored trophy-list regions and regional flags have drifted from the
corrected rules in trophies/services/region_service.py. This command only prints.

    python manage.py audit_regions                          # whole catalogue
    python manage.py audit_regions --platform PS5
    python manage.py audit_regions --bucket korea_as_jp     # every list in one bucket

WHY A REPORT FIRST. The fix rebuilds every list's region from its title IDs, which rewrites chips
hunters already see. Run this before the rebuild for the baseline and again after it, so "fixed"
is a number rather than a feeling.

Read the samples by the `played` figure: they are ordered most-played first, so the top of each
section is the mistakes the most hunters are looking at. The `shown` column counts the lists whose
chips actually render today (flagged regional, with a stored region): a wrong label on a list that
is not flagged regional sits in the column unseen.
"""
from collections import Counter

from django.core.management.base import BaseCommand

from trophies.services import region_service
from trophies.util_modules.constants import ALL_PLATFORMS


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
        self._verdicts(stats, judged)
        self._coverage(stats['coverage'])
        w(self.style.MIGRATE_HEADING('Store walk'))
        on_lists, walked = stats['title_ids_on_lists'], stats['title_ids_walked']
        w(f"  {on_lists:,} distinct title IDs sit on trophy lists (whole catalogue, ignores --platform); "
          f"{walked:,} walked ({100 * walked / on_lists if on_lists else 0:.1f}%), "
          f"{stats['title_ids_with_region']:,} with any region.")
        w("  An unwalked title ID's label is its dataset prefix (a guess for worldwide products), and the "
          "regional rule does not act on it yet.\n")
        self._sections(by_bucket, only, opts['sample'])

    # -- output ---------------------------------------------------------------------------

    def _summary(self, by_bucket, total):
        w = self.stdout.write
        w(self.style.MIGRATE_HEADING('Summary'))
        w(f'  {"bucket":<22}{"lists":>9}{"%":>7}{"shown":>9}{"plays":>12}')
        for bucket in region_service.BUCKETS:
            bucket_rows = by_bucket.get(bucket, [])
            shown = sum(1 for r in bucket_rows if r['shown'])
            plays = sum(r['played'] for r in bucket_rows)
            w(f'  {bucket:<22}{len(bucket_rows):>9,}{100 * len(bucket_rows) / total:>6.1f}%'
              f'{shown:>9,}{plays:>12,}')
        for bucket in (region_service.REGIONAL_MISSED, region_service.REGIONAL_STALE):
            reasons = Counter(r['reason'] for r in by_bucket.get(bucket, []))
            if reasons:
                parts = ', '.join(f'{n:,} {reason}' for reason, n in reasons.most_common())
                w(f'  ({bucket}: {parts})')
        w('')

    def _verdicts(self, stats, total):
        """How the regional rule splits the catalogue. `unjudged` lists keep their flag as it is
        (owner, 2026-10-23), so they are context here, not a bucket."""
        w = self.stdout.write
        w(self.style.MIGRATE_HEADING('Regional verdicts by the rule'))
        for verdict in (region_service.REGIONAL, region_service.NOT_REGIONAL, region_service.UNJUDGED):
            n = stats['verdicts'].get(verdict, 0)
            w(f'  {verdict:<14}{n:>9,}{100 * n / total:>6.1f}%')
        w(f"  ({stats['unjudged_flagged']:,} unjudged lists are flagged regional today and keep it: "
          f"they or a sibling have no region data.)")
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
                if row['siblings']:
                    w(f'      {self._siblings(row)}')
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
        tids = ', '.join(
            f'{tid}={prefix or "?"}' + ('' if stores is None else f' sold {"/".join(stores) or "nowhere"}')
            for tid, prefix, stores in row['title_ids']) or 'no title IDs'
        return (f"stored {_codes(row['stored'])}, title IDs say {_codes(row['expected'])} [{tids}]; "
                f"rule says {row['verdict']} ({row['reason']}); "
                f"concept {row['concept_id'] or '-'}, unit {row['unit'] or '-'}")

    @staticmethod
    def _siblings(row):
        """The lists the verdict compared this one against, so a hand check needs no second query.
        `*` marks a disjoint sibling (they sort first, so the cap never hides the deciding one);
        `[admin]` marks a region-locked sibling judged by its admin-set label. Capped, since a
        launcher-style unit (Call of Duty HQ) can hold a dozen."""
        cap = 4
        shown = [f"{'*' if s['disjoint'] else ''}{s['name']} ({s['np_id']}, {'/'.join(s['platforms']) or '?'}) "
                 f"{_codes(s['regions'])}{' [admin]' if s['locked'] else ''}"
                 for s in row['siblings'][:cap]]
        more = len(row['siblings']) - cap
        return 'siblings: ' + '; '.join(shown) + (f'; +{more} more' if more > 0 else '')
