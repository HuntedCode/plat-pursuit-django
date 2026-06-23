import logging

from django.core.management.base import BaseCommand

from trophies.models import Concept, Game
from trophies.services.concept_anchor_service import try_anchor_new_game

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Resolve a concept for every game stuck without one. For each "
        "concept-less Game it tries the IGDB anchor (try_anchor_new_game); on a "
        "clean match the Game lands on its IGDB-anchored Concept (enriched "
        "inline). When there's no clean IGDB match it falls back to a PP_ stub so "
        "the Game is never left concept-less. Runs inline, no PSN calls, no "
        "worker needed: this is the same anchor-or-stub recovery sync_complete's "
        "orphan reconcile performs, exposed as an on-demand command. To "
        "re-evaluate games that already have a (stub) concept, use anchor_concepts."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='List the concept-less games without resolving them',
        )
        parser.add_argument(
            '--profile-id', type=int, default=None,
            help='Only resolve games owned by this profile',
        )
        parser.add_argument(
            '--limit', type=int, default=None,
            help='Process at most this many games (each does one IGDB match call)',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        profile_id = options['profile_id']
        limit = options['limit']

        # try_anchor_new_game only acts on Games with no concept FK (it
        # short-circuits to None when concept_id is already set), so PP_ stubs
        # are intentionally out of scope here, anchor_concepts re-evaluates those.
        games = Game.objects.filter(concept__isnull=True)
        if profile_id:
            games = games.filter(played_by__profile_id=profile_id).distinct()
        games = games.order_by('id')

        total = games.count()
        if total == 0:
            self.stdout.write(self.style.SUCCESS("No concept-less games found."))
            return

        if limit:
            games = games[:limit]

        self.stdout.write(
            f"Found {total} concept-less game(s)"
            + (f"; processing first {limit}" if limit and limit < total else "")
            + ":"
        )

        if dry_run:
            for game in games:
                platforms = ', '.join(game.title_platform) if game.title_platform else 'Unknown'
                self.stdout.write(f"  {game.title_name} ({game.np_communication_id}) - {platforms}")
            self.stdout.write("\n[DRY RUN] No concepts resolved.")
            return

        anchored = 0
        stubbed = 0
        failed = 0
        for game in games:
            try:
                # Clean IGDB match: lands the Game on its anchored Concept
                # (enriched inline by process_match) and returns it.
                if try_anchor_new_game(game) is not None:
                    anchored += 1
                    self.stdout.write(
                        f"  [anchored] {game.title_name} ({game.np_communication_id})"
                    )
                    continue
                # No clean match: guard against a concurrent placement, then stub.
                # Unlike sync_complete's orphan reconcile, stubs minted here are
                # NOT queued for deferred IGDB enrichment (no worker/profile
                # context to defer into). Re-run anchor_concepts later if you
                # want these stubs re-evaluated against IGDB.
                game.refresh_from_db(fields=['concept'])
                if game.concept_id is None:
                    stub = Concept.create_default_concept(game)
                    game.add_concept(stub)
                    stubbed += 1
                    self.stdout.write(
                        f"  [stub {stub.concept_id}] {game.title_name} ({game.np_communication_id})"
                    )
                else:
                    anchored += 1  # placed by a concurrent process
            except Exception:
                # Per-game isolation: one bad game must not abort the batch.
                failed += 1
                self.stderr.write(
                    f"  [FAILED] {game.title_name} ({game.np_communication_id})"
                )
                logger.exception(
                    f"concept recovery failed for game {game.np_communication_id}"
                )

        self.stdout.write(self.style.SUCCESS(
            f"\nDone. Anchored: {anchored}, Stubbed: {stubbed}, Failed: {failed}."
        ))
