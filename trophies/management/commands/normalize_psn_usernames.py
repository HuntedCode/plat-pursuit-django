from django.core.management.base import BaseCommand
from trophies.models import Profile

class Command(BaseCommand):
    help = 'Normalize existing PSN usernames to lowercase'

    def handle(self, *args, **options):
        for profile in Profile.objects.all():
            if profile.psn_username:
                original = profile.psn_username
                profile.psn_username = original.lower()
                profile.save(update_fields=['psn_username'])