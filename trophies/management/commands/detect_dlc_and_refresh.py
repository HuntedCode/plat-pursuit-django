"""Detect new DLC and refresh the affected badge series + owner completion percentages.

A new TrophyGroup appears (via sync `get_or_create`) when a game gains a trophy group.
If that game ALREADY existed before this scan window (it has a trophy group created at
or before the watermark), the new group is DLC -- which can drop earners below 100% and
must re-evaluate the whole badge series the game belongs to. For each affected series we
re-evaluate every profile that has played a game in it, across every LIVE edition, via
`badge_apply.evaluate_and_apply_batch`. Awards and revokes both fall out of that: DLC can
newly qualify a hunter as easily as it lapses one.

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

from django.core.management.base import BaseCommand, CommandError
from django.db.models import F, Q, Value
from django.db.models.functions import Least, Round
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from trophies.models import Stage, TrophyGroup, Game, GroupBadge, Profile, ProfileGame
from trophies.services.badge_apply import evaluate_and_apply_batch
from trophies.util_modules.cache import redis_client

logger = logging.getLogger('psn_api')

WATERMARK_KEY = 'dlc_detection:last_run'
DEFAULT_LOOKBACK = timedelta(days=3)  # used when the watermark is missing (Redis flush, first run)
#: Hard ceiling on how far back a run will scan, however old the stored watermark is. Without it a
#: persistently failing series holds the watermark forever and the window grows by a day every night:
#: the scan re-walks it all, every affected series is re-swept over everyone who played it, and
#: `_recompute_completion` becomes the blanket historical backfill its own docstring forbids,
#: overwriting PSN's exact grade-weighted progress with the count-based approximation nightly.
MAX_LOOKBACK = timedelta(days=14)
#: Series that failed their refresh, retried on the next run REGARDLESS of the watermark. This is
#: what lets the watermark keep advancing: the retry rides an explicit list instead of a held window.
RETRY_KEY = 'dlc_detection:retry_series'


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
            # BOTH qualifier paths. A concept is either a direct stage member OR a ConceptBundle member
            # on that stage, never both -- so matching `concepts` alone misses every bundled game, and an
            # episodic series would never be flagged as affected no matter how much DLC it gained.
            for slug in (
                Stage.objects
                .filter(Q(concepts=concept) | Q(concept_bundles__concepts=concept))
                .values_list('series_slug', flat=True).distinct()
            ):
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

        # Series that failed a previous run come back regardless of what the window turned up.
        affected |= self._take_retries()

        failed = []
        for slug in sorted(affected):
            try:
                # Every LIVE edition of the series: DLC lands on a game, and a game can gate more than one
                # edition, so scoping to a single group badge would leave the others stale.
                badges = list(
                    GroupBadge.objects.filter(is_live=True, series__series_slug=slug)
                    .select_related('series', 'platform_group')
                )
                if not badges:
                    self.stdout.write(f"  skipped '{slug}': no live badges")
                    continue

                # Everyone who has played a game in the series. Not just current holders: DLC can newly
                # QUALIFY someone as easily as it lapses someone, and a holders-only scan would only ever
                # take badges away.
                #
                # Both qualifier paths again -- the legacy query matched `stages` only, so a hunter whose
                # single game in the series was a bundle member was never re-evaluated. Inheriting that
                # verbatim is not parity worth keeping.
                #
                # `.only('id')` because the batch uses nothing else: Profile is 48 fields including three
                # JSONFields and a TextField, and a broad franchise series approaches the whole userbase.
                profiles = Profile.objects.filter(
                    Q(played_games__game__concept__stages__series_slug=slug)
                    | Q(played_games__game__concept__bundles__stage__series_slug=slug)
                ).distinct().only('id', 'country_code')

                # `evaluate_and_apply_batch` is silent by construction (no notify parameter), which is the
                # behaviour this call site wants: an automated DLC sweep should not ping hunters about
                # badges they effectively already held. It also builds the catalog ONCE for the whole
                # batch rather than per profile.
                totals = evaluate_and_apply_batch(profiles, badges)
                self.stdout.write(self.style.SUCCESS(
                    f"  refreshed '{slug}': {len(badges)} edition(s), "
                    f"{totals['awarded']} awarded, {totals['revoked']} revoked, {totals['updated']} updated"
                ))
            except Exception:
                logger.exception("detect_dlc_and_refresh: refresh failed for series %s", slug)
                self.stdout.write(self.style.ERROR(f"  FAILED '{slug}' (see logs)"))
                failed.append(slug)

        # Recompute owner completion for the games that gained DLC (the trophy total grew).
        rows = self._recompute_completion(affected_game_ids)
        if affected_game_ids:
            self.stdout.write(self.style.SUCCESS(
                f"Recomputed completion on {rows} ProfileGame row(s) across {len(affected_game_ids)} game(s)."
            ))

        # Advance the watermark only after a full pass, so a crash re-scans the same window.
        #
        # A per-series failure is not a crash, which is the hole this closes. The loop above catches per
        # series and carries on, so the watermark used to advance regardless -- and because the next run
        # only scans groups created AFTER it, a series that raised was never swept again. Its owners kept
        # a false "100% complete" permanently, with the only evidence one ERROR line in a nightly log.
        # Holding the watermark means the next run re-scans the same window and retries; raising makes
        # `nightly` mark the step failed and exit non-zero, so a persistent failure surfaces as a red run
        # rather than as a window that quietly grows.
        # The watermark ADVANCES either way, and failures are retried by name. Holding it was the
        # first attempt and it is a runaway: the window grows a day per night forever, re-sweeping
        # every affected series over everyone who played it, and turning `_recompute_completion` into
        # the blanket backfill it must not be. Retrying an explicit list preserves the point (nothing
        # is dropped) without letting the window and the completion rewrite grow without bound.
        self._set_watermark(now)

        if failed:
            self._queue_retries(failed)
            self.stdout.write(self.style.ERROR(
                f"{len(failed)} series failed ({', '.join(failed)}); queued for retry on the next run. "
                f"Watermark -> {now.isoformat()}"
            ))
            raise CommandError(f"detect_dlc_and_refresh: {len(failed)} series failed: {', '.join(failed)}")

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
                # Clamp to 100: earned <= total for the DLC case (only the denominator grew), but a stale
                # earned_trophies_count vs a shrunk defined set could otherwise write progress > 100.
                total_rows += ProfileGame.objects.filter(game_id=gid).update(
                    progress=Least(Value(100), Round(F('earned_trophies_count') * 100.0 / total))
                )
            except Exception:
                # A swallowed per-game failure is NOT retried (the watermark still advances); the owner's
                # value self-heals on their next PSN sync, matching the badge-refresh loop's behavior.
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
                # Clamped: a stored watermark older than MAX_LOOKBACK means something has been wrong
                # for a while, and re-walking an ever-growing window every night makes it worse rather
                # than better. Failed series are retried through RETRY_KEY instead.
                return max(parsed, now - MAX_LOOKBACK)
        return now - DEFAULT_LOOKBACK

    @staticmethod
    def _take_retries():
        """Series queued by a previous failed run. Read AND cleared: a series that fails again is
        re-queued by `_queue_retries`, so the set never grows unless failures keep happening."""
        try:
            raw = redis_client.spop(RETRY_KEY, 500) or []
        except Exception:
            logger.warning("detect_dlc_and_refresh: redis unavailable for retry read")
            return set()
        return {r.decode() if isinstance(r, bytes) else r for r in raw}

    @staticmethod
    def _queue_retries(slugs):
        try:
            redis_client.sadd(RETRY_KEY, *slugs)
        except Exception:
            logger.warning("detect_dlc_and_refresh: redis unavailable for retry write")

    def _set_watermark(self, when):
        try:
            redis_client.set(WATERMARK_KEY, when.isoformat())
        except Exception:
            logger.warning("detect_dlc_and_refresh: redis unavailable for watermark write")
