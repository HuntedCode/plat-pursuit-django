from django.core.management.base import BaseCommand
from trophies.models import Badge, Profile
from trophies.services.badge_service import handle_badge
from notifications.services.deferred_notification_service import DeferredNotificationService


class Command(BaseCommand):
    help = (
        "Refresh badge awards + leaderboards for a single badge series (--series <slug>) "
        "or for every badge series in the DB (--all)."
    )

    def add_arguments(self, parser):
        parser.add_argument('--series', type=str, help='Refresh a single badge series by slug.')
        parser.add_argument(
            '--all', action='store_true', dest='all_series',
            help='Refresh every distinct badge series in the DB.',
        )

    def handle(self, *args, **options):
        series_slug = options['series']
        all_series = options['all_series']

        if all_series:
            slugs = list(
                Badge.objects
                .exclude(series_slug__isnull=True).exclude(series_slug='')
                .values_list('series_slug', flat=True)
                .distinct().order_by('series_slug')
            )
            self.stdout.write(self.style.MIGRATE_HEADING(f"Refreshing ALL {len(slugs)} badge series."))
            for i, slug in enumerate(slugs, 1):
                try:
                    self._refresh_series(slug, position=f"[{i}/{len(slugs)}]")
                except Exception as e:
                    # One bad series shouldn't abort the whole sweep.
                    self.stdout.write(self.style.ERROR(f"Series '{slug}' failed: {e}"))
            self.stdout.write(self.style.SUCCESS(f"Finished refreshing {len(slugs)} badge series."))
            return

        if not series_slug:
            self.stdout.write(self.style.ERROR("Provide --series <slug> or --all."))
            return

        self._refresh_series(series_slug)

    def _refresh_series(self, series_slug, position=''):
        prefix = f"{position} " if position else ''
        self.stdout.write(self.style.MIGRATE_HEADING(f"{prefix}--- BEGIN series '{series_slug}' ---"))

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

        # Rebuild leaderboards for this series (backfills historical progress data)
        try:
            from trophies.services.redis_leaderboard_service import rebuild_series_leaderboards
            earners_count, progress_count = rebuild_series_leaderboards(series_slug)
            self.stdout.write(
                f"Rebuilt leaderboards for {series_slug}: "
                f"{earners_count} earners, {progress_count} progress"
            )
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Failed rebuilding leaderboards for {series_slug}: {e}"))

        self.stdout.write(self.style.SUCCESS(f"{prefix}--- END series '{series_slug}' ---"))
