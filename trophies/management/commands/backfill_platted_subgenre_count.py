"""
Backfill platted_subgenre_count for existing genre challenges and revoke
incorrectly awarded subgenre_progress milestones.

One-time command to fix milestones that were awarded based on assigned (not
platted) subgenres. For each affected user:
  1. Updates Challenge.platted_subgenre_count to the correct value
  2. Removes UserMilestone rows the user no longer qualifies for
  3. Removes associated UserTitle rows
  4. Removes associated milestone notifications
  5. Decrements Milestone.earned_count

Usage:
    python manage.py backfill_platted_subgenre_count --dry-run
    python manage.py backfill_platted_subgenre_count
"""
import logging

from django.db.models import F
from django.core.management.base import BaseCommand

from notifications.models import Notification
from trophies.models import Challenge, Milestone, UserMilestone, UserMilestoneProgress, UserTitle
from trophies.services.challenge_service import get_platted_subgenres

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Backfill platted_subgenre_count and revoke unearned subgenre milestones'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Show what would change without saving',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        prefix = '[DRY RUN] ' if dry_run else ''

        # ── Step 1: Backfill platted_subgenre_count ───────────────────
        self.stdout.write(self.style.MIGRATE_HEADING('\nStep 1: Backfill platted_subgenre_count'))

        challenges = Challenge.objects.filter(
            challenge_type='genre', is_deleted=False,
        ).select_related('profile').prefetch_related(
            'genre_slots__concept', 'bonus_slots__concept',
        )

        updated_count = 0
        for challenge in challenges:
            platted_count = len(get_platted_subgenres(challenge))
            if challenge.platted_subgenre_count != platted_count:
                self.stdout.write(
                    f"  {challenge.profile.psn_username} - "
                    f"'{challenge.name}': "
                    f"{challenge.platted_subgenre_count} -> {platted_count}"
                )
                if not dry_run:
                    challenge.platted_subgenre_count = platted_count
                    challenge.save(update_fields=['platted_subgenre_count'])
                updated_count += 1

        self.stdout.write(self.style.SUCCESS(
            f"  {prefix}Updated {updated_count} of {challenges.count()} genre challenges."
        ))

        # ── Step 2: Revoke unearned subgenre_progress milestones ──────
        self.stdout.write(self.style.MIGRATE_HEADING('\nStep 2: Revoke unearned subgenre milestones'))

        subgenre_milestones = list(
            Milestone.objects.filter(criteria_type='subgenre_progress')
            .order_by('required_value')
        )
        if not subgenre_milestones:
            self.stdout.write('  No subgenre_progress milestones found.')
            return

        # Build a map of profile -> best platted_subgenre_count
        # (across all their genre challenges)
        profile_best = {}
        for challenge in challenges:
            pid = challenge.profile_id
            current_best = profile_best.get(pid, 0)
            if challenge.platted_subgenre_count > current_best:
                profile_best[pid] = challenge.platted_subgenre_count

        # Find all UserMilestones for subgenre_progress milestones
        user_milestones = UserMilestone.objects.filter(
            milestone__in=subgenre_milestones,
        ).select_related('profile', 'milestone')

        revoked_milestones = 0
        revoked_titles = 0
        revoked_notifications = 0

        for um in user_milestones:
            best = profile_best.get(um.profile_id, 0)
            target = um.milestone.required_value

            if best >= target:
                continue  # Legitimately earned

            self.stdout.write(
                f"  REVOKE: {um.profile.psn_username} - "
                f"'{um.milestone.name}' (target={target}, actual={best})"
            )

            if not dry_run:
                # Delete associated title
                title_deleted, _ = UserTitle.objects.filter(
                    profile=um.profile,
                    source_type='milestone',
                    source_id=um.milestone_id,
                ).delete()
                revoked_titles += title_deleted

                # Delete associated notification
                notif_deleted, _ = Notification.objects.filter(
                    recipient=um.profile.user,
                    notification_type='milestone_achieved',
                    metadata__milestone_id=um.milestone_id,
                ).delete()
                revoked_notifications += notif_deleted

                # Decrement earned_count
                Milestone.objects.filter(pk=um.milestone_id).update(
                    earned_count=F('earned_count') - 1,
                )

                # Update progress value to actual count
                UserMilestoneProgress.objects.filter(
                    profile=um.profile,
                    milestone=um.milestone,
                ).update(progress_value=best)

                # Delete the UserMilestone
                um.delete()

            revoked_milestones += 1

        if dry_run:
            self.stdout.write(self.style.SUCCESS(
                f"  {prefix}Would revoke {revoked_milestones} milestone awards."
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"  Revoked {revoked_milestones} milestones, "
                f"{revoked_titles} titles, "
                f"{revoked_notifications} notifications."
            ))
