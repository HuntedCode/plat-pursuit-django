from django.core.management.base import BaseCommand
from trophies.models import Profile
from trophies.utils import check_discord_role_badges

class Command(BaseCommand):
    def handle(self, *args, **options):
        discord_profiles = Profile.objects.filter(is_discord_verified=True, discord_id__isnull=False)
        for profile in discord_profiles:
            check_discord_role_badges(profile)