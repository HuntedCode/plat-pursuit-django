from django.core.management.base import BaseCommand
from django.db.models import Q

from trophies.models import Game, Profile, ProfileGame
from trophies.psn_manager import PSNManager
from trophies.util_modules.constants import TITLE_STATS_SUPPORTED_PLATFORMS


class Command(BaseCommand):
    help = (
        "Find profiles that own PS4/PS5 games without concepts and queue "
        "sync_title_stats to resolve proper concepts through the normal pipeline."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Preview affected games and profiles without queuing anything',
        )
        parser.add_argument(
            '--profile-id', type=int, default=None,
            help='Only target a specific profile',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        profile_id = options['profile_id']

        # Find PS4/PS5 games without concepts
        modern_platforms = Q()
        for platform in TITLE_STATS_SUPPORTED_PLATFORMS:
            modern_platforms |= Q(title_platform__contains=platform)

        conceptless_games = Game.objects.filter(
            modern_platforms,
            concept__isnull=True,
        )

        total_games = conceptless_games.count()
        if total_games == 0:
            self.stdout.write(self.style.SUCCESS("No PS4/PS5 games without concepts found."))
            return

        self.stdout.write(f"Found {total_games} PS4/PS5 game(s) without concepts:")
        for game in conceptless_games:
            self.stdout.write(f"  {game.title_name} ({game.np_communication_id}) - {game.title_platform}")

        # Pick one profile per concept-less game to avoid redundant syncs.
        # The concept is assigned to the Game (shared), so only one profile
        # needs to resolve it through sync_title_id.
        needed_profile_ids = set()
        games_per_profile = {}
        for game in conceptless_games:
            pg_qs = ProfileGame.objects.filter(game=game)
            if profile_id:
                pg_qs = pg_qs.filter(profile_id=profile_id)
            pg = pg_qs.values_list('profile_id', flat=True).first()
            if pg:
                needed_profile_ids.add(pg)
                games_per_profile.setdefault(pg, []).append(game)
            else:
                self.stdout.write(f"  WARNING: No profile found for {game.title_name} ({game.np_communication_id})")

        if not needed_profile_ids:
            self.stdout.write("No profiles found owning these games.")
            return

        profiles = Profile.objects.filter(id__in=needed_profile_ids).select_related('user')

        self.stdout.write(f"\n{profiles.count()} profile(s) need sync_title_stats:")
        for profile in profiles:
            game_count = len(games_per_profile.get(profile.id, []))
            self.stdout.write(f"  {profile.user.username} (ID: {profile.id}) - {game_count} concept-less game(s)")

        if dry_run:
            self.stdout.write("\n[DRY RUN] No jobs queued.")
            return

        queued = 0
        skipped = 0
        for profile in profiles:
            if profile.sync_status == 'syncing':
                self.stdout.write(f"  Skipping {profile.user.username} (already syncing)")
                skipped += 1
                continue

            # Queue a single sync_title_stats with is_last=True so it
            # auto-paginates through all results. Games without title_ids
            # won't be matched by update_profile_game_with_title_stats,
            # so they flow into remaining_title_stats -> sync_title_id
            # which resolves proper concepts.
            args = [20, 0, 20, True, False]
            PSNManager.assign_job(
                'sync_title_stats', args=args, profile_id=profile.id,
            )
            queued += 1
            self.stdout.write(f"  Queued sync_title_stats for {profile.user.username}")

        self.stdout.write(self.style.SUCCESS(
            f"\nDone. Queued: {queued}, Skipped (already syncing): {skipped}"
        ))
