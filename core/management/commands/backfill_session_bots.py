"""
One-time backfill: classify historical AnalyticsSession rows as is_bot=True/False
based on the User-Agent regex in core.services.bot_detection.

After the migration adds the field with default=False, all historical sessions
are False. This command sweeps the table and flips the bots. Going forward,
new sessions are classified at creation by AnalyticsSessionMiddleware.

Usage:
  python manage.py backfill_session_bots [--dry-run] [--batch-size 5000]
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import AnalyticsSession
from core.services.bot_detection import is_bot_user_agent


class Command(BaseCommand):
    help = "Backfill AnalyticsSession.is_bot from User-Agent strings."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Count rows that would be flipped without writing.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=5000,
            help="Number of session_ids per UPDATE batch (default: 5000).",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        batch_size = options["batch_size"]

        if dry_run:
            self.stdout.write(self.style.WARNING("--- DRY RUN MODE ---\n"))

        total = AnalyticsSession.objects.count()
        self.stdout.write(f"Scanning {total:,} sessions...")

        # Iterate is_bot=False rows only — already-flagged bots from prior
        # backfill runs (or the new middleware path) are skipped. Cheap with
        # the new index. Using values_list keeps memory bounded.
        candidate_qs = (
            AnalyticsSession.objects
            .filter(is_bot=False)
            .values_list("session_id", "user_agent")
        )

        bot_ids_pending = []
        scanned = 0
        flipped = 0

        for session_id, user_agent in candidate_qs.iterator(chunk_size=10000):
            scanned += 1
            if is_bot_user_agent(user_agent):
                bot_ids_pending.append(session_id)

            if len(bot_ids_pending) >= batch_size:
                flipped += self._flip_batch(bot_ids_pending, dry_run=dry_run)
                bot_ids_pending = []
                self.stdout.write(
                    f"  scanned={scanned:,}/{total:,}  bots_flipped={flipped:,}"
                )

        # Flush the tail
        if bot_ids_pending:
            flipped += self._flip_batch(bot_ids_pending, dry_run=dry_run)

        verdict = "Would flip" if dry_run else "Flipped"
        bot_pct = (flipped / scanned * 100) if scanned else 0
        self.stdout.write(self.style.SUCCESS(
            f"\nDone. Scanned {scanned:,} candidate session(s). "
            f"{verdict} {flipped:,} ({bot_pct:.1f}%) to is_bot=True."
        ))

    def _flip_batch(self, session_ids, dry_run):
        if dry_run:
            return len(session_ids)
        with transaction.atomic():
            return AnalyticsSession.objects.filter(
                session_id__in=session_ids
            ).update(is_bot=True)
