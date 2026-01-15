from django.core.management.base import BaseCommand
from django.db import transaction
from trophies.models import UserBadge, UserMilestone, UserTitle


class Command(BaseCommand):
    help = "Backfill UserTitle records for existing badge and milestone awards."

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help="Show what would be created without making changes."
        )
        parser.add_argument(
            '--badges-only',
            action='store_true',
            help="Only process badge titles."
        )
        parser.add_argument(
            '--milestones-only',
            action='store_true',
            help="Only process milestone titles."
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        badges_only = options['badges_only']
        milestones_only = options['milestones_only']

        badge_titles_created = 0
        milestone_titles_created = 0

        if not milestones_only:
            badge_titles_created = self.process_badges(dry_run)

        if not badges_only:
            milestone_titles_created = self.process_milestones(dry_run)

        total = badge_titles_created + milestone_titles_created
        action = "Would create" if dry_run else "Created"

        self.stdout.write(self.style.SUCCESS(
            f"{action} {total} UserTitle records "
            f"({badge_titles_created} from badges, {milestone_titles_created} from milestones)"
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

    def process_milestones(self, dry_run):
        """Create UserTitle records for all UserMilestones with associated titles."""
        created_count = 0

        user_milestones = UserMilestone.objects.select_related(
            'profile', 'milestone', 'milestone__title'
        ).filter(milestone__title__isnull=False)

        self.stdout.write(f"Processing {user_milestones.count()} milestone awards with titles...")

        for user_milestone in user_milestones:
            profile = user_milestone.profile
            milestone = user_milestone.milestone
            title = milestone.title

            if dry_run:
                exists = UserTitle.objects.filter(
                    profile=profile, title=title
                ).exists()
                if not exists:
                    created_count += 1
                    self.stdout.write(
                        f"  [DRY RUN] Would create: {profile.psn_username} <- {title.name} (milestone: {milestone.name})"
                    )
            else:
                _, created = UserTitle.objects.get_or_create(
                    profile=profile,
                    title=title,
                    defaults={
                        'source_type': 'milestone',
                        'source_id': milestone.id
                    }
                )
                if created:
                    created_count += 1
                    self.stdout.write(
                        f"  Created: {profile.psn_username} <- {title.name} (milestone: {milestone.name})"
                    )

        return created_count
