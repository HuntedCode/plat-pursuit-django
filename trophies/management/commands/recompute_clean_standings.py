"""Rebuild `ProfileTrophyStanding` -- the Shovelware Free board's store -- from EarnedTrophy ground truth.

THE ONLY WRITER of this table, and that is the design rather than a convenience. Shovelware status is a
property of the GAME, so one game being re-flagged invalidates the standing of every hunter who ever
earned a trophy on it. That fan-out is invisible to a per-row signal, which is why this store has no
incremental path at all: it is recomputed from scratch, in a batch seam, and can therefore never drift.

WHY THIS MUST NEVER MOVE INTO SYNC. `ProfileBadgeStanding` used to carry trophy counts and they were
deleted in 2026-08: maintaining a full-library `EarnedTrophy` aggregate per profile was affordable while
the seam ran from a management command and became a per-sync cost the moment the engine was wired into
`sync_complete`. This command is that same aggregate. It is affordable because it runs once a night over
a bounded population; called per sync it would re-make the 2026-08 mistake exactly.

ORDERING. It runs as a `nightly` STEP, immediately after `update_shovelware`, because it reads the flags
that command writes. That dependency is expressed as step order rather than as wall-clock spacing in the
Render dashboard -- which is the whole reason `nightly` exists, and which matters here because
`update_shovelware` and `nightly` both sat at 04:00.
"""
import time

from django.core.cache import cache
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from trophies.models import (
    SHOVELWARE_FLAGGED_STATUSES, EarnedTrophy, Profile, ProfileTrophyStanding,
)

#: Where a budget-capped sweep left off, so the next run resumes rather than restarting. Not a source of
#: truth for anything -- losing it re-runs a full recompute, which is a no-op.
CURSOR_KEY = 'lb:clean_standings:cursor'
CURSOR_TTL = 60 * 60 * 24 * 7


class Command(BaseCommand):
    help = (
        'Rebuild ProfileTrophyStanding (the Shovelware Free board) from EarnedTrophy, '
        'excluding trophies earned on flagged games.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Compute and report deltas without writing.')
        parser.add_argument('--chunk-size', type=int, default=200,
                            help='Profiles aggregated per query.')
        parser.add_argument('--max-minutes', type=int, default=30,
                            help='Wall-clock budget. Exits cleanly between chunks if exceeded.')
        parser.add_argument('--profile-ids', nargs='*', type=int, default=None,
                            help='Optional subset, for ad-hoc repair runs.')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        # A zero here is a ZeroDivisionError below and a negative makes `range` empty -- which would write
        # nothing and still report success, so a typo'd flag would look like a clean run.
        chunk_size = max(1, options['chunk_size'])
        deadline = time.monotonic() + options['max_minutes'] * 60
        start = time.monotonic()

        explicit = options['profile_ids']
        # RESUME POINT. A budgeted run that always restarted at the beginning would re-process the same
        # prefix every night, write nothing (change detection skips it), burn the same budget in the same
        # place, and never reach the tail -- so the hunters past the cutoff would be permanently absent
        # from the board, with the cron reporting success. The cursor is what makes "deferred to the next
        # run" true rather than a hopeful phrase. Losing it (cache eviction) restarts the sweep, which is
        # safe: this command is a full recompute, so a repeated pass is a no-op.
        after_id = 0 if explicit else (cache.get(CURSOR_KEY) or 0)

        if explicit:
            all_ids = sorted(set(explicit))
        else:
            all_ids = list(self._population(after_id))

        total = len(all_ids)
        chunks_total = (total + chunk_size - 1) // chunk_size
        self.stdout.write(self.style.NOTICE(
            f'recompute_clean_standings starting: {total} profiles, chunk={chunk_size}, '
            f'budget={options["max_minutes"]}min, dry_run={dry_run}'
            + (f', resuming after profile {after_id}' if after_id else '')
        ))

        written = 0
        chunks_done = 0
        capped = False
        for chunk_start in range(0, total, chunk_size):
            # ALWAYS ONE CHUNK, whatever the budget says. Checking the deadline before any work means a
            # budget too small for a single chunk does nothing, advances no cursor, and does the same
            # nothing every night -- which is the starvation this cursor exists to end, reintroduced by
            # the guard meant to bound the run. One chunk of forward progress per run is the floor.
            if chunks_done and time.monotonic() >= deadline:
                capped = True
                break
            chunk = all_ids[chunk_start:chunk_start + chunk_size]
            written += self._process_chunk(chunk, dry_run)
            chunks_done += 1
            if not explicit and not dry_run:
                # Advanced per CHUNK, not at the end: the point is that the NEXT run starts where this one
                # actually stopped, and a run killed mid-sweep (deploy, OOM) has stopped too.
                cache.set(CURSOR_KEY, chunk[-1], CURSOR_TTL)

        if not explicit and not dry_run and not capped:
            cache.delete(CURSOR_KEY)          # a full pass finished; the next one starts from the top

        if capped:
            self.stdout.write(self.style.WARNING(
                f'Hit max-minutes budget after {chunks_done}/{chunks_total} chunks. '
                f'{chunks_total - chunks_done} deferred; the next run resumes after profile '
                f'{all_ids[chunk_start - 1] if chunks_done else after_id}.'
            ))
        verb = 'Would write' if dry_run else 'Wrote'
        self.stdout.write(self.style.SUCCESS(
            f'recompute_clean_standings complete in {time.monotonic() - start:.1f}s. '
            f'{verb} {written} standing(s) across {chunks_done} chunk(s).'
        ))

    @staticmethod
    def _population(after_id=0):
        """Which profiles get a row: LINKED hunters, plus anyone who already has one, ASCENDING BY ID.

        The second half is not redundant, and `recompute_job_xp --all` carries the same pair for the same
        reason: a hunter who UNLINKS, or whose trophies were removed, keeps a row that a linked-only sweep
        would never revisit -- so their stale figures would sit on the board forever. Including existing
        rows is what lets this command correct them.

        LINKED-ONLY on the first half, which deviates from the badge and career stores deliberately. Those
        are written for every profile because their rows serve profile pages too, so the `is_linked` gate
        is applied at READ and verifying an account reveals a standing that already existed. This table
        serves ONE board and nothing else, so a row for an unlinked profile would be computed, stored and
        never read -- roughly 250,000 of them at current scale. The cost of the deviation is that a
        newly-verified hunter is absent from this board until the next nightly run, rather than appearing
        the moment they verify.

        A LEFT JOIN rather than the `.union()` this started as. A bare UNION has no defined row order in
        Postgres, so the chunking walked the population in an order that was not merely arbitrary but
        could differ run to run -- and with a budget cap that decides WHICH hunters get stranded. Ordering
        is what makes the cursor in `handle` mean anything. `clean_standing` is a OneToOne, so the join
        cannot multiply rows and no DISTINCT is needed.
        """
        return (
            Profile.objects
            .filter(Q(is_linked=True) | Q(clean_standing__isnull=False))
            .filter(id__gt=after_id)
            .order_by('id')
            .values_list('id', flat=True)
        )

    def _process_chunk(self, profile_ids, dry_run):
        """One GROUP BY for the chunk's counts, one read of the profiles, one create + one update."""
        now = timezone.now()
        # NO `hide_hiddens` FILTER, and that is the decision rather than an omission. It was built and
        # reverted: a board figure has to mean the same thing on EVERY row, and `hide_hiddens` is a
        # per-hunter DISPLAY preference ("hide these from MY list"), not a claim about what was earned.
        # Honouring it would rank two hunters by two different rules and leave the board unreproducible by
        # anyone but its owner. `Profile.total_trophies_raw` exists so the Trophies board can say the same
        # thing; between them, both boards now rank on "every trophy synced to us".
        #
        # `hide_zeros` could not apply here in any case: it excludes games with ZERO earned trophies,
        # which contribute nothing to a count of EARNED ones. It does not move `Profile.total_trophies`
        # either, for the same reason -- on a profile it only changes `total_unearned` and the average.
        rows = (
            EarnedTrophy.objects
            .filter(profile_id__in=profile_ids, earned=True)
            .exclude(trophy__game__shovelware_status__in=SHOVELWARE_FLAGGED_STATUSES)
            .values('profile_id')
            .annotate(
                bronze=Count('id', filter=Q(trophy__trophy_type='bronze')),
                silver=Count('id', filter=Q(trophy__trophy_type='silver')),
                gold=Count('id', filter=Q(trophy__trophy_type='gold')),
                platinum=Count('id', filter=Q(trophy__trophy_type='platinum')),
            )
        )
        counts = {r['profile_id']: r for r in rows}

        # The mirrors are stamped from the SOURCE here, exactly as every other recompute seam stamps
        # them. `_propagate_profile_flags_to_standings` covers the edges between runs; this covers birth.
        owners = dict(
            Profile.objects.filter(id__in=profile_ids)
            .values_list('id', 'country_code')
        )
        linked = set(
            Profile.objects.filter(id__in=profile_ids, is_linked=True).values_list('id', flat=True)
        )
        existing = {
            s.profile_id: s
            for s in ProfileTrophyStanding.objects.filter(profile_id__in=profile_ids)
        }

        to_create, to_update = [], []
        for pid in profile_ids:
            if pid not in owners:
                continue                     # deleted between the population read and now
            c = counts.get(pid, {})
            bronze = c.get('bronze', 0)
            silver = c.get('silver', 0)
            gold = c.get('gold', 0)
            plat = c.get('platinum', 0)
            # The SUM OF THE TIERS. Counted here rather than stored per tier: three tier columns lived on
            # this model until nothing turned out to read them (`page()` passes only the two figures
            # `board_window`'s `extra` maps), so they were dropped before they shipped.
            trophies = bronze + silver + gold + plat
            fields = {
                'clean_plats': plat, 'clean_trophies': trophies,
                'country_code': owners[pid] or '', 'is_linked': pid in linked,
            }

            row = existing.get(pid)
            if row is None:
                to_create.append(ProfileTrophyStanding(profile_id=pid, **fields))
                continue
            if any(getattr(row, k) != v for k, v in fields.items()):
                for k, v in fields.items():
                    setattr(row, k, v)
                # STAMPED BY HAND. `updated_at` is `auto_now`, which Django applies in `Model.save()` --
                # `bulk_update` does not call it, so without this the timestamp stays frozen at row
                # creation while the figures beside it move. The one field that answers "how stale is
                # this board" would have been the one field guaranteed to be wrong.
                row.updated_at = now
                to_update.append(row)

        if not dry_run:
            # ONE transaction for the chunk. Without it a bulk_update that raised after a successful
            # bulk_create left the chunk half-applied -- some hunters recomputed, the rest stale -- and
            # the command aborted, so nothing revisited them until the sweep next reached this chunk.
            with transaction.atomic():
                if to_create:
                    ProfileTrophyStanding.objects.bulk_create(to_create, batch_size=500)
                if to_update:
                    ProfileTrophyStanding.objects.bulk_update(
                    to_update,
                        ['clean_plats', 'clean_trophies', 'country_code', 'is_linked', 'updated_at'],
                        batch_size=500,
                    )
        return len(to_create) + len(to_update)
