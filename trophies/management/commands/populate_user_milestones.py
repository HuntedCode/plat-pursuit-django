from django.core.management.base import BaseCommand
from trophies.models import Profile
from trophies.utils import check_all_milestones_for_user

class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument('--type', type=str, required=False, help="Criteria type.")

    def handle(self, *args, **options):
        criteria_type = options['type']
        for profile in Profile.objects.all():
            if criteria_type:
                check_all_milestones_for_user(profile=profile, criteria_type=criteria_type)
                continue
            check_all_milestones_for_user(profile=profile)