from django.core.management.base import BaseCommand, CommandError

from trophies.models import Game
from trophies.psn_manager import PSNManager
from trophies.util_modules.psn_sweep import (
    SweepConfigurationError, resolve_api_platform, resolve_driver_profile,
)


class Command(BaseCommand):
    help = (
        "Re-run PSN concept resolution across the catalogue so every game gets a fresh "
        "get_details response, and with it a PSNConceptData + PSNRawPayload capture. "
        "Capture only happens on the sync path, so games resolved before that shipped have "
        "no PSN row and will never get one on their own: an already-anchored, already-matched "
        "game is never re-queued by a normal profile sync. This is the deliberate walk that "
        "fills them in. "
        "Jobs drain on bulk_priority so the sweep never starves live user syncs. "
        "NOT CAPTURE-ONLY -- see the warning printed at start; run --dry-run first."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Count and preview without enqueuing any jobs.',
        )
        parser.add_argument(
            '--driver-profile', dest='driver_profile', default=None,
            help='psn_username of the profile supplying auth/context. Defaults to the first '
                 'active ScoutAccount. This profile carries the bulk_priority job counter for '
                 'the whole sweep and its sync_progress_value is incremented once per job, so '
                 'use a dedicated scout and reset its progress afterwards.',
        )
        parser.add_argument(
            '--missing-only', action='store_true',
            help='Only enqueue games whose concept has no PSNConceptData row yet. This is the '
                 'backfill filter: it shrinks every re-run to what is genuinely uncaptured, so '
                 'an interrupted sweep can be resumed without re-spending API calls.',
        )
        parser.add_argument(
            '--platform', default=None,
            help='Only enqueue games whose title_platform contains this value (e.g. PS4, PS5).',
        )
        parser.add_argument(
            '--limit', type=int, default=None,
            help='Cap the number of games enqueued. Use this to run the sweep in sessions '
                 'rather than dumping the whole catalogue into Redis at once.',
        )

    def handle(self, *args, **options):
        driver = self._resolve_driver(options['driver_profile'])
        dry_run = options['dry_run']
        limit = options['limit']

        games = Game.objects.exclude(title_ids=[]).order_by('id')
        if options['platform']:
            games = games.filter(title_platform__contains=options['platform'])
        if options['missing_only']:
            # concept__isnull=False first: a concept-less game has nothing to have captured, and
            # would otherwise match `psn_data__isnull=True` and be swept every single run.
            games = games.filter(concept__isnull=False, concept__psn_data__isnull=True)

        # Only the four columns the loop reads. The catalogue is tens of thousands of rows and
        # every JSONField pulled here is dead weight.
        games = games.only('id', 'np_communication_id', 'title_ids', 'title_platform')

        total = games.count()
        if total == 0:
            self.stdout.write(self.style.SUCCESS("No matching games. Nothing to do."))
            return

        self.stdout.write(
            f"Driver profile: {driver.psn_username} (id={driver.id})\n"
            f"Matching games: {total}" + (f" (capped to {limit})" if limit else "")
        )
        self.stdout.write(self.style.WARNING(
            "This enqueues sync_title_id, which is the full concept-resolution pipeline, not a\n"
            "read-only capture. Per game it may also: correct TitleID platform/region, add a\n"
            "region to the Game, refresh a PSN-native concept's release date, media and English\n"
            "fields, and anchor a concept-less Game to IGDB. Those are the same writes a normal\n"
            "sync performs -- this only makes them happen sooner -- but on an IGDB-anchored\n"
            "concept nothing is overwritten: that branch preserves the concept and captures.\n"
            "Each game costs at least one PSN get_details call, more if it walks region fallbacks."
        ))

        enqueued = 0
        skipped_platform = 0
        for game in games.iterator():
            if limit and enqueued >= limit:
                break

            platform = resolve_api_platform(game.title_platform)
            if platform is None:
                skipped_platform += 1
                continue

            if dry_run:
                enqueued += 1
                continue

            # sync_title_id resolves its own platform from the Game/TitleID, so the platform
            # check above is a skip filter rather than a job argument.
            PSNManager.assign_job(
                'sync_title_id',
                [game.title_ids[0], game.np_communication_id],
                driver.id,
                priority_override='bulk_priority',
            )
            enqueued += 1
            if enqueued % 500 == 0:
                self.stdout.write(f"  queued {enqueued}...")

        skip_note = (
            f", skipped {skipped_platform} with no resolvable platform" if skipped_platform else ""
        )
        if dry_run:
            self.stdout.write(self.style.SUCCESS(
                f"[DRY RUN] Would enqueue {enqueued} game(s){skip_note}. No jobs queued."
            ))
            return

        self.stdout.write(self.style.SUCCESS(
            f"Enqueued {enqueued} sync_title_id job(s) on bulk_priority{skip_note}. "
            f"They drain via the TokenKeeper worker against driver '{driver.psn_username}'; "
            f"avoid syncing that profile until the sweep finishes, then reset its sync progress."
        ))

    def _resolve_driver(self, username):
        try:
            return resolve_driver_profile(username)
        except SweepConfigurationError as exc:
            raise CommandError(str(exc))
