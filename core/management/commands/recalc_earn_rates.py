"""Site-wide recompute of Trophy.earned_count / Trophy.earn_rate / Game.played_count.

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
import time
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db.models import Count

from trophies.models import Game, Trophy, ProfileGame, EarnedTrophy


class Command(BaseCommand):
    help = (
        'Recompute played_count on Games, and earned_count + earn_rate on '
        'Trophies, using bulk GROUP BY aggregates. Designed for daily cron use.'
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

        if explicit_ids:
            all_game_ids = sorted(set(explicit_ids))
        else:
            all_game_ids = list(Game.objects.order_by('id').values_list('id', flat=True))

        total_games = len(all_game_ids)
        self.stdout.write(self.style.NOTICE(
            f'recalc_earn_rates starting: {total_games} games, chunk={chunk_size}, '
            f'budget={max_seconds // 60}min, dry_run={dry_run}'
        ))

        total_games_updated = 0
        total_trophies_updated = 0
        chunks_processed = 0
        chunks_total = (total_games + chunk_size - 1) // chunk_size

        for chunk_start in range(0, total_games, chunk_size):
            if time.monotonic() >= deadline:
                self.stdout.write(self.style.WARNING(
                    f'Hit max-minutes budget after {chunks_processed}/{chunks_total} chunks. '
                    f'Remaining {chunks_total - chunks_processed} chunks deferred to next run.'
                ))
                break

            chunk_ids = all_game_ids[chunk_start:chunk_start + chunk_size]
            games_updated, trophies_updated = self._process_chunk(chunk_ids, dry_run)
            total_games_updated += games_updated
            total_trophies_updated += trophies_updated
            chunks_processed += 1

            elapsed = time.monotonic() - start
            self.stdout.write(
                f'chunk {chunks_processed}/{chunks_total} '
                f'(games {chunk_start + 1}-{chunk_start + len(chunk_ids)}): '
                f'+{games_updated} games, +{trophies_updated} trophies '
                f'[{elapsed:.1f}s elapsed]'
            )

        elapsed_total = time.monotonic() - start
        verb = 'Would update' if dry_run else 'Updated'
        self.stdout.write(self.style.SUCCESS(
            f'recalc_earn_rates complete in {elapsed_total:.1f}s. '
            f'{verb} {total_games_updated} Games and {total_trophies_updated} Trophies '
            f'across {chunks_processed} chunks.'
        ))

    def _process_chunk(self, game_ids, dry_run):
        """Recompute one chunk of games. Three bulk queries, two bulk updates."""
        # 1. Played counts per game (one GROUP BY across ProfileGame).
        played_counts = dict(
            ProfileGame.objects.filter(game_id__in=game_ids)
            .values('game_id').annotate(cnt=Count('id'))
            .values_list('game_id', 'cnt')
        )

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
            new_played = played_counts.get(game_id, 0)
            game_updates.append(Game(id=game_id, played_count=new_played))

            for trophy in trophies_by_game.get(game_id, []):
                new_earned = earned_counts.get(trophy.id, 0)
                new_rate = new_earned / new_played if new_played > 0 else 0.0
                if trophy.earned_count != new_earned or trophy.earn_rate != new_rate:
                    trophy.earned_count = new_earned
                    trophy.earn_rate = new_rate
                    trophy_updates.append(trophy)

        if not dry_run:
            if game_updates:
                Game.objects.bulk_update(game_updates, ['played_count'])
            if trophy_updates:
                Trophy.objects.bulk_update(trophy_updates, ['earned_count', 'earn_rate'])

        return len(game_updates), len(trophy_updates)
