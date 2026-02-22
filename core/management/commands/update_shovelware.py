import logging

from django.core.management.base import BaseCommand
from django.utils import timezone

from trophies.models import Game, PublisherBlacklist, Trophy, Concept
from trophies.services.shovelware_detection_service import ShovelwareDetectionService

logger = logging.getLogger("psn_api")


class Command(BaseCommand):
    help = "Rebuild the shovelware list from scratch using rule-based detection."

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Report what would change without writing to the database.',
        )
        parser.add_argument(
            '--verbose', action='store_true',
            help='Print details for every game evaluated.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        verbose = options['verbose']

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN: no changes will be saved.\n"))

        # Step 1: Reset all non-locked, non-manual statuses to clean
        self.stdout.write("Step 1: Resetting auto-flagged games to clean...")
        reset_count = Game.objects.filter(
            shovelware_status='auto_flagged',
            shovelware_lock=False,
        ).count()
        if not dry_run:
            Game.objects.filter(
                shovelware_status='auto_flagged',
                shovelware_lock=False,
            ).update(shovelware_status='clean', shovelware_updated_at=None)
        self.stdout.write(f"  {reset_count} game(s) reset to clean.")

        # Step 2: Clear all PublisherBlacklist entries
        self.stdout.write("\nStep 2: Resetting publisher blacklist...")
        pub_count = PublisherBlacklist.objects.count()
        if not dry_run:
            PublisherBlacklist.objects.all().update(flagged_concepts=[], is_blacklisted=False)
        self.stdout.write(f"  {pub_count} publisher(s) reset.")

        # Step 3: Scan all games with platinum trophies having >= 90% earn rate
        self.stdout.write(
            f"\nStep 3: Scanning for games with plat earn rate "
            f">= {ShovelwareDetectionService.FLAG_THRESHOLD}%..."
        )
        flagged_games = set()
        flagged_concepts = set()
        publisher_concepts = {}  # publisher_name -> set of concept_ids

        high_rate_plats = (
            Trophy.objects
            .filter(
                trophy_type='platinum',
                trophy_earn_rate__gte=ShovelwareDetectionService.FLAG_THRESHOLD,
            )
            .select_related('game__concept')
            .only('game__id', 'game__title_name', 'game__concept__concept_id',
                  'game__concept__publisher_name', 'game__shovelware_lock',
                  'game__shovelware_status', 'trophy_earn_rate')
        )

        now = timezone.now()
        for plat in high_rate_plats:
            game = plat.game

            if game.shovelware_lock or game.shovelware_status == 'manually_flagged':
                if verbose:
                    self.stdout.write(f"  [SKIP] {game.title_name} (locked/manual)")
                continue

            concept = game.concept
            if concept:
                concept_id = concept.concept_id
                flagged_concepts.add(concept_id)

                if concept.publisher_name:
                    if concept.publisher_name not in publisher_concepts:
                        publisher_concepts[concept.publisher_name] = set()
                    publisher_concepts[concept.publisher_name].add(concept_id)

                if not dry_run:
                    count = Game.objects.filter(
                        concept=concept, shovelware_lock=False,
                    ).exclude(
                        shovelware_status='manually_flagged',
                    ).update(shovelware_status='auto_flagged', shovelware_updated_at=now)
                else:
                    count = Game.objects.filter(
                        concept=concept, shovelware_lock=False,
                    ).exclude(
                        shovelware_status='manually_flagged',
                    ).count()

                if verbose:
                    self.stdout.write(
                        f"  [FLAG] {game.title_name} ({plat.trophy_earn_rate:.1f}%) "
                        f"-> {count} game(s) in concept {concept_id}"
                    )
                flagged_games.add(game.id)
            else:
                if not dry_run:
                    game.shovelware_status = 'auto_flagged'
                    game.shovelware_updated_at = now
                    game.save(update_fields=['shovelware_status', 'shovelware_updated_at'])
                flagged_games.add(game.id)
                if verbose:
                    self.stdout.write(
                        f"  [FLAG] {game.title_name} ({plat.trophy_earn_rate:.1f}%) (no concept)"
                    )

        self.stdout.write(
            f"  Flagged {len(flagged_games)} game(s) across {len(flagged_concepts)} concept(s)."
        )

        # Step 4: Update publisher blacklist entries
        self.stdout.write("\nStep 4: Updating publisher blacklist...")
        blacklisted_publishers = []

        for pub_name, concept_ids in publisher_concepts.items():
            is_bl = bool(concept_ids)

            if not dry_run:
                entry, _ = PublisherBlacklist.objects.get_or_create(name=pub_name)
                entry.flagged_concepts = list(concept_ids)
                entry.is_blacklisted = is_bl
                entry.save(update_fields=['flagged_concepts', 'is_blacklisted'])

            if is_bl:
                blacklisted_publishers.append(pub_name)

            if verbose or is_bl:
                status = "BLACKLISTED" if is_bl else f"{len(concept_ids)} concepts"
                self.stdout.write(f"  {pub_name}: {status}")

        self.stdout.write(
            f"  {len(publisher_concepts)} publisher(s) tracked, "
            f"{len(blacklisted_publishers)} blacklisted."
        )

        # Step 5: For blacklisted publishers, flag remaining games (with concept shield)
        if blacklisted_publishers:
            self.stdout.write(
                "\nStep 5: Flagging games from blacklisted publishers (with concept shield)..."
            )
            for pub_name in blacklisted_publishers:
                concepts = Concept.objects.filter(
                    publisher_name=pub_name,
                    games__isnull=False,
                ).distinct()

                shielded = 0
                extra_flagged = 0

                for concept in concepts:
                    if concept.concept_id in flagged_concepts:
                        continue  # Already flagged in Step 3

                    if ShovelwareDetectionService._concept_is_shielded(concept):
                        shielded += 1
                        if verbose:
                            self.stdout.write(
                                f"  [SHIELD] {pub_name} / {concept.concept_id}: "
                                f"concept shielded (no 80%+ plat rate)"
                            )
                        continue

                    if not dry_run:
                        count = Game.objects.filter(
                            concept=concept,
                            shovelware_lock=False,
                            shovelware_status='clean',
                        ).exclude(
                            shovelware_status='manually_flagged',
                        ).update(shovelware_status='auto_flagged', shovelware_updated_at=now)
                    else:
                        count = Game.objects.filter(
                            concept=concept,
                            shovelware_lock=False,
                            shovelware_status='clean',
                        ).exclude(
                            shovelware_status='manually_flagged',
                        ).count()

                    extra_flagged += count

                if extra_flagged > 0 or shielded > 0:
                    self.stdout.write(
                        f"  {pub_name}: {extra_flagged} additional game(s) flagged, "
                        f"{shielded} concept(s) shielded."
                    )
        else:
            self.stdout.write("\nStep 5: No blacklisted publishers. Skipping.")

        # Summary
        total_flagged = Game.objects.filter(
            shovelware_status__in=['auto_flagged', 'manually_flagged'],
        ).count()
        total_clean = Game.objects.filter(shovelware_status='clean').count()
        total_locked = Game.objects.filter(shovelware_lock=True).count()

        self.stdout.write(self.style.SUCCESS(
            f"\nRebuild complete!"
            f"\n  Flagged: {total_flagged}"
            f"\n  Clean: {total_clean}"
            f"\n  Locked: {total_locked}"
            f"\n  Blacklisted publishers: {len(blacklisted_publishers)}"
        ))
