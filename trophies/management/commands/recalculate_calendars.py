"""
Recalculate Platinum Calendar Challenge data for all or specific users.

Performs a full reconciliation: fills missing days, unfills phantom days,
corrects plat_count values, and updates challenge progress counters.

Usage:
    python manage.py recalculate_calendars --dry-run           # Preview changes
    python manage.py recalculate_calendars                     # Fix all calendars
    python manage.py recalculate_calendars --username Jlowe    # Single user
    python manage.py recalculate_calendars --include-complete  # Include 365/365 calendars
    python manage.py recalculate_calendars --reset-hidden      # Reset stale user_hidden flags first
"""
import calendar as cal_module

from django.core.management.base import BaseCommand

from trophies.models import Challenge, CalendarChallengeDay, EarnedTrophy, Profile, ProfileGame
from trophies.services.challenge_service import (
    _reconcile_calendar_days,
    recalculate_challenge_counts,
)


class Command(BaseCommand):
    help = 'Recalculate calendar challenge data (fill/unfill/plat_count corrections)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would change without making changes',
        )
        parser.add_argument(
            '--username',
            type=str,
            default=None,
            help='PSN username to target a single user',
        )
        parser.add_argument(
            '--include-complete',
            action='store_true',
            help='Also process completed (365/365) calendars',
        )
        parser.add_argument(
            '--reset-hidden',
            action='store_true',
            help='Reset stale user_hidden flags before reconciliation (safe: next health check re-hides actually hidden games)',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        username = options['username']
        include_complete = options['include_complete']
        reset_hidden = options['reset_hidden']

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE: no changes will be made'))

        # Reset stale user_hidden flags before reconciliation
        if reset_hidden:
            profile_filter = {}
            if username:
                profile_filter['profile__psn_username__iexact'] = username

            hidden_et = EarnedTrophy.objects.filter(user_hidden=True, **profile_filter)
            hidden_pg = ProfileGame.objects.filter(user_hidden=True, **profile_filter)
            et_count = hidden_et.count()
            pg_count = hidden_pg.count()

            # Also reset Profile.total_hiddens so the health check detects mismatches
            profile_qs = Profile.objects.filter(total_hiddens__gt=0)
            if username:
                profile_qs = profile_qs.filter(psn_username__iexact=username)
            profile_count = profile_qs.count()

            if et_count or pg_count or profile_count:
                if dry_run:
                    self.stdout.write(self.style.WARNING(
                        f'Would reset user_hidden on {et_count} EarnedTrophy and {pg_count} ProfileGame rows'
                    ))
                    self.stdout.write(self.style.WARNING(
                        f'Would reset total_hiddens on {profile_count} Profile rows'
                    ))
                else:
                    hidden_et.update(user_hidden=False)
                    hidden_pg.update(user_hidden=False)
                    profile_qs.update(total_hiddens=0)
                    self.stdout.write(self.style.SUCCESS(
                        f'Reset user_hidden on {et_count} EarnedTrophy and {pg_count} ProfileGame rows'
                    ))
                    self.stdout.write(self.style.SUCCESS(
                        f'Reset total_hiddens on {profile_count} Profile rows'
                    ))
            else:
                self.stdout.write('No stale user_hidden flags found.')
            self.stdout.write('')

        qs = Challenge.objects.filter(
            challenge_type='calendar', is_deleted=False,
        ).select_related('profile', 'profile__user')

        if not include_complete:
            qs = qs.filter(is_complete=False)

        if username:
            qs = qs.filter(profile__psn_username__iexact=username)

        challenges = list(qs)
        total = len(challenges)
        self.stdout.write(f'Processing {total} {"active" if not include_complete else "active + completed"} calendar challenge(s)...\n')

        total_filled = 0
        total_unfilled = 0
        total_plat_corrections = 0
        challenges_affected = 0
        completion_changes = 0

        for challenge in challenges:
            psn_name = challenge.profile.psn_username
            old_filled_count = challenge.filled_count

            newly_filled, newly_unfilled, to_update = _reconcile_calendar_days(challenge)

            # Count plat_count-only corrections (not fill/unfill changes)
            plat_corrections = len(to_update) - newly_filled - newly_unfilled

            if not to_update:
                continue

            challenges_affected += 1
            total_filled += newly_filled
            total_unfilled += newly_unfilled
            total_plat_corrections += plat_corrections

            self.stdout.write(f'  {psn_name} - "{challenge.name}" (ID: {challenge.id})')

            if newly_unfilled:
                # Show which days are being unfilled
                for day_obj in to_update:
                    if not day_obj.is_filled and day_obj.pk:
                        # This day was filled before, now unfilled
                        month_name = cal_module.month_abbr[day_obj.month]
                        action = '(would unfill)' if dry_run else '(unfilled)'
                        self.stdout.write(
                            self.style.WARNING(f'    {month_name} {day_obj.day}: {action}')
                        )

            if newly_filled:
                for day_obj in to_update:
                    if day_obj.is_filled and day_obj.filled_at is not None:
                        # Check if this is a newly filled day (game_id just set)
                        month_name = cal_module.month_abbr[day_obj.month]
                        action = '(would fill)' if dry_run else '(filled)'
                        self.stdout.write(
                            self.style.SUCCESS(f'    {month_name} {day_obj.day}: {action}')
                        )

            if plat_corrections > 0:
                self.stdout.write(f'    plat_count corrections: {plat_corrections}')

            if not dry_run:
                CalendarChallengeDay.objects.bulk_update(
                    to_update,
                    ['is_filled', 'filled_at', 'platinum_earned_at', 'game_id', 'plat_count'],
                )
                recalculate_challenge_counts(challenge)

                # Handle completion status changes
                was_complete = challenge.is_complete
                if challenge.completed_count >= challenge.total_items and not was_complete:
                    challenge.is_complete = True
                    completion_changes += 1
                elif challenge.completed_count < challenge.total_items and was_complete:
                    challenge.is_complete = False
                    challenge.completed_at = None
                    completion_changes += 1

                challenge.save(update_fields=[
                    'filled_count', 'completed_count', 'is_complete',
                    'completed_at', 'updated_at',
                ])

            new_count = challenge.filled_count if not dry_run else old_filled_count - newly_unfilled + newly_filled
            self.stdout.write(f'    Progress: {old_filled_count}/365 -> {new_count}/365\n')

        # Summary
        self.stdout.write('=' * 60)
        self.stdout.write(self.style.SUCCESS('Processing complete!'))
        self.stdout.write(f'Challenges processed: {total}')
        self.stdout.write(self.style.WARNING(f'Challenges affected: {challenges_affected}'))
        self.stdout.write(self.style.SUCCESS(f'Days filled: {total_filled}'))
        self.stdout.write(self.style.WARNING(f'Days unfilled: {total_unfilled}'))
        self.stdout.write(f'plat_count corrections: {total_plat_corrections}')
        if completion_changes:
            self.stdout.write(self.style.WARNING(f'Completion status changes: {completion_changes}'))

        if dry_run and (total_filled or total_unfilled or total_plat_corrections):
            self.stdout.write(
                self.style.WARNING(
                    f'\nRun without --dry-run to apply changes'
                )
            )
