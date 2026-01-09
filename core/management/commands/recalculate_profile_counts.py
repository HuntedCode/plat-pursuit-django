from django.core.management.base import BaseCommand
from django.db.models import Sum
from trophies.models import Profile, EarnedTrophy, ProfileGame
from trophies.utils import update_profile_trophy_counts

class Command(BaseCommand):
    def handle(self, *args, **options):
        profile_qs = Profile.objects.all().iterator()
        total_profiles = 0
        for profile in profile_qs:
            update_profile_trophy_counts(profile)
            total_profiles += 1
        self.stdout.write(self.style.SUCCESS(f"Profile counts updated for {total_profiles} profiles!"))