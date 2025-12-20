from django.core.management.base import BaseCommand
from django.db.models import Count
from django.db import transaction
from trophies.models import Game, Trophy, ProfileGame, EarnedTrophy
from django.utils import timezone
from datetime import timedelta  # If adding --since option later

class Command(BaseCommand):
    help = 'Recalculates played_count on Games, earned_count & earn_rate on Trophies'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Simulate without saving changes.')
        parser.add_argument('--batch-size', type=int, default=1000, help='Batch size for queries and updates.')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        batch_size = options['batch_size']
        self.stdout.write(self.style.NOTICE(f"Starting recalc... (dry-run: {dry_run}, batch: {batch_size})"))

        game_updated_count = 0
        game_qs = Game.objects.all()
        total_games = game_qs.count()
        for i in range(0, total_games, batch_size):
            batch = game_qs[i:i + batch_size]
            for game in batch:
                new_played_count = ProfileGame.objects.filter(game=game).count()
                if new_played_count != game.played_count:
                    if not dry_run:
                        game.played_count = new_played_count
                        game.save(update_fields=['played_count'])
                    game_updated_count += 1
            self.stdout.write(f"Processed Games batch {i//batch_size + 1}/{(total_games//batch_size) + 1}")
        self.stdout.write(f"Updated {game_updated_count} Games played_count.")

        trophy_updated_count = 0
        trophy_qs = Trophy.objects.select_related('game')
        total_trophies = trophy_qs.count()
        for i in range(0, total_trophies, batch_size):
            with transaction.atomic():
                batch = trophy_qs[i:i + batch_size]
                for trophy in batch:
                    new_earned_count = EarnedTrophy.objects.filter(trophy=trophy, earned=True).count()
                    updated = False
                    if new_earned_count != trophy.earned_count:
                        trophy.earned_count = new_earned_count
                        updated = True
                    if trophy.game.played_count > 0:
                        new_earn_rate = trophy.earned_count / trophy.game.played_count
                        if new_earn_rate != trophy.earn_rate:
                            trophy.earn_rate = new_earn_rate
                            updated = True
                    if updated and not dry_run:
                        trophy.save(update_fields=['earned_count', 'earn_rate'])
                        trophy_updated_count += 1
            self.stdout.write(f"Processed Trophies batch {i//batch_size + 1}/{(total_trophies//batch_size) + 1}")
        self.stdout.write(f"Updated {trophy_updated_count} Trophies earned_count/earn_rate.")
                
        self.stdout.write(self.style.SUCCESS('Recalculation complete!'))