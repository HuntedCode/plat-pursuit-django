"""Seed SeriesEditionStanding from the JSON maps the per-edition board used to read.

WHY A BACKFILL AT ALL. The store is written by `badge_xp.apply_changes`, so it fills itself as profiles
sync -- but only as they sync, and an unsynced hunter would silently vanish from the edition board they
were on the day before. `evaluate_badges --all` would populate it properly, and it walks every scraped
profile's whole library, which is hours. This reads the rows the old board already read and lands the
same answer in one table scan.

WHAT IT CAN AND CANNOT RECOVER. `group_progress` and `group_xp` are exact -- they are per-edition and
already materialized, so points, stages and the denominator come across whole. `advanced_at` cannot:
`SeriesBadgeStanding` carries ONE date for the whole series (the furthest-along edition's), which is the
very thing the new store exists to stop tiebreaking on. So it is seeded from the series-wide value here,
which makes the board behave EXACTLY as it does today until the next `evaluate_badges` writes each
edition's own date, and better after. Degrading to today's behaviour is the point: no hunter's rank moves
at deploy, and the fix arrives with the next nightly.

IDEMPOTENT. Re-running upserts the same rows, so it is safe after a partial run and safe to run again
once evaluation has started filling real dates -- though at that point it would OVERWRITE those dates
with the series-wide seed again, so `--only-missing` (the default) skips any series that already has
per-edition rows for that profile. Pass `--force` to rewrite them.
"""
from django.core.management.base import BaseCommand

from trophies.models import SeriesBadgeStanding, SeriesEditionStanding

#: Rows per INSERT. Big enough that the round trips do not dominate, small enough that the batch and its
#: conflict resolution stay well inside `work_mem` on the web box.
BATCH = 2_000

#: What an upsert refreshes. Deliberately every column but the key: a re-run after a partial one has to be
#: able to correct a row, not just skip it.
_UPDATE_FIELDS = ['xp', 'stages_cleared', 'gating_count', 'advanced_at', 'country_code', 'is_linked']


class Command(BaseCommand):
    help = "Seed SeriesEditionStanding from SeriesBadgeStanding's group_progress / group_xp maps."

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Count the rows that would be written, without writing.')
        parser.add_argument('--force', action='store_true',
                            help='Rewrite rows that already exist, re-seeding advanced_at from the '
                                 'series-wide value. Off by default so a re-run cannot undo the '
                                 'per-edition dates a later evaluation has filled in.')
        parser.add_argument('--series', default='',
                            help='Limit to one series slug (for spot-checking before a full run).')

    def handle(self, *args, **options):
        qs = SeriesBadgeStanding.objects.all()
        if options['series']:
            qs = qs.filter(series_slug=options['series'])

        existing = set()
        if not options['force']:
            # (profile, series) rather than the full key: presence of ANY edition row means this profile's
            # series has been through the real write seam, and re-seeding one edition of it from the
            # series-wide date would put back exactly the tiebreak this store removed.
            ex = SeriesEditionStanding.objects.all()
            if options['series']:
                ex = ex.filter(series_slug=options['series'])
            existing = set(ex.values_list('profile_id', 'series_slug').distinct())

        pending, written, skipped = [], 0, 0
        fields = ('profile_id', 'series_slug', 'group_progress', 'group_xp',
                  'advanced_at', 'country_code', 'is_linked')
        for pid, slug, prog, xp_map, advanced, country, linked in qs.values_list(*fields).iterator(
                chunk_size=BATCH):
            if (pid, slug) in existing:
                skipped += 1
                continue
            for key, pair in (prog or {}).items():
                # `[cleared, gating]`, and only a STARTED edition gets a row -- the store's membership
                # rule, which `group_progress` does not apply because the Collection needs the untouched
                # editions for their denominators.
                if not isinstance(pair, (list, tuple)) or len(pair) != 2 or not pair[0]:
                    continue
                pending.append(SeriesEditionStanding(
                    profile_id=pid, series_slug=slug, platform_group_key=key,
                    xp=int((xp_map or {}).get(key) or 0),
                    stages_cleared=int(pair[0]), gating_count=int(pair[1] or 0),
                    advanced_at=advanced, country_code=country or '', is_linked=linked,
                ))
            if len(pending) >= BATCH:
                written += self._flush(pending, options['dry_run'])
                pending = []
        written += self._flush(pending, options['dry_run'])

        prefix = '[dry-run] ' if options['dry_run'] else ''
        msg = f'{prefix}{written} edition standings from {qs.count()} series standings'
        if skipped:
            msg += f' ({skipped} series standings skipped, already seeded -- pass --force to rewrite)'
        self.stdout.write(self.style.SUCCESS(msg) if not options['dry_run'] else msg)

    @staticmethod
    def _flush(rows, dry_run):
        if not rows or dry_run:
            return len(rows)
        SeriesEditionStanding.objects.bulk_create(
            rows, update_conflicts=True,
            unique_fields=['profile', 'series_slug', 'platform_group_key'],
            update_fields=_UPDATE_FIELDS,
        )
        return len(rows)
