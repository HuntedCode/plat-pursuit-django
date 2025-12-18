from django.core.management.base import BaseCommand
from django.db.models import Count
from django.db import transaction
from trophies.models import Game, Trophy, ProfileGame, EarnedTrophy

class Command(BaseCommand):
    help = 'Recalculates played_count on Games, earned_count & earn_rate on Trophies'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Simulate without saving changes.')
        parser.add_argument('--batch-size', type=int, default=1000, help='Batch size for updates.')

    @transaction.atomic
    def handle(self, *args, **options):
        dry_run = options['dry_run']
        batch_size = options['batch_size']
        self.stdout.write(self.style.NOTICE(f"Starting recalc... (dry-run: {dry_run}, batch: {batch_size})"))

        games_to_update = []
        game_qs = Game.objects.all().iterator(chunk_size=batch_size)
        for game in game_qs:
            new_played_count = ProfileGame.objects.filter(game=game).count()
            if new_played_count != game.played_count:
                game.played_count = new_played_count
                games_to_update.append(game)
        if not dry_run and games_to_update:
            Game.objects.bulk_update(games_to_update, ['played_count'], batch_size=batch_size)
        self.stdout.write(f"Updated {len(games_to_update)} Games played_count.")
        
        trophies_to_update = []
        trophy_qs = Trophy.objects.select_related('game').iterator(chunk_size=batch_size)
        for trophy in trophy_qs:
            new_earned_count = EarnedTrophy.objects.filter(trophy=trophy, earned=True).count()
            if new_earned_count != trophy.earned_count:
                trophy.earned_count = new_earned_count
            if trophy.game.played_count > 0:
                new_earn_rate = trophy.earned_count / trophy.game.played_count
                if new_earn_rate != trophy.earn_rate:
                    trophy.earn_rate = new_earn_rate
            trophies_to_update.append(trophy)
        if not dry_run and trophies_to_update:
            Trophy.objects.bulk_update(trophies_to_update, ['earned_count', 'earn_rate'], batch_size=batch_size)
        self.stdout.write(f"Updated {len(trophies_to_update)} Trophies earned_count/earn_rate.")
                
        self.stdout.write(self.style.SUCCESS('Recalculation complete!'))