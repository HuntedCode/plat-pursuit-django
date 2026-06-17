"""Detect new DLC and refresh the affected badge series.

A new TrophyGroup appears (via sync `get_or_create`) when a game gains a trophy group.
If that game ALREADY existed before this scan window (it has a trophy group created at
or before the watermark), the new group is DLC -- which can drop earners below 100% and
must re-evaluate the whole badge series the game belongs to. For each affected series we
run the same full refresh as `refresh_badge_series` (re-check every earner + rebuild the
leaderboard) via the shared `badge_refresh_service`. The per-earner lapse behavior
(delete on main / maintenance on rebuild) is handled by `handle_badge`, so this command
is branch-agnostic and merges cleanly.

Run on a cron (sibling of `refresh_scouts`). A new game's first sync creates all its
groups at once with none predating the watermark, so it is correctly ignored as "not DLC".
"""
import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from trophies.models import Stage, TrophyGroup
from trophies.services.badge_refresh_service import refresh_badge_series_awards
from trophies.util_modules.cache import redis_client

logger = logging.getLogger('psn_api')

WATERMARK_KEY = 'dlc_detection:last_run'
DEFAULT_LOOKBACK = timedelta(days=3)  # used when the watermark is missing (Redis flush, first run)


class Command(BaseCommand):
    help = (
        "Detect games that gained new DLC (a new trophy group on an already-existing game) "
        "and refresh the affected badge series + leaderboards."
    )

    def add_arguments(self, parser):
        parser.add_argument('--since', type=str, help='Override the watermark (ISO datetime).')
        parser.add_argument('--dry-run', action='store_true', help='Report affected series without refreshing or advancing the watermark.')

    def handle(self, *args, **options):
        now = timezone.now()
        watermark = self._resolve_watermark(options.get('since'), now)
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"DLC scan: trophy groups created after {watermark.isoformat()}"
        ))

        affected = set()
        scanned = dlc_groups = 0
        new_groups = TrophyGroup.objects.filter(created_at__gt=watermark).select_related('game__concept')
        for tg in new_groups:
            scanned += 1
            concept = tg.game.concept
            if concept is None:
                continue
            # DLC = the game predates this window (a group exists from at/before the watermark).
            # A brand-new game's groups are all created together with none predating it -> skipped.
            if not TrophyGroup.objects.filter(game_id=tg.game_id, created_at__lte=watermark).exists():
                continue
            dlc_groups += 1
            for slug in Stage.objects.filter(concepts=concept).values_list('series_slug', flat=True).distinct():
                if slug:
                    affected.add(slug)

        self.stdout.write(
            f"Scanned {scanned} new trophy group(s); {dlc_groups} are DLC on existing games; "
            f"{len(affected)} affected badge series."
        )

        if options.get('dry_run'):
            for slug in sorted(affected):
                self.stdout.write(f"  would refresh: {slug}")
            self.stdout.write(self.style.WARNING("Dry run -- no refresh, watermark unchanged."))
            return

        for slug in sorted(affected):
            try:
                # Automated DLC re-evaluation: stay silent (matches the prior behavior,
                # where most badges sent no Discord; new DLC mostly lapses badges anyway).
                processed, changed, earners, _progress = refresh_badge_series_awards(
                    slug, skip_notifications=True,
                )
                self.stdout.write(self.style.SUCCESS(
                    f"  refreshed '{slug}': {processed} pairs, {changed} profiles changed, {earners} earners"
                ))
            except Exception:
                logger.exception("detect_dlc_and_refresh: refresh failed for series %s", slug)
                self.stdout.write(self.style.ERROR(f"  FAILED '{slug}' (see logs)"))

        # Advance the watermark only after a full pass, so a crash re-scans the same window.
        self._set_watermark(now)
        self.stdout.write(self.style.SUCCESS(f"DLC scan complete. Watermark -> {now.isoformat()}"))

    def _resolve_watermark(self, since_opt, now):
        if since_opt:
            parsed = parse_datetime(since_opt)
            if parsed:
                return parsed if timezone.is_aware(parsed) else timezone.make_aware(parsed)
            self.stdout.write(self.style.WARNING(f"Could not parse --since '{since_opt}'; using stored watermark."))
        raw = None
        try:
            raw = redis_client.get(WATERMARK_KEY)
        except Exception:
            logger.warning("detect_dlc_and_refresh: redis unavailable for watermark read")
        if raw:
            parsed = parse_datetime(raw.decode() if isinstance(raw, bytes) else raw)
            if parsed:
                return parsed
        return now - DEFAULT_LOOKBACK

    def _set_watermark(self, when):
        try:
            redis_client.set(WATERMARK_KEY, when.isoformat())
        except Exception:
            logger.warning("detect_dlc_and_refresh: redis unavailable for watermark write")
