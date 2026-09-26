"""Read-only report: how far each Contract's curated job profile has drifted from what its
IGDB genres/themes detect today, and how much banked XP is riding on the difference.

The rule itself lives in trophies/services/job_drift.py (shared, so a future repair pipeline
cannot classify differently from the report that sized it). This command only prints.

    python manage.py report_job_drift                      # whole catalogue
    python manage.py report_job_drift --live-only          # only contracts hunters can see
    python manage.py report_job_drift --bucket freelancer_repair --sample 40

WHY A REPORT FIRST. Re-pointing banked grants moves hunters' per-job levels DOWN, so the
question is not "can we" but "on which of these is a disagreement actually an error". Only
`freelancer_repair` and `combo_upgrade` answer that on their own. `narrower` in particular
conflates deliberate
curation (trimming IGDB's peripheral tags is the documented job of a curator) with genuinely
stale data, and nothing stored tells them apart -- so its size decides whether an automatic
path is worth building at all or whether this stays a staff queue.

Read it with the XP column, not the contract count: a hundred drifted contracts nobody has
claimed cost nothing to fix, and one drifted contract with four thousand earners is the whole
problem.
"""
from collections import Counter

from django.core.management.base import BaseCommand

from trophies.services import job_drift
from trophies.util_modules.constants import MAX_CONTRACT_JOBS


def _plural(n):
    """'1 contract' / '12 contracts' -- the read-out is prose, and "1 contracts" in a staff
    report reads as a placeholder somebody forgot to finish."""
    return f"{n:,} contract{'' if n == 1 else 's'}"


class Command(BaseCommand):
    help = "Report Contracts whose jobs disagree with current IGDB detection (read-only)."

    def add_arguments(self, parser):
        parser.add_argument('--live-only', action='store_true', dest='live_only',
                            help='Only contracts that are live. Staged drafts drift too, but '
                                 'nobody has banked XP on them yet.')
        parser.add_argument('--bucket', choices=job_drift.BUCKETS,
                            help='Drill into ONE bucket: prints every contract in it, not a sample.')
        parser.add_argument('--sample', type=int, default=8,
                            help='Contracts listed per bucket, most banked XP first (default 8).')

    def handle(self, *args, **opts):
        live_only = opts['live_only']
        sample_n = opts['sample']
        only = opts['bucket']        # argparse always sets it (None when not passed)
        w = self.stdout.write
        head = self.style.MIGRATE_HEADING

        rows = job_drift.scan(live_only=live_only)
        if not rows:
            w(self.style.WARNING('No contracts found.'))
            return

        by_bucket = {}
        for row in rows:
            by_bucket.setdefault(row['bucket'], []).append(row)
        # Most banked XP first -- the ordering every section uses, so "the top of the list" means
        # the same thing everywhere: what it would cost to fix, not what happens to sort early.
        for bucket_rows in by_bucket.values():
            bucket_rows.sort(key=lambda r: (-r['banked_xp'], -r['banked_hunters'], r['slug']))

        counts = Counter(r['bucket'] for r in rows)
        drifted = [r for r in rows if r['bucket'] not in job_drift.SETTLED]
        no_signal = by_bucket.get(job_drift.NO_SIGNAL, [])

        scope = 'live contracts only' if live_only else 'all contracts, live and staged'
        w(head(f'Job drift -- Contract job profiles vs current IGDB detection ({scope})'))
        w(f'  {len(rows):,} contracts scanned, {sum(1 for r in rows if r["is_live"]):,} of them live.')
        w(f'  Detection pools each contract\'s member + bundle concepts and caps at '
          f'{MAX_CONTRACT_JOBS} jobs by signal strength, exactly as the admin action does.\n')

        self._summary(by_bucket, counts, len(rows))
        self._totals(drifted, no_signal)
        self._sections(by_bucket, only, sample_n)
        if not only:
            self._verdict(by_bucket)

    # -- output ---------------------------------------------------------------------------

    def _summary(self, by_bucket, counts, total):
        w = self.stdout.write
        w(self.style.MIGRATE_HEADING('Summary'))
        w(f'  {"bucket":<24}{"contracts":>10}{"%":>7}{"hunters":>10}{"banked XP":>14}{"pending":>9}')
        for bucket in job_drift.BUCKETS:
            n = counts.get(bucket, 0)
            if not n:
                continue
            bucket_rows = by_bucket[bucket]
            hunters = sum(r['banked_hunters'] for r in bucket_rows)
            xp = sum(r['banked_xp'] for r in bucket_rows)
            pending = sum(r['pending_hunters'] for r in bucket_rows)
            pct = 100 * n / total
            # An uncosted bucket was never measured, so printing zeros there would read as
            # "measured and found to be none".
            if bucket in job_drift.UNCOSTED:
                w(f'  {bucket:<24}{n:>10,}{pct:>6.1f}%{"-":>10}{"-":>14}{"-":>9}')
            else:
                w(f'  {bucket:<24}{n:>10,}{pct:>6.1f}%{hunters:>10,}{xp:>14,}{pending:>9,}')
        w('')

    def _totals(self, drifted, no_signal):
        w = self.stdout.write
        xp = sum(r['banked_xp'] for r in drifted)
        hunters = sum(r['banked_hunters'] for r in drifted)
        pending = sum(r['pending_hunters'] for r in drifted)
        with_xp = sum(1 for r in drifted if r['banked_xp'])
        w(self.style.MIGRATE_HEADING('What is at stake'))
        w(f'  {_plural(len(drifted))} have drifted; {with_xp:,} of them have banked XP on them.')
        w(f'  {xp:,} XP across {hunters:,} hunter-contract pairs would be re-split.')
        # "without claiming ANYTHING": pending counts hunters with ZERO grants, so someone who
        # banked the platinum tier and left the 100% unclaimed reads as banked here, not pending.
        w(f'  {pending:,} more hunters have REACHED a drifted contract without claiming anything '
          f'-- those re-split for free.')
        w('  Hunter figures are per contract, so one hunter holding three drifted contracts '
          'counts three times.')
        # The summary above costs no_signal (it is reachable via --bucket) while these totals do
        # not (it is not drift). Without this line a reader sees a larger number in the table
        # than in the total underneath it and has nothing to explain the gap.
        ns_xp = sum(r['banked_xp'] for r in no_signal)
        # Counts the no-signal rows that actually CARRY the XP, matching the `with_xp` idiom
        # above. `len(no_signal)` would read as "all 50 of these have XP" when one of them does.
        ns_with_xp = sum(1 for r in no_signal if r['banked_xp'])
        if ns_xp:
            w(f'  Excluded from the figures above: {ns_xp:,} XP on {_plural(ns_with_xp)} with '
              f'no IGDB signal -- costed in the summary because they are worth a look, but not '
              f'drift, since there is nothing to compare against.')
        w('')

    def _sections(self, by_bucket, only, sample_n):
        w = self.stdout.write
        buckets = [only] if only else [b for b in job_drift.BUCKETS if b not in job_drift.SETTLED]
        for bucket in buckets:
            bucket_rows = by_bucket.get(bucket)
            if not bucket_rows:
                if only:
                    # An explicit drill-down that printed nothing reads as a broken command.
                    w(self.style.MIGRATE_HEADING(f'{bucket} -- {job_drift.BUCKET_LABELS[bucket]}'))
                    w('  No contracts in this bucket.\n')
                continue
            w(self.style.MIGRATE_HEADING(f'{bucket} -- {job_drift.BUCKET_LABELS[bucket]}'))
            shown = bucket_rows if only else bucket_rows[:sample_n]
            for row in shown:
                w(f'  {self._line(row)}')
                w(f'      {self._diff(row)}')
            if len(bucket_rows) > len(shown):
                w(f'    ... and {len(bucket_rows) - len(shown):,} more '
                  f'(--bucket {bucket} to list them all).')
            w('')

    @staticmethod
    def _line(row):
        state = '' if row['is_live'] else ' [staged]'
        if row['bucket'] in job_drift.UNCOSTED:
            # Reachable only via `--bucket aligned`. These rows are skipped by `attach_stakes`
            # on purpose, so their zeros mean "never asked", not "asked and found none" --
            # printing "no earners" here would state a fact nobody measured, on the bucket
            # most likely to have thousands of them.
            stake = 'stakes not measured (aligned rows are not costed)'
        elif row['banked_xp']:
            stake = f"{row['banked_xp']:,} XP across {row['banked_hunters']:,} hunter(s)"
        elif row['pending_hunters']:
            stake = f"no banked XP; {row['pending_hunters']:,} hunter(s) reached, unclaimed"
        else:
            stake = 'no earners'
        return f"{row['name']} ({row['slug']}){state} -- {stake}"

    @staticmethod
    def _diff(row):
        """The job change itself, as `held -> detected` plus the signed diff."""
        if row['bucket'] == job_drift.NO_SIGNAL:
            # `suggested` here is whatever the fallback produced from nothing, so printing it
            # would assert a detection the data does not support.
            return f"holds {', '.join(row['current']) or '(none)'}; IGDB has no genres or themes"
        held = ', '.join(row['current']) or '(none)'
        detected = ', '.join(row['suggested']) or '(none)'
        parts = []
        if row['added']:
            parts.append('+' + ' +'.join(row['added']))
        if row['removed']:
            parts.append('-' + ' -'.join(row['removed']))
        diff = f"   [{' '.join(parts)}]" if parts else ''
        return f"{held}  ->  {detected}{diff}"

    def _verdict(self, by_bucket):
        """Which buckets an automatic path could safely own, by the promote-only rule."""
        w = self.stdout.write

        def stat(bucket):
            bucket_rows = by_bucket.get(bucket, [])
            return len(bucket_rows), sum(r['banked_xp'] for r in bucket_rows)

        repair_n, repair_xp = stat(job_drift.FREELANCER_REPAIR)
        combo_n, combo_xp = stat(job_drift.COMBO_UPGRADE)
        narrower_n, narrower_xp = stat(job_drift.NARROWER)
        unjobbed_n, _ = stat(job_drift.UNJOBBED)

        w(self.style.MIGRATE_HEADING('Read-out'))
        w('  Unambiguous (a job profile that is strictly wrong, nothing curated is lost):')
        w(f'    freelancer_repair  {_plural(repair_n)}, {repair_xp:,} XP')
        w(f'    combo_upgrade      {_plural(combo_n)}, {combo_xp:,} XP')
        w('  Needs a human (curation and stale data are indistinguishable from the outside):')
        w(f'    narrower           {_plural(narrower_n)}, {narrower_xp:,} XP')
        w('    plus wider / mixed / combo_downgrade / freelancer_regression above.')
        if unjobbed_n:
            w(self.style.WARNING(
                f'  !! {_plural(unjobbed_n)} have NO jobs set. Those bank ZERO XP when '
                f'claimed -- a hunter completes the game, claims, and receives nothing.'))
        w('')
