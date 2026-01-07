from django.core.management.base import BaseCommand
from trophies.models import Profile
from trophies.utils import initial_badge_check

class Command(BaseCommand):
    def handle(self, *args, **options):
        profiles = Profile.objects.all()
        for profile in profiles:
            initial_badge_check(profile, discord_notify=False)