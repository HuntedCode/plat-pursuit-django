from django.core.management.base import BaseCommand
from trophies.models import Profile, ProfileGame
from trophies.utils import check_profile_badges

class Command(BaseCommand):
    def handle(self, *args, **options):
        profiles = Profile.objects.all()
        checked = 0
        for profile in profiles:
            profilegame_ids = ProfileGame.objects.filter(
                profile=profile,
                game__concept__badges__user_title__isnull=False,
            ).exclude(
                game__concept__badges__user_title=''
            ).values_list('id', flat=True).distinct()
            check_profile_badges(profile, profilegame_ids, skip_notis=True)
            checked += 1
            self.stdout.write(f"Checked {profilegame_ids.count()} profilegames for profile {profile.id}")
        self.stdout.write(self.style.SUCCESS(f"Checked {checked} profiles successfully!"))