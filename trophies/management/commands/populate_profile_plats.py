from django.core.management.base import BaseCommand
from trophies.models import Profile

class Command(BaseCommand):
    def handle(self, *args, **options):
        profiles = Profile.objects.all()
        updated_profiles = 0
        for profile in profiles:
            profile.update_plats()
            updated_profiles += 1
        self.stdout.write(self.style.SUCCESS(f"Successfully updated {updated_profiles} profiles!"))