from django.core.management.base import BaseCommand
from trophies.models import Badge, Profile
from trophies.utils import process_badge

class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument(
            '--slug',
            type=str,
            help='Filter to a specific series_slug (e.g. "spider-man")'
        )

    def handle(self, *args, **options):
        series_slug = options['slug']
        badges = Badge.objects.filter(series_slug=series_slug)
        profiles = Profile.objects.filter(played_games__game__concept__badges__in=badges).distinct()
        for profile in profiles:
            for badge in badges:
                process_badge(profile, badge)
            self.stdout.write(f"Processed {len(badges)} badges for {profile.psn_username}")
        self.stdout.write(self.style.SUCCESS(f"Refreshed badge for {len(profiles)} profiles successfully!")) 