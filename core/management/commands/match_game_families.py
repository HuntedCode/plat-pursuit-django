from django.core.management.base import BaseCommand

from core.services.game_family_service import find_matches


class Command(BaseCommand):
    help = "Find and group related Concepts into GameFamily records"

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Print what would happen without creating anything',
        )
        parser.add_argument(
            '--auto-only',
            action='store_true',
            help='Only process high-confidence matches (skip proposal creation)',
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Print detailed match reasoning',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        auto_only = options['auto_only']
        verbose = options['verbose']

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — no changes will be made\n"))

        self.stdout.write("Scanning concepts for game family matches...\n")

        stats = find_matches(
            dry_run=dry_run,
            auto_only=auto_only,
            verbose=verbose,
        )

        self.stdout.write(f"\nScanned {stats['total_concepts']} concepts")
        self.stdout.write(f"  - {stats['auto_created']} high-confidence → {'would auto-create' if dry_run else 'auto-created'} GameFamilies")
        self.stdout.write(f"  - {stats['proposals_created']} medium-confidence → {'would create' if dry_run else 'created'} proposals for admin review")
        self.stdout.write(f"  - {stats['skipped']} skipped (already grouped or duplicate proposals)")
        self.stdout.write(self.style.SUCCESS("\nDone."))
