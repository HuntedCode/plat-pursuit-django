"""
Compute and post the daily community trophy tracker to Discord.

Scheduled to run twice via Render cron (16:30 UTC + 17:30 UTC) to handle
DST without DST-aware logic. Whichever fires first at the right ET time
succeeds; the second is a no-op via the `posted_at` idempotency gate.

Webhook posting uses a SYNCHRONOUS direct POST (not the trophies queue/
worker). Reason: this is a one-shot management command. The queue's
daemon worker thread dies abruptly when the parent process exits, which
can drop messages mid-flight before the HTTP request lands. Direct POST
also surfaces HTTP errors as CommandError instead of swallowing them in
the worker's logger, so cron failures are visible in Render's UI.
"""
import json
import logging
from datetime import date as date_cls, datetime, timedelta

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from core.models import CommunityTrophyDay
from core.services.community_trophy_tracker import (
    ET,
    build_embed_payload,
    compute_day_stats,
    eligible_profile_count,
    get_current_records,
)
from trophies.discord_utils.discord_notifications import PROXIES

logger = logging.getLogger(__name__)

# Fake numbers used by --test-data. Realistic-ish "great day" totals so the
# preview shows comma-formatting behavior on multi-digit values.
TEST_FAKE_STATS = {
    'total_trophies': 12847,
    'total_platinums': 234,
    'total_ultra_rares': 187,
}
TEST_FAKE_PP_SCORE = (
    TEST_FAKE_STATS['total_trophies']
    + 5 * TEST_FAKE_STATS['total_platinums']
    + 3 * TEST_FAKE_STATS['total_ultra_rares']
)


class Command(BaseCommand):
    help = "Compute and post yesterday's community trophy tracker to Discord."

    def add_arguments(self, parser):
        parser.add_argument(
            '--date',
            help='Target ET date in YYYY-MM-DD. Defaults to yesterday in ET.',
        )
        parser.add_argument(
            '--force-repost',
            action='store_true',
            help='Re-post even if posted_at is already set on the row.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Compute and print embed JSON; do not write to DB or post.',
        )
        parser.add_argument(
            '--test-data',
            action='store_true',
            help=(
                'Send a fake-data preview embed to DISCORD_TEST_WEBHOOK_URL so you can '
                'eyeball the format. Does not touch the DB. Refuses to run if the test '
                'webhook is not configured (use --use-platinum-webhook to override).'
            ),
        )
        parser.add_argument(
            '--test-scenario',
            choices=['record', 'normal'],
            default='record',
            help='Only with --test-data. "record" flags every stat as NEW RECORD (gold embed). "normal" suppresses the badges (blue embed). Default: record.',
        )
        parser.add_argument(
            '--use-platinum-webhook',
            action='store_true',
            help='With --test-data: post the preview to DISCORD_PLATINUM_WEBHOOK_URL instead of the test channel. Use sparingly.',
        )

    def handle(self, *args, **opts):
        if opts['test_data']:
            self._handle_test_data(opts)
            return

        target_date = self._resolve_target_date(opts.get('date'))
        self.stdout.write(f"Target ET date: {target_date}")

        if opts['dry_run']:
            stats = compute_day_stats(target_date)
            transient = CommunityTrophyDay(date=target_date, **stats)
            prior = get_current_records()  # don't exclude (no row exists)
            payload = build_embed_payload(transient, prior_records=prior)
            self.stdout.write("--- DRY RUN: stats ---")
            self.stdout.write(json.dumps(stats, indent=2))
            self.stdout.write("--- DRY RUN: embed payload ---")
            self.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False))
            return

        with transaction.atomic():
            day, created = (
                CommunityTrophyDay.objects
                .select_for_update()
                .get_or_create(
                    date=target_date,
                    defaults={'eligible_profile_count': eligible_profile_count()},
                )
            )

            if not created and day.posted_at and not opts['force_repost']:
                self.stdout.write(self.style.WARNING(
                    f"Already posted for {target_date} at {day.posted_at}. Skipping."
                ))
                return

            stats = compute_day_stats(target_date)
            day.total_trophies = stats['total_trophies']
            day.total_platinums = stats['total_platinums']
            day.total_ultra_rares = stats['total_ultra_rares']
            day.pp_score = stats['pp_score']
            day.save()

        prior_records = get_current_records(exclude_pk=day.pk)
        payload = build_embed_payload(day, prior_records=prior_records)

        self._post_webhook_direct(
            payload,
            settings.DISCORD_PLATINUM_WEBHOOK_URL,
            error_label="Community trophy tracker webhook",
        )

        # posted_at is set ONLY after a confirmed 2xx response, so a failed
        # POST leaves the row available for retry on the next run.
        day.posted_at = timezone.now()
        day.save(update_fields=['posted_at'])
        self.stdout.write(self.style.SUCCESS(
            f"Posted tracker for {target_date} "
            f"(T={day.total_trophies:,} P={day.total_platinums:,} "
            f"UR={day.total_ultra_rares:,} PP={day.pp_score:,})"
        ))

    def _resolve_target_date(self, raw: str | None) -> date_cls:
        if raw:
            try:
                return datetime.strptime(raw, '%Y-%m-%d').date()
            except ValueError:
                raise CommandError(f"Invalid --date value: {raw!r} (expected YYYY-MM-DD)")
        # Default: yesterday in ET, regardless of UTC date
        today_et = timezone.now().astimezone(ET).date()
        return today_et - timedelta(days=1)

    def _handle_test_data(self, opts):
        """Build a fake CommunityTrophyDay (transient, not saved) and post a preview.

        Routes to DISCORD_TEST_WEBHOOK_URL by default. Refuses to send if that
        env var is empty unless --use-platinum-webhook is set.
        """
        target_date = self._resolve_target_date(opts.get('date'))
        scenario = opts['test_scenario']

        transient = CommunityTrophyDay(
            date=target_date,
            total_trophies=TEST_FAKE_STATS['total_trophies'],
            total_platinums=TEST_FAKE_STATS['total_platinums'],
            total_ultra_rares=TEST_FAKE_STATS['total_ultra_rares'],
            pp_score=TEST_FAKE_PP_SCORE,
        )

        if scenario == 'record':
            # Empty prior_records => every stat is flagged NEW RECORD.
            prior_records = {}
        else:
            # Prior records that all beat the fake numbers => no NEW RECORD badges.
            prior_records = {
                'max_trophies': TEST_FAKE_STATS['total_trophies'] + 1000,
                'max_platinums': TEST_FAKE_STATS['total_platinums'] + 50,
                'max_ultra_rares': TEST_FAKE_STATS['total_ultra_rares'] + 50,
                'max_pp_score': TEST_FAKE_PP_SCORE + 5000,
            }

        payload = build_embed_payload(transient, prior_records=prior_records)

        if opts['use_platinum_webhook']:
            webhook_url = settings.DISCORD_PLATINUM_WEBHOOK_URL
            channel_label = "DISCORD_PLATINUM_WEBHOOK_URL (live!)"
        else:
            webhook_url = getattr(settings, 'DISCORD_TEST_WEBHOOK_URL', None)
            channel_label = "DISCORD_TEST_WEBHOOK_URL"
            if not webhook_url:
                raise CommandError(
                    "DISCORD_TEST_WEBHOOK_URL is not set. Either configure it in your "
                    ".env, or pass --use-platinum-webhook to send the preview to the "
                    "live channel (NOT recommended)."
                )

        self.stdout.write(self.style.WARNING(
            f"TEST MODE: scenario={scenario}, target_date={target_date}, "
            f"channel={channel_label}"
        ))
        self.stdout.write("--- TEST PREVIEW: embed payload ---")
        self.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False))

        response = self._post_webhook_direct(
            payload, webhook_url, error_label="Test webhook"
        )
        self.stdout.write(self.style.SUCCESS(
            f"Test preview delivered ({response.status_code}). Check the test channel."
        ))

    def _post_webhook_direct(self, payload, webhook_url, *, error_label="Webhook"):
        """Synchronous direct POST to a Discord webhook URL.

        Used by both the production daily post and the --test-data preview.
        See the module docstring for why this bypasses the trophies queue/
        worker pattern. Raises CommandError on transport failure or any 4xx/5xx
        response so cron jobs report the failure and `posted_at` is not
        flipped on a row whose post never landed.
        """
        try:
            response = requests.post(webhook_url, json=payload, proxies=PROXIES, timeout=10)
        except requests.RequestException as e:
            logger.exception(f"{error_label} direct POST raised")
            raise CommandError(f"{error_label} POST failed: {e}")

        if response.status_code >= 400:
            raise CommandError(
                f"{error_label} returned HTTP {response.status_code}: {response.text[:500]}"
            )
        return response
