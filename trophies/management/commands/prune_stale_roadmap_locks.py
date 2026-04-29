"""Sweeper for abandoned roadmap edit locks.

Under the advisory lock model, soft-expired locks persist so the original
holder can resume cleanly when they return. After enough time has passed we
accept the holder isn't coming back. This command archives those long-stale
locks' branches as recovery revisions and deletes the locks. Intended to run
once a day from cron.

Default age threshold: 7 days since `last_heartbeat`. Use --days to override.
Use --dry-run to preview without changing state.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from trophies.models import RoadmapEditLock, RoadmapRevision
from trophies.services.roadmap_merge_service import archive_displaced_lock


class Command(BaseCommand):
    help = "Archive and delete roadmap edit locks idle longer than --days (default 7)."

    def add_arguments(self, parser):
        parser.add_argument(
            '--days', type=int, default=7,
            help='Minimum days since last_heartbeat to consider abandoned (default 7).',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='List candidates without archiving or deleting.',
        )

    def handle(self, *args, **options):
        days = options['days']
        dry_run = options['dry_run']
        cutoff = timezone.now() - timedelta(days=days)

        candidates = (
            RoadmapEditLock.objects
            .select_related('roadmap', 'holder')
            .filter(last_heartbeat__lt=cutoff)
            .order_by('last_heartbeat')
        )

        count = candidates.count()
        if count == 0:
            self.stdout.write(self.style.SUCCESS(
                f"No locks idle longer than {days} days. Nothing to do."
            ))
            return

        self.stdout.write(self.style.WARNING(
            f"Found {count} lock(s) idle longer than {days} days (cutoff: {cutoff:%Y-%m-%d %H:%M})."
        ))

        archived = 0
        for lock in candidates:
            holder = lock.holder.psn_username if lock.holder else '<deleted>'
            line = (
                f"  lock=#{lock.id} roadmap=#{lock.roadmap_id} "
                f"holder={holder} last_heartbeat={lock.last_heartbeat:%Y-%m-%d %H:%M}"
            )
            if dry_run:
                self.stdout.write(line + " [DRY RUN]")
                continue

            try:
                with transaction.atomic():
                    locked = RoadmapEditLock.objects.select_for_update().select_related(
                        'roadmap'
                    ).get(pk=lock.pk)
                    archive_displaced_lock(
                        locked,
                        actor=lock.holder,
                        action_type=RoadmapRevision.ACTION_AUTO_TAKEN_OVER,
                    )
                archived += 1
                self.stdout.write(line + " → archived")
            except RoadmapEditLock.DoesNotExist:
                self.stdout.write(line + " (already gone, skipping)")
            except Exception as e:
                self.stderr.write(self.style.ERROR(line + f" → ERROR: {e}"))

        if dry_run:
            self.stdout.write(self.style.WARNING(f"[DRY RUN] Would archive {count} lock(s)."))
        else:
            self.stdout.write(self.style.SUCCESS(f"Archived {archived} lock(s)."))
