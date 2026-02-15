"""
Enforce A-Z Challenge anti-stack rules on existing challenges.

Removes non-completed slot assignments where the game is now excluded
by the updated get_excluded_game_ids() logic (concept siblings, GameFamily
siblings of platinumed games, or >50% progress games).

Usage:
    python manage.py enforce_az_challenge_rules --dry-run
    python manage.py enforce_az_challenge_rules
"""
from django.core.management.base import BaseCommand

from trophies.models import Challenge
from trophies.services.challenge_service import (
    get_excluded_game_ids,
    recalculate_challenge_counts,
)


class Command(BaseCommand):
    help = 'Remove stacked/excluded games from active A-Z challenge slots'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be cleared without making changes',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE: no changes will be made'))

        challenges = (
            Challenge.objects.filter(
                challenge_type='az', is_deleted=False, is_complete=False,
            )
            .select_related('profile')
            .prefetch_related('az_slots__game')
        )

        total_challenges = challenges.count()
        challenges_affected = 0
        total_cleared = 0
        total_skipped_completed = 0

        self.stdout.write(f'Processing {total_challenges} active A-Z challenges...')

        for challenge in challenges:
            excluded_ids = get_excluded_game_ids(challenge.profile)
            slots_cleared = []
            skipped_completed = []

            for slot in challenge.az_slots.all():
                if not slot.game_id or slot.game_id not in excluded_ids:
                    continue

                if slot.is_completed:
                    skipped_completed.append(slot)
                    total_skipped_completed += 1
                    continue

                slots_cleared.append(slot)
                total_cleared += 1

            if not slots_cleared and not skipped_completed:
                continue

            challenges_affected += 1
            username = challenge.profile.psn_username
            self.stdout.write(f'\n  {username} - "{challenge.name}" (ID: {challenge.id})')

            for slot in slots_cleared:
                game_name = slot.game.title_name if slot.game else '?'
                action = '(would clear)' if dry_run else '(cleared)'
                self.stdout.write(
                    self.style.WARNING(f'    [{slot.letter}] {game_name} {action}')
                )
                if not dry_run:
                    slot.game = None
                    slot.assigned_at = None
                    slot.save(update_fields=['game', 'assigned_at'])

            for slot in skipped_completed:
                game_name = slot.game.title_name if slot.game else '?'
                self.stdout.write(
                    f'    [{slot.letter}] {game_name} (completed, skipped)'
                )

            if not dry_run and slots_cleared:
                recalculate_challenge_counts(challenge)

        # Summary
        self.stdout.write('\n' + '=' * 60)
        self.stdout.write(self.style.SUCCESS('Processing complete!'))
        self.stdout.write(f'Challenges processed: {total_challenges}')
        self.stdout.write(self.style.WARNING(f'Challenges affected: {challenges_affected}'))
        self.stdout.write(self.style.WARNING(f'Slots cleared: {total_cleared}'))
        self.stdout.write(f'Completed slots skipped: {total_skipped_completed}')

        if dry_run and total_cleared > 0:
            self.stdout.write(
                self.style.WARNING(
                    f'\nRun without --dry-run to apply {total_cleared} changes'
                )
            )
