from django.core.management.base import BaseCommand
from trophies.models import Badge

class Command(BaseCommand):
    def handle(self, *args, **options):
        badges = Badge.objects.all()
        for badge in badges:
            badge.update_required()
        self.stdout.write(self.style.SUCCESS(f"Updated requirements for {len(badges)} badges successfully!"))