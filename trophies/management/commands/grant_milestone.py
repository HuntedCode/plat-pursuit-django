from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import F

from trophies.models import Milestone, Profile, UserMilestone, UserTitle
from trophies.services.badge_service import notify_bot_role_earned
from notifications.signals import create_milestone_notification


class Command(BaseCommand):
    help = 'Grant a milestone (with all side effects) to one or more users.'

    def add_arguments(self, parser):
        parser.add_argument('milestone', type=str, help='Milestone name to grant.')
        parser.add_argument('--username', type=str, help='Single PSN username.')
        parser.add_argument('--usernames', type=str, help='Comma-separated PSN usernames.')
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview without writing to the database.',
        )
        parser.add_argument(
            '--silent',
            action='store_true',
            help='Suppress in-app notifications.',
        )

    def handle(self, *args, **options):
        milestone_name = options['milestone']
        dry_run = options['dry_run']
        silent = options['silent']

        # Collect usernames
        usernames = []
        if options['username']:
            usernames.append(options['username'])
        if options['usernames']:
            usernames.extend(u.strip() for u in options['usernames'].split(',') if u.strip())

        usernames = [u.lower() for u in dict.fromkeys(usernames)]

        if not usernames:
            self.stderr.write(self.style.ERROR('Provide --username or --usernames.'))
            return

        # Look up milestone
        try:
            milestone = Milestone.objects.select_related('title').get(name=milestone_name)
        except Milestone.DoesNotExist:
            self.stderr.write(self.style.ERROR(f'Milestone "{milestone_name}" not found.'))
            return

        if dry_run:
            self.stdout.write(self.style.WARNING('Dry-run mode: no changes will be made.'))
        if silent:
            self.stdout.write(self.style.WARNING('Silent mode: notifications suppressed.'))

        self.stdout.write(f'Milestone: {milestone.name} (criteria_type={milestone.criteria_type})')
        if milestone.title:
            self.stdout.write(f'Title: {milestone.title.title_text}')

        granted = 0
        skipped = 0
        errors = 0

        for username in usernames:
            try:
                profile = Profile.objects.get(psn_username=username)
            except Profile.DoesNotExist:
                self.stdout.write(self.style.ERROR(f'  {username}: profile not found'))
                errors += 1
                continue

            if dry_run:
                already = UserMilestone.objects.filter(profile=profile, milestone=milestone).exists()
                if already:
                    self.stdout.write(f'  {username}: already granted (would skip)')
                    skipped += 1
                else:
                    self.stdout.write(self.style.SUCCESS(f'  {username}: would grant'))
                    granted += 1
                continue

            result = self._grant(profile, milestone, silent)
            if result == 'granted':
                self.stdout.write(self.style.SUCCESS(f'  {username}: granted'))
                granted += 1
            elif result == 'skipped':
                self.stdout.write(f'  {username}: already granted')
                skipped += 1

        self.stdout.write('')
        label = 'Would grant' if dry_run else 'Granted'
        self.stdout.write(self.style.SUCCESS(
            f'Done. {label}: {granted}, Already had: {skipped}, Errors: {errors}'
        ))

    def _grant(self, profile, milestone, silent):
        with transaction.atomic():
            user_milestone, created = UserMilestone.objects.get_or_create(
                profile=profile,
                milestone=milestone,
            )

            if not created:
                return 'skipped'

            Milestone.objects.filter(pk=milestone.pk).update(
                earned_count=F('earned_count') + 1,
            )

            if milestone.title:
                UserTitle.objects.get_or_create(
                    profile=profile,
                    title=milestone.title,
                    defaults={'source_type': 'milestone', 'source_id': milestone.pk},
                )

            if (milestone.discord_role_id
                    and profile.is_discord_verified
                    and profile.discord_id):
                transaction.on_commit(
                    lambda p=profile, r=milestone.discord_role_id:
                        notify_bot_role_earned(p, r)
                )

        if not silent:
            create_milestone_notification(user_milestone)

        return 'granted'
