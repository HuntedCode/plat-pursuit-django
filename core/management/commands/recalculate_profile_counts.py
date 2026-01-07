from django.core.management.base import BaseCommand
from trophies.models import Profile, EarnedTrophy, ProfileGame

class Command(BaseCommand):
    def handle(self, *args, **options):
        profile_qs = Profile.objects.all().iterator()
        total_profiles = 0
        for profile in profile_qs:
            profile.total_trophies = EarnedTrophy.objects.filter(profile=profile, earned=True).count()
            profile.total_unearned = EarnedTrophy.objects.filter(profile=profile, earned=False).count()
            profile.total_plats = EarnedTrophy.objects.filter(profile=profile, earned=True, trophy__trophy_type='platinum').count()
            profile.total_games = ProfileGame.objects.filter(profile=profile).count()
            profile.total_completes = ProfileGame.objects.filter(profile=profile, progress=100).count()
            profile.avg_progress = profile.get_average_progress()
            profile.save(update_fields=['total_trophies', 'total_unearned', 'total_plats', 'total_games', 'total_completes', 'avg_progress'])
            total_profiles += 1
        self.stdout.write(self.style.SUCCESS(f"Profile counts updated for {total_profiles} profiles!"))