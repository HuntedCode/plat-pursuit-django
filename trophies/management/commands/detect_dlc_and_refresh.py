"""Detect new DLC and refresh the affected badge series + owner completion percentages.

A new TrophyGroup appears (via sync `get_or_create`) when a game gains a trophy group.
If that game ALREADY existed before this scan window (it has a trophy group created at
or before the watermark), the new group is DLC -- which can drop earners below 100% and
must re-evaluate the whole badge series the game belongs to. For each affected series we
run the same full refresh as `refresh_badge_series` (re-check every earner + rebuild the
leaderboard) via the shared `badge_refresh_service`. The per-earner lapse behavior
(delete on main / maintenance on rebuild) is handled by `handle_badge`, so this command
is branch-agnostic and merges cleanly.

DLC also grows the game's trophy TOTAL, so every owner's stored `ProfileGame.progress`
(a PSN-reported, grade-weighted %) is left overstated until they re-sync -- and inactive
owners may never correct, showing a false "100% complete". We therefore recompute every
owner's completion for the affected games here, count-based from the already-denormed
`earned_trophies_count / new total`. That is a stopgap approximation (PSN weights by trophy
grade) but is EXACT at the 100%->below boundary -- the visible bug -- since new DLC trophies
are unearned by all, so only the denominator moved; PSN restores the exact weighted value on
each owner's next sync. NOT a blanket historical backfill (that would overwrite accurate
just-synced values); it fires only for games detected as gaining DLC in this window.

Run on a cron (sibling of `refresh_scouts`). A new game's first sync creates all its
groups at once with none predating the watermark, so it is correctly ignored as "not DLC".
"""
import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import F
from django.db.models.functions import Round
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from trophies.models import Stage, TrophyGroup, Game, ProfileGame
from trophies.services.badge_refresh_service import refresh_badge_series_awards
from trophies.util_modules.cache import redis_client

logger = logging.getLogger('psn_api')

WATERMARK_KEY = 'dlc_detection:last_run'
DEFAULT_LOOKBACK = timedelta(days=3)  # used when the watermark is missing (Redis flush, first run)


class Command(BaseCommand):
    help = (
        "Detect games that gained new DLC (a new trophy group on an already-existing game) "
        "and refresh the affected badge series + leaderboards + owner completion percentages."
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
        affected_game_ids = set()
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
            affected_game_ids.add(tg.game_id)   # its owners' completion % is now stale (denominator grew)
            for slug in Stage.objects.filter(concepts=concept).values_list('series_slug', flat=True).distinct():
                if slug:
                    affected.add(slug)

        self.stdout.write(
            f"Scanned {scanned} new trophy group(s); {dlc_groups} are DLC on existing games; "
            f"{len(affected)} affected badge series; {len(affected_game_ids)} games to recompute completion."
        )

        if options.get('dry_run'):
            for slug in sorted(affected):
                self.stdout.write(f"  would refresh: {slug}")
            self.stdout.write(f"  would recompute owner completion for {len(affected_game_ids)} game(s).")
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

        # Recompute owner completion for the games that gained DLC (the trophy total grew).
        rows = self._recompute_completion(affected_game_ids)
        if affected_game_ids:
            self.stdout.write(self.style.SUCCESS(
                f"Recomputed completion on {rows} ProfileGame row(s) across {len(affected_game_ids)} game(s)."
            ))

        # Advance the watermark only after a full pass, so a crash re-scans the same window.
        self._set_watermark(now)
        self.stdout.write(self.style.SUCCESS(f"DLC scan complete. Watermark -> {now.isoformat()}"))

    def _recompute_completion(self, game_ids):
        """Recompute ProfileGame.progress for games that gained DLC, so owners' completion % isn't left
        overstated until they re-sync. Count-based (earned_trophies_count / new total) -- exact at the
        100%->below boundary; PSN restores the exact grade-weighted value on each owner's next sync. One
        bounded DB-side UPDATE per game (no per-row iteration, no EarnedTrophy touch) -> whale-safe."""
        total_rows = 0
        for gid in game_ids:
            game = Game.objects.filter(pk=gid).first()
            if game is None:
                continue
            try:
                total = game.get_total_defined_trophies()
            except (KeyError, TypeError):
                total = 0   # malformed/empty defined_trophies -> can't divide; skip
            if not total:   # no defined trophies -> nothing to divide by; skip
                continue
            try:
                total_rows += ProfileGame.objects.filter(game_id=gid).update(
                    progress=Round(F('earned_trophies_count') * 100.0 / total)
                )
            except Exception:
                logger.exception("detect_dlc_and_refresh: completion recompute failed for game %s", gid)
        return total_rows

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
