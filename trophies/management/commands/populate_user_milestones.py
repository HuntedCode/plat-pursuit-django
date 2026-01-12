from django.core.management.base import BaseCommand
from trophies.models import Profile
from trophies.utils import check_all_milestones_for_user

class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument('--type', type=str, required=False, help="Criteria type.")
        parser.add_argument('--username', type=str, required=False, help="Profile username to check.")

    def handle(self, *args, **options):
        criteria_type = options['type']
        username = options['username']

        if username:
            try:
                profile = Profile.objects.get(psn_username=username)
            except Profile.DoesNotExist:
                self.stdout.write(self.style.ERROR(f"Error: profile with name {username} does not exist."))
                return
            if criteria_type:
                check_all_milestones_for_user(profile=profile, criteria_type=criteria_type)
            else:
                check_all_milestones_for_user(profile=profile)
            self.stdout.write(self.style.SUCCESS(f"Profile {username} updated successfully!"))
            return


        for profile in Profile.objects.all():
            if criteria_type:
                check_all_milestones_for_user(profile=profile, criteria_type=criteria_type)
                continue
            check_all_milestones_for_user(profile=profile)
        self.stdout.write(self.style.SUCCESS('Profiles updated successfully!'))