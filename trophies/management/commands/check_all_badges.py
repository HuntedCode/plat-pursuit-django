import logging
import time

from django.core.management.base import BaseCommand
from django.db import transaction

from trophies.models import Badge, Profile, UserBadge
from trophies.services.badge_service import initial_badge_check

logger = logging.getLogger("psn_api")


class Command(BaseCommand):
    help = 'Run a full badge recheck for all profiles or a specific user.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--username',
            type=str,
            help='Recheck badges for a single profile by PSN username.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview what badges would be awarded/revoked without making changes.',
        )

    def handle(self, *args, **options):
        username = options.get('username')
        dry_run = options.get('dry_run', False)

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN: no changes will be made.\n'))

        if username:
            try:
                profiles = [Profile.objects.get(psn_username__iexact=username)]
                self.stdout.write(f'Checking badges for: {profiles[0].psn_username}\n')
            except Profile.DoesNotExist:
                self.stdout.write(self.style.ERROR(f'Profile "{username}" not found.'))
                return
        else:
            profiles = list(
                Profile.objects.filter(played_games__isnull=False).distinct()
            )
            self.stdout.write(f'Checking badges for {len(profiles)} profiles...\n')

        badge_names = dict(Badge.objects.values_list('id', 'name'))
        total_awarded = 0
        total_revoked = 0
        errors = 0
        start_time = time.time()

        for i, profile in enumerate(profiles, 1):
            before_ids = set(
                UserBadge.objects.filter(profile=profile)
                .values_list('badge_id', flat=True)
            )

            sid = transaction.savepoint() if dry_run else None

            try:
                initial_badge_check(profile, discord_notify=False)
            except Exception as e:
                logger.exception(f'Error checking badges for {profile.psn_username}')
                self.stdout.write(self.style.ERROR(
                    f'[{i}/{len(profiles)}] {profile.psn_username} - Error: {e}'
                ))
                errors += 1
                if sid:
                    transaction.savepoint_rollback(sid)
                continue

            after_ids = set(
                UserBadge.objects.filter(profile=profile)
                .values_list('badge_id', flat=True)
            )

            awarded = after_ids - before_ids
            revoked = before_ids - after_ids

            if dry_run and sid:
                transaction.savepoint_rollback(sid)

            if awarded or revoked:
                self.stdout.write(f'[{i}/{len(profiles)}] {profile.psn_username}:')
                for badge_id in awarded:
                    name = badge_names.get(badge_id, f'Badge #{badge_id}')
                    prefix = 'WOULD AWARD' if dry_run else 'AWARDED'
                    self.stdout.write(self.style.SUCCESS(f'  + {prefix}: {name}'))
                for badge_id in revoked:
                    name = badge_names.get(badge_id, f'Badge #{badge_id}')
                    prefix = 'WOULD REVOKE' if dry_run else 'REVOKED'
                    self.stdout.write(self.style.WARNING(f'  - {prefix}: {name}'))
            else:
                self.stdout.write(f'[{i}/{len(profiles)}] {profile.psn_username} - no changes')

            total_awarded += len(awarded)
            total_revoked += len(revoked)

        duration = time.time() - start_time
        self.stdout.write(f'\n{"=" * 50}')
        self.stdout.write(self.style.SUCCESS(f'Done in {duration:.1f}s'))
        self.stdout.write(f'Profiles checked: {len(profiles)}')
        self.stdout.write(f'Badges {"would be " if dry_run else ""}awarded: {total_awarded}')
        self.stdout.write(f'Badges {"would be " if dry_run else ""}revoked: {total_revoked}')
        if errors:
            self.stdout.write(self.style.ERROR(f'Errors: {errors}'))
