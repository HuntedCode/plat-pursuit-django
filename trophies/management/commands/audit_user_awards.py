"""
Audit UserTitle and UserMilestone records, removing unearned ones.

Three audit phases:
  1. Orphaned badge-sourced UserTitles (badge no longer earned)
  2. Orphaned milestone-sourced UserTitles (milestone no longer earned)
  3. UserMilestone re-validation (handler says user no longer qualifies)

Dry-run by default. Pass --commit to actually make changes.

Usage:
    python manage.py audit_user_awards                          # Preview all, all users
    python manage.py audit_user_awards --commit                 # Apply changes
    python manage.py audit_user_awards --username Jlowe         # Single user
    python manage.py audit_user_awards --include-premium        # Also audit premium-only milestones
    python manage.py audit_user_awards --type titles            # Only audit UserTitle records
    python manage.py audit_user_awards --type milestones        # Only audit UserMilestone records
"""
import logging
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db.models import Exists, F, OuterRef

from trophies.models import (
    Milestone,
    Profile,
    UserBadge,
    UserMilestone,
    UserTitle,
)

logger = logging.getLogger("psn_api")


class Command(BaseCommand):
    help = 'Audit UserTitle and UserMilestone records, removing unearned ones.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--commit',
            action='store_true',
            help='Apply changes. Without this flag, runs in dry-run mode.',
        )
        parser.add_argument(
            '--username',
            type=str,
            help='Audit a single profile by PSN username.',
        )
        parser.add_argument(
            '--include-premium',
            action='store_true',
            help='Also re-check premium-only milestones for non-premium users.',
        )
        parser.add_argument(
            '--type',
            type=str,
            choices=['titles', 'milestones', 'all'],
            default='all',
            help='Which record types to audit (default: all).',
        )

    def handle(self, *args, **options):
        commit = options['commit']
        username = options['username']
        include_premium = options['include_premium']
        audit_type = options['type']

        if not commit:
            self.stdout.write(self.style.WARNING(
                'DRY RUN MODE: no changes will be made. Pass --commit to apply.\n'
            ))

        # Resolve profile filter
        profile = None
        if username:
            try:
                profile = Profile.objects.get(psn_username=username)
                self.stdout.write(f'Auditing single profile: {username}\n')
            except Profile.DoesNotExist:
                self.stdout.write(self.style.ERROR(
                    f'Error: profile "{username}" does not exist.'
                ))
                return

        # Track totals
        totals = {
            'badge_titles_removed': 0,
            'milestone_titles_removed': 0,
            'milestones_removed': 0,
            'milestone_titles_cascade': 0,
            'profiles_affected': set(),
        }

        if audit_type in ('titles', 'all'):
            self._audit_badge_titles(commit, profile, totals)
            self._audit_milestone_titles(commit, profile, totals)

        if audit_type in ('milestones', 'all'):
            self._audit_milestones(commit, profile, include_premium, totals)

        # Summary
        self.stdout.write('\n' + '=' * 60)
        self.stdout.write(self.style.SUCCESS('Audit complete!'))
        total_titles = (
            totals['badge_titles_removed']
            + totals['milestone_titles_removed']
            + totals['milestone_titles_cascade']
        )
        self.stdout.write(f'Orphaned badge titles removed: {totals["badge_titles_removed"]}')
        self.stdout.write(f'Orphaned milestone titles removed: {totals["milestone_titles_removed"]}')
        self.stdout.write(f'Unearned milestones removed: {totals["milestones_removed"]}')
        self.stdout.write(f'Cascade title removals (from milestones): {totals["milestone_titles_cascade"]}')
        self.stdout.write(f'Total titles removed: {total_titles}')
        self.stdout.write(f'Profiles affected: {len(totals["profiles_affected"])}')

        if not commit and (total_titles + totals['milestones_removed']) > 0:
            self.stdout.write(self.style.WARNING(
                f'\nRun with --commit to apply these changes.'
            ))

    def _audit_badge_titles(self, commit, profile, totals):
        """Phase 1: Remove UserTitles where the source badge is no longer earned."""
        self.stdout.write(self.style.MIGRATE_HEADING(
            '--- Phase 1: Orphaned badge-sourced UserTitles ---'
        ))

        qs = UserTitle.objects.filter(source_type='badge').select_related(
            'profile', 'title'
        )
        if profile:
            qs = qs.filter(profile=profile)

        # Find titles where no matching UserBadge exists
        orphaned = qs.exclude(
            Exists(UserBadge.objects.filter(
                profile_id=OuterRef('profile_id'),
                badge_id=OuterRef('source_id'),
            ))
        )

        count = orphaned.count()
        if count == 0:
            self.stdout.write('  No orphaned badge titles found.\n')
            return

        self.stdout.write(f'  Found {count} orphaned badge title(s):')

        for ut in orphaned:
            username = ut.profile.psn_username
            displayed = ' (DISPLAYED)' if ut.is_displayed else ''
            self.stdout.write(self.style.WARNING(
                f'    {username}: "{ut.title.name}" (badge_id={ut.source_id}){displayed}'
            ))
            totals['profiles_affected'].add(ut.profile_id)
            totals['badge_titles_removed'] += 1

        if commit:
            orphaned.delete()
            self.stdout.write(self.style.SUCCESS(f'  Deleted {count} orphaned badge title(s).\n'))
        else:
            self.stdout.write('')

    def _audit_milestone_titles(self, commit, profile, totals):
        """Phase 2: Remove UserTitles where the source milestone is no longer earned."""
        self.stdout.write(self.style.MIGRATE_HEADING(
            '--- Phase 2: Orphaned milestone-sourced UserTitles ---'
        ))

        qs = UserTitle.objects.filter(source_type='milestone').select_related(
            'profile', 'title'
        )
        if profile:
            qs = qs.filter(profile=profile)

        orphaned = qs.exclude(
            Exists(UserMilestone.objects.filter(
                profile_id=OuterRef('profile_id'),
                milestone_id=OuterRef('source_id'),
            ))
        )

        count = orphaned.count()
        if count == 0:
            self.stdout.write('  No orphaned milestone titles found.\n')
            return

        self.stdout.write(f'  Found {count} orphaned milestone title(s):')

        for ut in orphaned:
            username = ut.profile.psn_username
            displayed = ' (DISPLAYED)' if ut.is_displayed else ''
            self.stdout.write(self.style.WARNING(
                f'    {username}: "{ut.title.name}" (milestone_id={ut.source_id}){displayed}'
            ))
            totals['profiles_affected'].add(ut.profile_id)
            totals['milestone_titles_removed'] += 1

        if commit:
            orphaned.delete()
            self.stdout.write(self.style.SUCCESS(
                f'  Deleted {count} orphaned milestone title(s).\n'
            ))
        else:
            self.stdout.write('')

    def _audit_milestones(self, commit, profile, include_premium, totals):
        """Phase 3: Re-validate UserMilestone records via handlers."""
        from trophies.milestone_handlers import MILESTONE_HANDLERS

        self.stdout.write(self.style.MIGRATE_HEADING(
            '--- Phase 3: UserMilestone re-validation ---'
        ))

        # Build queryset of UserMilestones to check
        qs = UserMilestone.objects.select_related(
            'profile', 'milestone', 'milestone__title'
        ).exclude(
            milestone__criteria_type='manual'  # Skip admin-granted milestones
        )
        if profile:
            qs = qs.filter(profile=profile)

        if not include_premium:
            qs = qs.exclude(milestone__premium_only=True)

        # Group by profile for efficient handler caching
        by_profile = defaultdict(list)
        for um in qs.iterator():
            by_profile[um.profile_id].append(um)

        total_checked = 0
        skipped_no_handler = 0

        for profile_id, user_milestones in by_profile.items():
            # Group by criteria_type for cache reuse
            by_type = defaultdict(list)
            for um in user_milestones:
                by_type[um.milestone.criteria_type].append(um)

            for criteria_type, ums in by_type.items():
                handler = MILESTONE_HANDLERS.get(criteria_type)
                if not handler:
                    skipped_no_handler += len(ums)
                    continue

                _cache = {}
                for um in ums:
                    total_checked += 1
                    result = handler(um.profile, um.milestone, _cache=_cache)

                    if result['achieved']:
                        continue

                    # User no longer qualifies
                    username = um.profile.psn_username
                    milestone_name = um.milestone.name
                    progress = result.get('progress', 0)
                    target = um.milestone.required_value

                    self.stdout.write(self.style.WARNING(
                        f'    {username}: "{milestone_name}" '
                        f'(progress={progress}/{target}, no longer qualifies)'
                    ))
                    totals['profiles_affected'].add(um.profile_id)
                    totals['milestones_removed'] += 1

                    if commit:
                        # Remove associated UserTitle if milestone has a title
                        if um.milestone.title:
                            deleted_count, _ = UserTitle.objects.filter(
                                profile=um.profile,
                                source_type='milestone',
                                source_id=um.milestone_id,
                            ).delete()
                            if deleted_count:
                                totals['milestone_titles_cascade'] += deleted_count
                                self.stdout.write(
                                    f'      Removed title: "{um.milestone.title.name}"'
                                )

                        # Decrement earned_count (with floor of 0)
                        Milestone.objects.filter(
                            pk=um.milestone_id, earned_count__gt=0
                        ).update(earned_count=F('earned_count') - 1)

                        # Delete the UserMilestone
                        um.delete()

        if total_checked == 0 and skipped_no_handler == 0:
            self.stdout.write('  No UserMilestone records to audit.\n')
        else:
            self.stdout.write(
                f'\n  Checked {total_checked} UserMilestone record(s).'
            )
            if skipped_no_handler:
                self.stdout.write(
                    f'  Skipped {skipped_no_handler} (no handler for criteria type).'
                )
            self.stdout.write('')
