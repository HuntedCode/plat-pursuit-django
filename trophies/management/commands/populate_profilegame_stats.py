from django.core.management.base import BaseCommand
from django.db.models import Max
from trophies.models import ProfileGame, EarnedTrophy

class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument('--batch_size', type=int, default=100, help="Batch size for updates.")

    def handle(self, *args, **options):
        batch_size = options['batch_size']
        pg_qs = ProfileGame.objects.all()
        total = pg_qs.count()
        updated_count = 0
        for i in range(0, total, batch_size):
            batch = pg_qs[i:i + batch_size]
            for pg in batch:
                earned_qs = EarnedTrophy.objects.filter(profile=pg.profile, trophy__game=pg.game)
                pg.earned_trophies_count = earned_qs.filter(earned=True).count()
                pg.unearned_trophies_count = earned_qs.filter(earned=False).count()
                pg.has_plat = earned_qs.filter(trophy__trophy_type='platinum', earned=True).exists()
                recent_date = earned_qs.filter(earned=True).aggregate(
                    max_date=Max('earned_date_time')
                )['max_date']
                pg.most_recent_trophy_date = recent_date if recent_date else None
                pg.save(update_fields=['earned_trophies_count', 'unearned_trophies_count', 'has_plat', 'most_recent_trophy_date'])
                updated_count += 1
            self.stdout.write(f"Processed {i + batch_size}/{total} ProfileGames.")
        self.stdout.write(self.style.SUCCESS(f"Updated {updated_count} ProfileGames."))