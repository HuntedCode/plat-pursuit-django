from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count

from trophies.models import Game, Profile, ScoutAccount
from trophies.psn_manager import PSNManager


class Command(BaseCommand):
    help = (
        "Enqueue sync_trophy_groups to refresh games' trophy groups from PSN. "
        "Catches trophy groups (DLC) added to a title after our last sync, which "
        "is common for low-popularity games no active user keeps synced and "
        "therefore can't be detected from our own DB. The PSN "
        "trophy_groups_summary call is title-level (account-agnostic), so a "
        "single driver profile can refresh ANY game's groups, including games "
        "with zero players. Jobs drain on the bulk_priority queue so the sweep "
        "never starves live user syncs. Re-running is safe (idempotent "
        "get_or_create; never deletes trophies/groups)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Count and preview without enqueuing any jobs.',
        )
        parser.add_argument(
            '--driver-profile', dest='driver_profile', default=None,
            help='psn_username of the profile used as auth/context for the PSN '
                 'calls. Defaults to the first active ScoutAccount. NOTE: this '
                 'profile carries the bulk_priority job counter for the whole '
                 'sweep, so its own sync would wait until the sweep drains. '
                 'Prefer a dedicated/pausable scout.',
        )
        parser.add_argument(
            '--missing-only', action='store_true',
            help='Only enqueue games with zero TrophyGroup rows. Default: every '
                 'game (full refresh, the only way to catch newly-added DLC).',
        )
        parser.add_argument(
            '--platform', default=None,
            help='Only enqueue games whose title_platform contains this value '
                 '(e.g. PS4, PS5).',
        )
        parser.add_argument(
            '--limit', type=int, default=None,
            help='Cap the number of games enqueued (for batching/testing).',
        )

    def handle(self, *args, **options):
        driver = self._resolve_driver(options['driver_profile'])
        dry_run = options['dry_run']
        limit = options['limit']

        games = Game.objects.all().order_by('id')
        if options['platform']:
            games = games.filter(title_platform__contains=options['platform'])
        if options['missing_only']:
            games = games.annotate(_group_count=Count('trophy_groups')).filter(_group_count=0)

        total = games.count()
        if total == 0:
            self.stdout.write(self.style.SUCCESS("No matching games. Nothing to do."))
            return

        self.stdout.write(
            f"Driver profile: {driver.psn_username} (id={driver.id})\n"
            f"Matching games: {total}" + (f" (capped to {limit})" if limit else "")
        )
        self.stdout.write(
            "Each game = one PSN trophy_groups_summary call, drained on the "
            "bulk_priority queue (lowest priority; will not starve live syncs)."
        )

        enqueued = 0
        skipped_platform = 0
        for game in games.iterator():
            if limit and enqueued >= limit:
                break

            platform = self._resolve_platform(game.title_platform)
            if platform is None:
                skipped_platform += 1
                self.stdout.write(self.style.WARNING(
                    f"  SKIP (no resolvable platform): {game.title_name} "
                    f"({game.np_communication_id}) {game.title_platform}"
                ))
                continue

            if dry_run:
                enqueued += 1
                continue

            PSNManager.assign_job(
                'sync_trophy_groups',
                [game.np_communication_id, platform],
                driver.id,
                priority_override='bulk_priority',
            )
            enqueued += 1
            if enqueued % 500 == 0:
                self.stdout.write(f"  queued {enqueued}...")

        skip_note = f", skipped {skipped_platform} with no resolvable platform" if skipped_platform else ""
        if dry_run:
            self.stdout.write(self.style.SUCCESS(
                f"[DRY RUN] Would enqueue {enqueued} game(s){skip_note}. No jobs queued."
            ))
            return

        self.stdout.write(self.style.SUCCESS(
            f"Enqueued {enqueued} sync_trophy_groups job(s) on bulk_priority{skip_note}. "
            f"They drain via the TokenKeeper worker against driver "
            f"'{driver.psn_username}'; avoid syncing that profile until the sweep finishes."
        ))

    def _resolve_driver(self, username):
        """Resolve the driver Profile: explicit --driver-profile, else first
        active scout. The driver only supplies auth/context (the PSN call is
        title-level), so it does not need to own any of the games."""
        if username:
            try:
                return Profile.objects.get(psn_username=username.lower())
            except Profile.DoesNotExist:
                raise CommandError(f"No profile with psn_username '{username}'.")

        scout = (
            ScoutAccount.objects.filter(status='active')
            .select_related('profile').first()
        )
        if not scout:
            raise CommandError(
                "No --driver-profile given and no active ScoutAccount found. "
                "Pass --driver-profile <psn_username>."
            )
        return scout.profile

    @staticmethod
    def _resolve_platform(title_platform):
        """Resolve the API platform for a game. PSPC titles report
        title_platform[0]='PSPC' and carry the real platform at [1]. Returns
        None when the list is empty or only 'PSPC' with no fallback, so the
        caller can skip rather than IndexError."""
        if not title_platform:
            return None
        first = title_platform[0]
        if first != 'PSPC':
            return first
        return title_platform[1] if len(title_platform) > 1 else None
