"""Site-wide recompute of Trophy.earned_count / Trophy.earn_rate / Game.played_count and the game's
denormalized community stats (plats_earned_count / full_completion_count / avg_completion /
monthly_players_count / total_earns_count).

Runs as a daily cron (see docs/guides/cron-jobs.md). Replaces the per-profile
inline recompute that used to live in `psn_api_service.update_profilegame_stats`
Phase 2, which was firing once per profile sync_complete and turning the DB CPU
graph into a wall every time multiple profiles finished syncing in the same
window. Decoupling that work into a single batched daily run was the structural
fix for the May 2026 web-server OOM crashes.

Step 2 of the broader denormalization plan adds incremental signal-driven
updates so the counters stay live between cron runs; this command then becomes
purely a reconcile / drift-correction safety net rather than the source of
truth.
"""
import logging
import time
from collections import defaultdict
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import connection
from django.db.models import Count, Q, Avg
from django.utils import timezone

from trophies.models import Game, Trophy, ProfileGame, EarnedTrophy
from trophies.util_modules.cache import redis_client

logger = logging.getLogger(__name__)

# Window for Game.monthly_players_count. Matches the "active players" stat the
# game-detail header has always shown; it just used to be computed per request.
MONTHLY_PLAYER_WINDOW = timedelta(days=30)

# Resume cursor: the highest Game id fully processed by the previous run. Without it, a
# run that hits --max-minutes restarts at id 0 every night, so the tail of the catalogue
# past the budget cutoff is NEVER reached. That was survivable when the game-detail page
# recomputed these stats live on a cache miss (stale meant slow, not wrong); now that the
# page reads the columns directly, an unreached game would serve `0` forever. Storing the
# cursor makes consecutive budget-capped runs sweep the catalogue round-robin instead.
# Same Redis-watermark shape as detect_dlc_and_refresh.
CURSOR_KEY = 'recalc_earn_rates:cursor'

# This command is a batch job whose per-chunk aggregates legitimately outrun any timeout
# appropriate for a web request. It is also documented to be run by hand after deploy --
# from a shell that inherits the WEB service environment, where DB_STATEMENT_TIMEOUT_MS is
# deliberately 15s. Widening it on this connection means the command behaves the same no
# matter which service's shell launches it.
STATEMENT_TIMEOUT_MS = 600_000  # 10 minutes per statement


class Command(BaseCommand):
    help = (
        'Recompute played_count + community stats (plats_earned_count, '
        'full_completion_count, avg_completion, monthly_players_count, total_earns_count) '
        'on Games, and earned_count + earn_rate on Trophies, using bulk GROUP BY '
        'aggregates. Designed for daily cron use.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Compute and report deltas without writing changes.',
        )
        parser.add_argument(
            '--chunk-size', type=int, default=200,
            help='Games processed per chunk. Each chunk runs three bulk queries.',
        )
        parser.add_argument(
            '--max-minutes', type=int, default=30,
            help='Wall-clock budget. Exits cleanly between chunks if exceeded.',
        )
        parser.add_argument(
            '--game-ids', nargs='*', type=int, default=None,
            help='Optional subset of game IDs to recompute (for ad-hoc reruns).',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        chunk_size = options['chunk_size']
        max_seconds = options['max_minutes'] * 60
        explicit_ids = options['game_ids']

        start = time.monotonic()
        deadline = start + max_seconds

        self._widen_statement_timeout()

        # An explicit --game-ids run is a targeted ad-hoc pass: it neither reads nor
        # advances the cursor, so it can't perturb the nightly sweep's position.
        resumed_from = None
        if explicit_ids:
            all_game_ids = sorted(set(explicit_ids))
        else:
            all_game_ids = list(Game.objects.order_by('id').values_list('id', flat=True))
            resumed_from = self._get_cursor()
            if resumed_from is not None:
                # Rotate so the run starts just past where the last one stopped, wrapping
                # around the end. Every game is still visited once per full pass; only the
                # starting point moves, so a permanently budget-capped job still reaches
                # the whole catalogue over consecutive runs.
                split = next(
                    (i for i, gid in enumerate(all_game_ids) if gid > resumed_from),
                    len(all_game_ids),
                )
                if split >= len(all_game_ids):
                    resumed_from = None   # cursor past the end -> start a fresh pass
                else:
                    all_game_ids = all_game_ids[split:] + all_game_ids[:split]

        total_games = len(all_game_ids)
        self.stdout.write(self.style.NOTICE(
            f'recalc_earn_rates starting: {total_games} games, chunk={chunk_size}, '
            f'budget={max_seconds // 60}min, dry_run={dry_run}'
            + (f', resuming after game id {resumed_from}' if resumed_from is not None else '')
        ))

        total_games_updated = 0
        total_trophies_updated = 0
        chunks_processed = 0
        chunks_total = (total_games + chunk_size - 1) // chunk_size

        last_done_id = None
        for chunk_start in range(0, total_games, chunk_size):
            if time.monotonic() >= deadline:
                self.stdout.write(self.style.WARNING(
                    f'Hit max-minutes budget after {chunks_processed}/{chunks_total} chunks. '
                    f'Remaining {chunks_total - chunks_processed} chunks resume from game id '
                    f'{last_done_id} on the next run.'
                ))
                break

            chunk_ids = all_game_ids[chunk_start:chunk_start + chunk_size]
            games_updated, trophies_updated = self._process_chunk(chunk_ids, dry_run)
            total_games_updated += games_updated
            total_trophies_updated += trophies_updated
            chunks_processed += 1
            last_done_id = chunk_ids[-1]

            elapsed = time.monotonic() - start
            self.stdout.write(
                f'chunk {chunks_processed}/{chunks_total} '
                f'(games {chunk_start + 1}-{chunk_start + len(chunk_ids)}): '
                f'+{games_updated} games, +{trophies_updated} trophies '
                f'[{elapsed:.1f}s elapsed]'
            )

        # Advance the cursor only for a full-catalogue, non-dry run. A completed pass
        # clears it so the next run starts from the top with a fresh ordering.
        if not dry_run and not explicit_ids:
            completed_pass = chunks_processed == chunks_total
            self._set_cursor(None if completed_pass else last_done_id)

        elapsed_total = time.monotonic() - start
        verb = 'Would update' if dry_run else 'Updated'
        self.stdout.write(self.style.SUCCESS(
            f'recalc_earn_rates complete in {elapsed_total:.1f}s. '
            f'{verb} {total_games_updated} Games and {total_trophies_updated} Trophies '
            f'across {chunks_processed} chunks.'
        ))

    def _widen_statement_timeout(self):
        """Raise statement_timeout for this connection only (see STATEMENT_TIMEOUT_MS)."""
        try:
            with connection.cursor() as cursor:
                cursor.execute(f'SET statement_timeout = {STATEMENT_TIMEOUT_MS}')
        except Exception:
            # Non-fatal: a restrictive inherited timeout may still abort a chunk, but that
            # surfaces as a loud QueryCanceled rather than silently skewed stats.
            logger.warning('recalc_earn_rates: could not raise statement_timeout', exc_info=True)

    def _get_cursor(self):
        """Highest Game id fully processed last run, or None to start a fresh pass."""
        try:
            raw = redis_client.get(CURSOR_KEY)
        except Exception:
            logger.warning('recalc_earn_rates: redis unavailable for cursor read')
            return None
        if not raw:
            return None
        try:
            return int(raw.decode() if isinstance(raw, bytes) else raw)
        except (TypeError, ValueError):
            return None

    def _set_cursor(self, game_id):
        try:
            if game_id is None:
                redis_client.delete(CURSOR_KEY)
            else:
                redis_client.set(CURSOR_KEY, str(game_id))
        except Exception:
            # Losing the cursor costs coverage of the tail, not correctness, and the next
            # run simply restarts the pass -- so this stays a warning.
            logger.warning('recalc_earn_rates: redis unavailable for cursor write')

    def _process_chunk(self, game_ids, dry_run):
        """Recompute one chunk of games. Three bulk queries, two bulk updates."""
        # 1. Community stats per game (one GROUP BY across ProfileGame): played_count PLUS the four denormed
        #    completion stats (plats earned / 100% completions / avg completion / monthly players). All five
        #    share this single aggregate + population (ALL ProfileGame rows, incl. user_hidden) so the
        #    denominator is consistent. `monthly` is a filtered Count on the same scan rather than its own
        #    query -- it rides along for free.
        monthly_since = timezone.now() - MONTHLY_PLAYER_WINDOW
        game_stats = {
            row['game_id']: row
            for row in (
                ProfileGame.objects.filter(game_id__in=game_ids)
                .values('game_id')
                .annotate(
                    cnt=Count('id'),
                    plats=Count('id', filter=Q(has_plat=True)),
                    completions=Count('id', filter=Q(progress=100)),
                    avg=Avg('progress'),
                    monthly=Count('id', filter=Q(last_played_date_time__gte=monthly_since)),
                )
            )
        }

        # 2. Earned counts per trophy (one GROUP BY across EarnedTrophy).
        earned_counts = dict(
            EarnedTrophy.objects.filter(trophy__game_id__in=game_ids, earned=True)
            .values('trophy_id').annotate(cnt=Count('id'))
            .values_list('trophy_id', 'cnt')
        )

        # 3. Current Trophy state for change detection.
        trophies = list(
            Trophy.objects.filter(game_id__in=game_ids)
            .only('id', 'game_id', 'earned_count', 'earn_rate')
        )

        trophies_by_game = defaultdict(list)
        for t in trophies:
            trophies_by_game[t.game_id].append(t)

        # Build update lists. We diff against current values so the
        # bulk_update only writes rows that actually changed.
        trophy_updates = []
        game_updates = []

        for game_id in game_ids:
            s = game_stats.get(game_id)
            new_played = s['cnt'] if s else 0
            game_trophies = trophies_by_game.get(game_id, [])
            game_updates.append(Game(
                id=game_id,
                played_count=new_played,
                plats_earned_count=s['plats'] if s else 0,
                full_completion_count=s['completions'] if s else 0,
                avg_completion=round(s['avg'], 1) if (s and s['avg'] is not None) else 0.0,
                monthly_players_count=s['monthly'] if s else 0,
                # Free: earned_counts is already in memory for the earn-rate pass below, so the
                # game-wide total is a sum over this game's trophies rather than a new query.
                total_earns_count=sum(earned_counts.get(t.id, 0) for t in game_trophies),
            ))

            for trophy in game_trophies:
                new_earned = earned_counts.get(trophy.id, 0)
                new_rate = new_earned / new_played if new_played > 0 else 0.0
                if trophy.earned_count != new_earned or trophy.earn_rate != new_rate:
                    trophy.earned_count = new_earned
                    trophy.earn_rate = new_rate
                    trophy_updates.append(trophy)

        if not dry_run:
            if game_updates:
                Game.objects.bulk_update(game_updates, [
                    'played_count', 'plats_earned_count', 'full_completion_count', 'avg_completion',
                    'monthly_players_count', 'total_earns_count',
                ])
            if trophy_updates:
                Trophy.objects.bulk_update(trophy_updates, ['earned_count', 'earn_rate'])

        return len(game_updates), len(trophy_updates)
