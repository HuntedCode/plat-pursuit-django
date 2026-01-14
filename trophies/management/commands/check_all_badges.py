from django.core.management.base import BaseCommand
from trophies.models import Profile, ProfileGame
from trophies.services.badge_service import check_profile_badges

class Command(BaseCommand):
    def handle(self, *args, **options):
        profiles_qs = Profile.objects.all()
        updated_profiles = 0

        for profile in profiles_qs:
            try:
                pg_qs = ProfileGame.objects.filter(profile=profile)
                pg_ids = []
                for pg in pg_qs:
                    pg_ids.append(pg.id)
                check_profile_badges(profile, pg_ids)
            except Exception as e:
                self.stdout.write(f"Failed to update badges for profile {profile.psn_username}: {e}")
            updated_profiles += 1
        self.stdout.write(self.style.SUCCESS(f"Successfully updated badges for {updated_profiles} profiles!"))


        