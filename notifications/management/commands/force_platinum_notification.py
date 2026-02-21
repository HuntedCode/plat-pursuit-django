"""
Force create a platinum notification by directly calling the signal handler.
This bypasses the normal signal flow to test if the handler works.

Usage: python manage.py force_platinum_notification
"""
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from trophies.models import EarnedTrophy, Trophy, Profile
from django.utils import timezone
import logging

logger = logging.getLogger(__name__)
User = get_user_model()


class Command(BaseCommand):
    help = 'Force create a platinum notification by simulating signal'

    def handle(self, *args, **options):
        # Get first superuser
        user = User.objects.filter(is_superuser=True).first()
        if not user:
            self.stdout.write(self.style.ERROR('No superuser found'))
            return

        profile = Profile.objects.filter(user=user).first()
        if not profile:
            self.stdout.write(self.style.ERROR('No profile found'))
            return

        # Find an earned platinum trophy
        earned_plat = EarnedTrophy.objects.filter(
            profile=profile,
            trophy__trophy_type='platinum',
            earned=True,
        ).exclude(
            trophy__game__shovelware_status__in=['auto_flagged', 'manually_flagged'],
        ).first()

        if not earned_plat:
            self.stdout.write(self.style.ERROR('No earned platinum trophy found'))
            return

        self.stdout.write(f'Found platinum: {earned_plat.trophy.trophy_name} from {earned_plat.trophy.game.title_name}')
        self.stdout.write(f'Game ID: {earned_plat.trophy.game.id}')
        self.stdout.write('\nCalling signal handler directly...\n')

        # Import and call the signal handler directly
        from notifications.signals import notify_platinum_earned

        try:
            notify_platinum_earned(
                sender=EarnedTrophy,
                instance=earned_plat,
                created=False,  # Existing trophy
                raw=False,
                using='default',
                update_fields=None
            )

            self.stdout.write(self.style.SUCCESS('\n✅ Signal handler executed!'))
            self.stdout.write('Check /notifications/ to see if notification was created.')
            self.stdout.write('Check Django logs for [SIGNAL] messages.')

        except Exception as e:
            self.stdout.write(self.style.ERROR(f'\n❌ Error: {e}'))
            import traceback
            traceback.print_exc()
