"""
Management command to recalculate gamification stats.

Usage:
    python manage.py recalculate_gamification           # All profiles with badge progress
    python manage.py recalculate_gamification --profile=username  # Single profile
    python manage.py recalculate_gamification --dry-run # Show count without updating
"""
from django.core.management.base import BaseCommand
from trophies.models import Profile
from trophies.services.xp_service import update_profile_gamification, recalculate_all_gamification


class Command(BaseCommand):
    help = 'Recalculate gamification stats (badge XP) for profiles'

    def add_arguments(self, parser):
        parser.add_argument(
            '--profile',
            type=str,
            help='PSN username of specific profile to recalculate'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show count of profiles that would be updated without actually updating'
        )

    def handle(self, *args, **options):
        profile_username = options.get('profile')
        dry_run = options.get('dry_run', False)

        if profile_username:
            # Single profile mode
            try:
                profile = Profile.objects.get(psn_username__iexact=profile_username)
            except Profile.DoesNotExist:
                self.stdout.write(
                    self.style.ERROR(f"Profile '{profile_username}' not found.")
                )
                return

            if dry_run:
                self.stdout.write(
                    self.style.SUCCESS(f"Would recalculate gamification for: {profile.psn_username}")
                )
                return

            gamification = update_profile_gamification(profile)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Recalculated gamification for {profile.psn_username}:\n"
                    f"  Total XP: {gamification.total_badge_xp}\n"
                    f"  Badges Earned: {gamification.total_badges_earned}\n"
                    f"  Series: {len(gamification.series_badge_xp)} series with XP"
                )
            )
            return

        # All profiles mode
        profiles_with_progress = Profile.objects.filter(
            badge_progress__isnull=False
        ).distinct()
        count = profiles_with_progress.count()

        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(f"Would recalculate gamification for {count} profiles")
            )
            return

        self.stdout.write(f"Recalculating gamification for {count} profiles...")

        updated_count = recalculate_all_gamification()

        self.stdout.write(
            self.style.SUCCESS(f"Successfully recalculated gamification for {updated_count} profiles")
        )
