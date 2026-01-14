from django.core.management.base import BaseCommand
from trophies.models import Profile
from trophies.services.badge_service import initial_badge_check

class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument('--username', type=str, required=False)
        parser.add_argument('--notify', action='store_true')

    def handle(self, *args, **options):
        username = options['username'] if options['username'] else None
        notify = options['notify']

        if username:
            profile = Profile.objects.get(psn_username=username)
            initial_badge_check(profile, discord_notify=notify)
        else:
            profiles = Profile.objects.all()
            for profile in profiles:
                initial_badge_check(profile, discord_notify=notify)