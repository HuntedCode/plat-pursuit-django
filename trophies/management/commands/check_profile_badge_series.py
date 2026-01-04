from django.core.management.base import BaseCommand
from trophies.models import Badge, Profile
from trophies.utils import handle_badge

class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument('--username', type=str, required=True)
        parser.add_argument('--series', type=str, required=True)

    def handle(self, *args, **options):
        username_str = options['username']
        series_slug = options['series']

        if not username_str or not series_slug:
            self.stdout.write("No arguments.")
            return
        
        try:
            profile = Profile.objects.get(psn_username=username_str)
        except Profile.DoesNotExist:
            self.stdout.write("Could not find profile.")
            return

        badges = Badge.objects.filter(series_slug=series_slug).order_by('tier')
        if not badges:
              self.stdout.write("Couldn't find any badges with that series_slug.")
              return
        checked_count = 0
        for badge in badges:
            created = handle_badge(profile, badge)
            checked_count += 1
            if created:
                self.stdout.write(f"Badge tier {badge.tier} was created.")
        
        self.stdout.write(self.style.SUCCESS(f"Checked {checked_count} badges successfully!"))