"""Seed ProfileTrophyGroup (per-group standings) for the group/speed leaderboards.

One-time backfill after the 0261/0262 migrations, before the group boards go live. Reuses the exact same
whale-safe aggregate the sync path uses (PsnApiService.update_trophy_group_stats), batched over profiles so
memory stays bounded. Idempotent and resumable: rows are upserted, so re-running (or resuming with
--start-id after an interruption) is safe.

    python manage.py backfill_profile_trophy_groups                 # everyone
    python manage.py backfill_profile_trophy_groups --username X    # one profile (spot-check)
    python manage.py backfill_profile_trophy_groups --start-id 500  # resume past profile id 500
"""
from django.core.management.base import BaseCommand

from trophies.models import Profile, ProfileGame
from trophies.services.psn_api_service import PsnApiService


class Command(BaseCommand):
    help = "Backfill ProfileTrophyGroup per-group standings for the group/speed leaderboards."

    def add_arguments(self, parser):
        parser.add_argument('--batch-size', type=int, default=100, help='Profiles processed per batch (default 100).')
        parser.add_argument('--username', type=str, help='Backfill a single profile by PSN username (spot-check).')
        parser.add_argument('--start-id', type=int, default=0, help='Resume: only profiles with id greater than this.')

    def handle(self, *args, **opts):
        if opts['username']:
            self._one(opts['username'])
            return

        batch_size = opts['batch_size']
        # Only profiles that actually own games (played_games is ProfileGame's reverse relation).
        profile_ids = list(
            Profile.objects.filter(id__gt=opts['start_id'], played_games__isnull=False)
            .order_by('id').values_list('id', flat=True).distinct()
        )
        total = len(profile_ids)
        self.stdout.write(f"Backfilling {total} profiles in batches of {batch_size}...")

        done = 0
        for i in range(0, total, batch_size):
            batch = profile_ids[i:i + batch_size]
            game_ids = list(
                ProfileGame.objects.filter(profile_id__in=batch)
                .values_list('game_id', flat=True).distinct()
            )
            PsnApiService.update_trophy_group_stats(batch, game_ids)
            done += len(batch)
            self.stdout.write(f"  {done}/{total} profiles (through id {batch[-1]})")

        self.stdout.write(self.style.SUCCESS(f"Done. Backfilled {done} profiles."))

    def _one(self, username):
        try:
            profile = Profile.objects.get(psn_username=username.lower())   # stored lowercased
        except Profile.DoesNotExist:
            self.stderr.write(self.style.ERROR(f"No profile for username: {username}"))
            return
        game_ids = list(ProfileGame.objects.filter(profile=profile).values_list('game_id', flat=True))
        PsnApiService.update_trophy_group_stats([profile.id], game_ids)
        self.stdout.write(self.style.SUCCESS(f"Backfilled {profile.psn_username} ({len(game_ids)} games)."))
