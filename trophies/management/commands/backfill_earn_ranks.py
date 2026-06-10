"""Backfill UserBadge.earn_rank for badges with unranked earners.

earn_rank (the Nth profile to earn a badge tier) is stamped at award time, so
badges earned before that logic existed have NULL ranks and show no engraving.
This assigns earn_rank = 1..N by earned_at order (id as a stable tie-break) for
every badge that has at least one unranked earner. Idempotent; safe to re-run.
"""

from django.core.management.base import BaseCommand

from trophies.models import UserBadge


class Command(BaseCommand):
    help = "Backfill UserBadge.earn_rank by earned_at order for badges with unranked earners."

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Report what would change without writing.')

    def handle(self, *args, **options):
        dry = options['dry_run']

        badge_ids = list(
            UserBadge.objects.filter(earn_rank__isnull=True)
            .values_list('badge_id', flat=True).distinct()
        )

        badges = 0
        changed = 0
        for badge_id in badge_ids:
            earners = list(
                UserBadge.objects.filter(badge_id=badge_id).order_by('earned_at', 'id')
            )
            to_update = []
            for rank, ub in enumerate(earners, start=1):
                if ub.earn_rank != rank:
                    ub.earn_rank = rank
                    to_update.append(ub)
            if to_update and not dry:
                UserBadge.objects.bulk_update(to_update, ['earn_rank'])
            badges += 1
            changed += len(to_update)

        self.stdout.write(self.style.SUCCESS(
            f"{'[dry-run] ' if dry else ''}Set earn_rank on {changed} UserBadge row(s) "
            f"across {badges} badge(s)."
        ))
