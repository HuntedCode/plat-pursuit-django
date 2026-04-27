"""
Sweep recent AnalyticsSession rows and flag UA-spoofing bots that survived
the regex classifier.

Three behavioral rules (defined in core.services.bot_behavioral):
- rule1_no_ref_bounce: anonymous + no referrer + page_count <= 1
- rule2_ip_burst: same IP, > N sessions, < M distinct UAs in window
- rule3_ua_spoofer: UA seen in > X anonymous + no-ref + bounced sessions

Usage:
    # Default sweep (last 24 hours, with 30-min buffer)
    python manage.py flag_behavioral_bots

    # Initial backfill of historical data
    python manage.py flag_behavioral_bots --lookback-hours 720 --dry-run
    python manage.py flag_behavioral_bots --lookback-hours 720

    # Hourly cron: run with default lookback, no dry-run
    python manage.py flag_behavioral_bots --lookback-hours 2

Sessions younger than 30 minutes are always skipped — page_count isn't final
until the session times out, so flagging in-flight sessions is unsafe.
"""
from django.core.management.base import BaseCommand

from core.services.bot_behavioral import run_behavioral_classification


class Command(BaseCommand):
    help = "Flag UA-spoofing bots in AnalyticsSession via three behavioral rules."

    def add_arguments(self, parser):
        parser.add_argument(
            "--lookback-hours",
            type=int,
            default=24,
            help="How far back to scan for sessions to evaluate (default: 24).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Count sessions that would be flagged without writing.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=5000,
            help="Number of session_ids per UPDATE batch (default: 5000).",
        )

    def handle(self, *args, **options):
        lookback_hours = options["lookback_hours"]
        dry_run = options["dry_run"]
        batch_size = options["batch_size"]

        if dry_run:
            self.stdout.write(self.style.WARNING("--- DRY RUN MODE ---\n"))

        self.stdout.write(
            f"Scanning sessions created in the last {lookback_hours}h "
            f"(skipping anything younger than 30 minutes)..."
        )

        counts = run_behavioral_classification(
            lookback_hours=lookback_hours,
            dry_run=dry_run,
            batch_size=batch_size,
        )

        verb = "Would flag" if dry_run else "Flagged"
        self.stdout.write("")
        for rule_name, count in counts.items():
            self.stdout.write(f"  {rule_name:24s}  {verb} {count:,}")

        total = sum(counts.values())
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"Done. {verb} {total:,} session(s) total."
        ))
