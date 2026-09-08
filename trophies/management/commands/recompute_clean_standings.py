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

from django.core.management.base import BaseCommand
from django.db.models import Count, Q
from django.utils import timezone

from trophies.models import (
    SHOVELWARE_FLAGGED_STATUSES, EarnedTrophy, Profile, ProfileTrophyStanding,
)


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
        chunk_size = options['chunk_size']
        deadline = time.monotonic() + options['max_minutes'] * 60
        start = time.monotonic()

        all_ids = options['profile_ids']
        if all_ids:
            all_ids = sorted(set(all_ids))
        else:
            all_ids = list(self._population())

        total = len(all_ids)
        chunks_total = (total + chunk_size - 1) // chunk_size
        self.stdout.write(self.style.NOTICE(
            f'recompute_clean_standings starting: {total} profiles, chunk={chunk_size}, '
            f'budget={options["max_minutes"]}min, dry_run={dry_run}'
        ))

        written = 0
        chunks_done = 0
        for chunk_start in range(0, total, chunk_size):
            if time.monotonic() >= deadline:
                self.stdout.write(self.style.WARNING(
                    f'Hit max-minutes budget after {chunks_done}/{chunks_total} chunks. '
                    f'{chunks_total - chunks_done} deferred to the next run.'
                ))
                break
            written += self._process_chunk(all_ids[chunk_start:chunk_start + chunk_size], dry_run)
            chunks_done += 1

        verb = 'Would write' if dry_run else 'Wrote'
        self.stdout.write(self.style.SUCCESS(
            f'recompute_clean_standings complete in {time.monotonic() - start:.1f}s. '
            f'{verb} {written} standing(s) across {chunks_done} chunk(s).'
        ))

    @staticmethod
    def _population():
        """Which profiles get a row: LINKED hunters, plus anyone who already has one.

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
        """
        return (
            Profile.objects.filter(is_linked=True).values('id')
            .union(ProfileTrophyStanding.objects.values('profile_id'))
            .values_list('id', flat=True)
        )

    def _process_chunk(self, profile_ids, dry_run):
        """One GROUP BY for the chunk's counts, one read of the profiles, one create + one update."""
        now = timezone.now()
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
            # The SUM OF THE TIERS, deliberately not a mirror of `Profile.total_trophies`. That figure is
            # filter-respecting (it honours the owner's hide_hiddens / hide_zeros display settings), and a
            # public board whose ordering moved when a hunter changed a private display preference would
            # be ranking on something no other reader can see.
            trophies = bronze + silver + gold + plat
            fields = {
                'clean_plats': plat, 'clean_trophies': trophies, 'clean_bronzes': bronze,
                'clean_silvers': silver, 'clean_golds': gold,
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
            if to_create:
                ProfileTrophyStanding.objects.bulk_create(to_create, batch_size=500)
            if to_update:
                ProfileTrophyStanding.objects.bulk_update(
                    to_update,
                    ['clean_plats', 'clean_trophies', 'clean_bronzes', 'clean_silvers', 'clean_golds',
                     'country_code', 'is_linked', 'updated_at'],
                    batch_size=500,
                )
        return len(to_create) + len(to_update)
