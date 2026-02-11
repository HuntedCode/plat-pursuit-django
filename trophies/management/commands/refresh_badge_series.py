from django.core.management.base import BaseCommand
from trophies.models import Badge, Profile
from trophies.services.badge_service import handle_badge
from notifications.services.deferred_notification_service import DeferredNotificationService

class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument('--series', type=str, required=True)

    def handle(self, *args, **options):
        series_slug = options['series']

        if not series_slug:
            self.stdout.write(f"No series given.")
            return

        badges = Badge.objects.filter(series_slug=series_slug).order_by('tier')
        if not badges.exists():
            self.stdout.write(self.style.WARNING(f"No badges found for series_slug '{series_slug}'."))
            return

        profiles = Profile.objects.filter(played_games__game__concept__stages__series_slug=series_slug).distinct()

        if not profiles.exists():
            self.stdout.write(self.style.WARNING(f"No profiles associated with series_slug '{series_slug}'"))

        # Track which profiles had badges awarded (for notification processing)
        profiles_with_new_badges = set()

        processed_count = 0
        for profile in profiles:
            for badge in badges:
                try:
                    created = handle_badge(profile, badge)
                    if created:
                        profiles_with_new_badges.add(profile.id)
                    processed_count += 1
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"Error for profile {profile.psn_username}, badge {badge.id}: {e}"))

        # Process consolidated badge notifications for all affected profiles
        for profile_id in profiles_with_new_badges:
            try:
                DeferredNotificationService.create_badge_notifications(profile_id)
                self.stdout.write(self.style.SUCCESS(f"Processed badge notifications for profile {profile_id}"))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Failed to process notifications for profile {profile_id}: {e}"))

        self.stdout.write(self.style.SUCCESS(f"Processed {processed_count} badge-profile pairs for series '{series_slug}'."))