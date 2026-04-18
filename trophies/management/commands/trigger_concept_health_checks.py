from django.core.management.base import BaseCommand
from django.db.models import Q

from trophies.models import Game, Profile, ProfileGame
from trophies.psn_manager import PSNManager
from trophies.util_modules.constants import TITLE_STATS_SUPPORTED_PLATFORMS


class Command(BaseCommand):
    help = (
        "Find profiles that own PS4/PS5 games without a real concept (null OR "
        "PP_ stub) and queue sync_title_stats to resolve proper concepts "
        "through the normal pipeline."
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
        parser.add_argument(
            '--null-only', action='store_true',
            help='Skip PP_ stub concepts; only target games with no concept at all',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        profile_id = options['profile_id']
        null_only = options['null_only']

        # Find PS4/PS5 games without a real concept. A concept is "missing" if
        # it's null OR a PP_ stub (created via Concept.create_default_concept
        # when PSN returned an errorCode for the title lookup). Stubs are fair
        # game because Game.add_concept() will absorb() the stub's data into
        # the real concept and delete the stub on replacement.
        modern_platforms = Q()
        for platform in TITLE_STATS_SUPPORTED_PLATFORMS:
            modern_platforms |= Q(title_platform__contains=platform)

        missing_concept = Q(concept__isnull=True)
        if not null_only:
            missing_concept |= Q(concept__concept_id__startswith='PP_')

        conceptless_games = Game.objects.filter(
            modern_platforms,
        ).filter(missing_concept)

        total_games = conceptless_games.count()
        if total_games == 0:
            self.stdout.write(self.style.SUCCESS("No PS4/PS5 games without concepts found."))
            return

        # Use select_related so the concept_id check below doesn't N+1.
        conceptless_games = conceptless_games.select_related('concept')
        null_count = sum(1 for g in conceptless_games if g.concept is None)
        stub_count = total_games - null_count

        self.stdout.write(
            f"Found {total_games} PS4/PS5 game(s) missing real concepts "
            f"({null_count} null, {stub_count} PP_ stub):"
        )
        for game in conceptless_games:
            kind = 'null' if game.concept is None else f'stub {game.concept.concept_id}'
            self.stdout.write(f"  [{kind}] {game.title_name} ({game.np_communication_id}) - {game.title_platform}")

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

        profiles = Profile.objects.filter(id__in=needed_profile_ids)

        self.stdout.write(f"\n{profiles.count()} profile(s) need sync_title_stats:")
        for profile in profiles:
            game_count = len(games_per_profile.get(profile.id, []))
            username = profile.psn_username
            self.stdout.write(f"  {username} (ID: {profile.id}) - {game_count} concept-less game(s)")

        if dry_run:
            self.stdout.write("\n[DRY RUN] No jobs queued.")
            return

        queued = 0
        skipped = 0
        for profile in profiles:
            username = profile.psn_username
            if profile.sync_status == 'syncing':
                self.stdout.write(f"  Skipping {username} (already syncing)")
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
            self.stdout.write(f"  Queued sync_title_stats for {username}")

        self.stdout.write(self.style.SUCCESS(
            f"\nDone. Queued: {queued}, Skipped (already syncing): {skipped}"
        ))
