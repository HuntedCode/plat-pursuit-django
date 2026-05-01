"""Site-wide reconcile of Profile.total_<type> counters.

Runs daily (see docs/guides/cron-jobs.md) as a drift-correction safety net for
the incremental signals that maintain Profile.total_bronzes / total_silvers /
total_golds / total_plats. Signals catch the common case (sync-time and admin
EarnedTrophy save / delete events). This command recomputes from scratch in
case any updates slipped past signals (bulk_update / queryset.update / signal
handler exceptions), so users never see drifted-up or drifted-down values.

Note: profile.total_trophies / total_unearned / avg_progress are intentionally
NOT recomputed here. Those are filter-respecting (hide_hiddens / hide_zeros)
and are recalculated on demand via update_profile_trophy_counts() — sync_complete
and the profile settings POST already call it.
"""
import time

from django.core.management.base import BaseCommand
from django.db.models import Count, Q

from trophies.models import Profile, EarnedTrophy


class Command(BaseCommand):
    help = (
        'Reconcile Profile.total_bronzes / silvers / golds / plats from '
        'EarnedTrophy ground truth. Drift-correction for the incremental signals.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Compute and report deltas without writing changes.',
        )
        parser.add_argument(
            '--chunk-size', type=int, default=200,
            help='Profiles processed per chunk.',
        )
        parser.add_argument(
            '--max-minutes', type=int, default=30,
            help='Wall-clock budget. Exits cleanly between chunks if exceeded.',
        )
        parser.add_argument(
            '--profile-ids', nargs='*', type=int, default=None,
            help='Optional subset of profile IDs to reconcile (for ad-hoc reruns).',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        chunk_size = options['chunk_size']
        max_seconds = options['max_minutes'] * 60
        explicit_ids = options['profile_ids']

        start = time.monotonic()
        deadline = start + max_seconds

        if explicit_ids:
            all_ids = sorted(set(explicit_ids))
        else:
            all_ids = list(Profile.objects.order_by('id').values_list('id', flat=True))

        total = len(all_ids)
        self.stdout.write(self.style.NOTICE(
            f'recalc_profile_counters starting: {total} profiles, chunk={chunk_size}, '
            f'budget={max_seconds // 60}min, dry_run={dry_run}'
        ))

        total_updated = 0
        chunks_processed = 0
        chunks_total = (total + chunk_size - 1) // chunk_size

        for chunk_start in range(0, total, chunk_size):
            if time.monotonic() >= deadline:
                self.stdout.write(self.style.WARNING(
                    f'Hit max-minutes budget after {chunks_processed}/{chunks_total} chunks. '
                    f'Remaining {chunks_total - chunks_processed} chunks deferred.'
                ))
                break

            chunk_ids = all_ids[chunk_start:chunk_start + chunk_size]
            updated = self._process_chunk(chunk_ids, dry_run)
            total_updated += updated
            chunks_processed += 1

            elapsed = time.monotonic() - start
            self.stdout.write(
                f'chunk {chunks_processed}/{chunks_total} '
                f'(profiles {chunk_start + 1}-{chunk_start + len(chunk_ids)}): '
                f'+{updated} profiles updated [{elapsed:.1f}s elapsed]'
            )

        elapsed_total = time.monotonic() - start
        verb = 'Would update' if dry_run else 'Updated'
        self.stdout.write(self.style.SUCCESS(
            f'recalc_profile_counters complete in {elapsed_total:.1f}s. '
            f'{verb} {total_updated} profiles across {chunks_processed} chunks.'
        ))

    def _process_chunk(self, profile_ids, dry_run):
        """Recompute one chunk of profiles. One GROUP BY query, one bulk_update."""
        # Single query: count earned trophies per (profile, trophy_type) for the chunk.
        rows = (
            EarnedTrophy.objects
            .filter(profile_id__in=profile_ids, earned=True)
            .values('profile_id')
            .annotate(
                bronze=Count('id', filter=Q(trophy__trophy_type='bronze')),
                silver=Count('id', filter=Q(trophy__trophy_type='silver')),
                gold=Count('id', filter=Q(trophy__trophy_type='gold')),
                platinum=Count('id', filter=Q(trophy__trophy_type='platinum')),
            )
        )
        new_counts = {row['profile_id']: row for row in rows}

        # Pull current stored values to detect changes.
        profiles = list(
            Profile.objects.filter(id__in=profile_ids).only(
                'id', 'total_bronzes', 'total_silvers', 'total_golds', 'total_plats'
            )
        )

        to_update = []
        for profile in profiles:
            row = new_counts.get(profile.id, {})
            new_bronze = row.get('bronze', 0)
            new_silver = row.get('silver', 0)
            new_gold = row.get('gold', 0)
            new_plat = row.get('platinum', 0)

            if (
                profile.total_bronzes != new_bronze
                or profile.total_silvers != new_silver
                or profile.total_golds != new_gold
                or profile.total_plats != new_plat
            ):
                profile.total_bronzes = new_bronze
                profile.total_silvers = new_silver
                profile.total_golds = new_gold
                profile.total_plats = new_plat
                to_update.append(profile)

        if to_update and not dry_run:
            Profile.objects.bulk_update(
                to_update,
                ['total_bronzes', 'total_silvers', 'total_golds', 'total_plats'],
            )

        return len(to_update)
