from django.core.management.base import BaseCommand
from trophies.models import Trophy

class Command(BaseCommand):
    help = 'Populate denormalized counters on Trophy model.'

    def handle(self, *args, **options):
        for trophy in Trophy.objects.all():
            trophy.earned_count = trophy.earned_trophy_entries.filter(earned=True).count()
            trophy.save(update_fields=['earned_count'])
        self.stdout.write(self.style.SUCCESS('Counters populated successfully.'))