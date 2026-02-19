from django.core.management.base import BaseCommand
from trophies.models import Profile
from trophies.services.milestone_service import check_all_milestones_for_user


class Command(BaseCommand):
    help = 'Check and award milestones for existing users.'

    def add_arguments(self, parser):
        parser.add_argument('--type', type=str, required=False, help="Criteria type to check.")
        parser.add_argument('--username', type=str, required=False, help="Single profile username to check.")
        parser.add_argument(
            '--silent',
            action='store_true',
            help='Suppress in-app notifications.',
        )

    def handle(self, *args, **options):
        criteria_type = options['type']
        username = options['username']
        silent = options['silent']

        notify_webapp = not silent

        if silent:
            self.stdout.write(self.style.WARNING('Silent mode: notifications suppressed.'))

        if username:
            self._process_single(username, criteria_type, notify_webapp)
        else:
            self._process_all(criteria_type, notify_webapp)

    def _process_single(self, username, criteria_type, notify_webapp):
        try:
            profile = Profile.objects.get(psn_username=username)
        except Profile.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"Error: profile with name {username} does not exist."))
            return

        awarded = check_all_milestones_for_user(
            profile=profile,
            criteria_type=criteria_type,
            notify_webapp=notify_webapp,
        )
        count = len(awarded) if awarded else 0
        self.stdout.write(self.style.SUCCESS(f"Profile {username}: {count} milestone(s) awarded."))

    def _process_all(self, criteria_type, notify_webapp):
        total_awarded = 0
        profiles_checked = 0

        for profile in Profile.objects.all().iterator():
            awarded = check_all_milestones_for_user(
                profile=profile,
                criteria_type=criteria_type,
                notify_webapp=notify_webapp,
            )
            count = len(awarded) if awarded else 0
            total_awarded += count
            profiles_checked += 1

            if count > 0:
                self.stdout.write(f"  {profile.psn_username}: {count} milestone(s) awarded")

        self.stdout.write(self.style.SUCCESS(
            f"Done. Checked {profiles_checked} profiles, awarded {total_awarded} total milestone(s)."
        ))
