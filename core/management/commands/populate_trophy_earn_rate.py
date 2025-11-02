from django.core.management.base import BaseCommand
from trophies.models import Trophy

class Command(BaseCommand):
    help = 'Populate earn rate on Trophy model.'

    def handle(self, *args, **options):
        for trophy in Trophy.objects.all():
            trophy.earn_rate = trophy.earned_count / trophy.game.played_count if trophy.game.played_count > 0 else 0.0
            trophy.save(update_fields=['earn_rate'])
        self.stdout.write(self.style.SUCCESS('Earn rates populated successfully.'))