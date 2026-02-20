"""
Management command to bulk sync Discord roles for all verified users.

Iterates over all profiles with Discord linked and calls sync_discord_roles()
for each, which re-assigns badge roles, milestone roles, and premium roles.
The bot's /assign-role endpoint is idempotent, so re-assigning existing roles
is harmless.

Useful when:
- A new badge or milestone gets a Discord role added
- Discord role IDs change and need to be re-pushed
- Roles get out of sync for any reason

Usage:
    python manage.py sync_all_discord_roles                    # Sync all verified users
    python manage.py sync_all_discord_roles --dry-run          # Preview who would be synced
    python manage.py sync_all_discord_roles --profile jlowe    # Sync a single user
    python manage.py sync_all_discord_roles --batch-size 50    # Control DB chunk size
"""
import logging

from django.core.management.base import BaseCommand
from django.db.models.functions import Lower

from trophies.models import Profile
from trophies.services.badge_service import sync_discord_roles

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Sync Discord roles for all verified users'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview which users would be synced without making API calls',
        )
        parser.add_argument(
            '--profile',
            type=str,
            help='PSN username of a specific profile to sync',
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=100,
            help='DB cursor chunk size for memory-efficient iteration (default: 100)',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        profile_username = options.get('profile')
        batch_size = options['batch_size']

        self.stdout.write("=" * 70)
        self.stdout.write("Discord Role Sync")
        self.stdout.write("=" * 70)

        if dry_run:
            self.stdout.write(self.style.WARNING(
                "DRY RUN MODE - No roles will be synced"
            ))

        # Build queryset
        queryset = Profile.objects.filter(
            is_discord_verified=True,
            discord_id__isnull=False,
        ).select_related('user').order_by(Lower('psn_username'))

        if profile_username:
            queryset = queryset.filter(psn_username__iexact=profile_username)
            if not queryset.exists():
                self.stdout.write(self.style.ERROR(
                    f"No verified Discord profile found for '{profile_username}'"
                ))
                return

        total = queryset.count()

        if total == 0:
            self.stdout.write(self.style.WARNING(
                "No Discord-verified profiles found"
            ))
            return

        self.stdout.write(f"\nFound {total} verified profile(s)")

        if dry_run:
            self._preview(queryset)
            return

        self._sync_roles(queryset, total, batch_size)

    def _preview(self, queryset):
        """Preview which users would be synced in dry-run mode."""
        self.stdout.write("\nProfiles that would be synced:")
        self.stdout.write("-" * 70)

        for profile in queryset.iterator():
            self.stdout.write(
                f"  {profile.psn_username} (Discord ID: {profile.discord_id})"
            )

        self.stdout.write("-" * 70)
        self.stdout.write(f"Total: {queryset.count()} profile(s)")

    def _sync_roles(self, queryset, total, batch_size):
        """Sync roles for all profiles in the queryset."""
        synced = 0
        failed = 0
        total_roles = 0

        self.stdout.write(f"\nSyncing roles...")
        self.stdout.write("-" * 70)

        for i, profile in enumerate(queryset.iterator(chunk_size=batch_size), 1):
            try:
                counts = sync_discord_roles(profile)
                role_count = sum(counts.values())
                total_roles += role_count
                synced += 1
                self.stdout.write(self.style.SUCCESS(
                    f"  [{i}/{total}] {profile.psn_username}: "
                    f"{role_count} role(s) synced {counts}"
                ))
            except Exception as e:
                failed += 1
                self.stdout.write(self.style.ERROR(
                    f"  [{i}/{total}] {profile.psn_username}: failed - {e}"
                ))
                logger.exception(
                    f"Failed to sync Discord roles for {profile.psn_username}"
                )

        # Summary
        self.stdout.write("-" * 70)
        self.stdout.write(self.style.SUCCESS(f"Synced: {synced}"))
        if failed > 0:
            self.stdout.write(self.style.ERROR(f"Failed: {failed}"))
        self.stdout.write(f"Total roles assigned: {total_roles}")
        self.stdout.write(f"Total profiles: {total}")
