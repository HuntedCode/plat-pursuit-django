from django.core.management.base import BaseCommand
from django.db import transaction
from trophies.models import UserBadge, UserTitle


class Command(BaseCommand):
    help = "Backfill UserTitle records for existing badge awards."

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help="Show what would be created without making changes."
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        badge_titles_created = self.process_badges(dry_run)
        action = "Would create" if dry_run else "Created"

        self.stdout.write(self.style.SUCCESS(
            f"{action} {badge_titles_created} UserTitle record(s) from badges"
        ))

    def process_badges(self, dry_run):
        """Create UserTitle records for all UserBadges with associated titles."""
        created_count = 0

        user_badges = UserBadge.objects.select_related(
            'profile', 'badge', 'badge__title'
        ).filter(badge__title__isnull=False)

        self.stdout.write(f"Processing {user_badges.count()} badge awards with titles...")

        for user_badge in user_badges:
            profile = user_badge.profile
            badge = user_badge.badge
            title = badge.title

            if dry_run:
                exists = UserTitle.objects.filter(
                    profile=profile, title=title
                ).exists()
                if not exists:
                    created_count += 1
                    self.stdout.write(
                        f"  [DRY RUN] Would create: {profile.psn_username} <- {title.name} (badge: {badge.name})"
                    )
            else:
                _, created = UserTitle.objects.get_or_create(
                    profile=profile,
                    title=title,
                    defaults={
                        'source_type': 'badge',
                        'source_id': badge.id
                    }
                )
                if created:
                    created_count += 1
                    self.stdout.write(
                        f"  Created: {profile.psn_username} <- {title.name} (badge: {badge.name})"
                    )

        return created_count

