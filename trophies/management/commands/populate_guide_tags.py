from django.core.management.base import BaseCommand
from trophies.models import GuideTag


class Command(BaseCommand):
    help = "Populate initial guide tags for categorizing guides."

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help="Show what would be created without making changes."
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        # Guide Type Tags
        type_tags = [
            ('Trophy Roadmap', 'trophy-roadmap', 1),
            ('100% Walkthrough', '100-walkthrough', 2),
            ('Collectibles Guide', 'collectibles', 3),
            ('Missables Guide', 'missables', 4),
            ('Speedrun Guide', 'speedrun', 5),
            ('DLC Guide', 'dlc', 6),
            ('Online Trophies', 'online-trophies', 7),
            ('Tips & Tricks', 'tips-tricks', 8),
        ]

        created = 0
        for name, slug, order in type_tags:
            if dry_run:
                self.stdout.write(f"  [DRY RUN] Would create: {name}")
            else:
                _, was_created = GuideTag.objects.get_or_create(
                    slug=slug,
                    defaults={'name': name, 'display_order': order}
                )
                if was_created:
                    created += 1
                    self.stdout.write(f"  Created: {name}")

        self.stdout.write(self.style.SUCCESS(f"Created {created} guide tags"))
