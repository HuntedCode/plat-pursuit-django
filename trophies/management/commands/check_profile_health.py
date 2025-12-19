from django.core.management.base import BaseCommand
from psn_manager import PSNManager
from trophies.models import Profile

class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument(
            '--psn_username',
            type=str,
            help='PSN name to check.'
        )

    def handle(self, *args, **options):
        profile = Profile.objects.get(psn_username__iexact=options['psn_username'])
        PSNManager.check_profile_health(profile)
