"""
Management command to test if platinum signal handler is working.
Creates a test platinum trophy earning to trigger the signal.

Usage: python manage.py test_platinum_signal
"""
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from trophies.models import EarnedTrophy, Trophy, Game, Profile
from django.utils import timezone

User = get_user_model()


class Command(BaseCommand):
    help = 'Test if platinum signal handler is triggered correctly'

    def handle(self, *args, **options):
        # Get first superuser
        user = User.objects.filter(is_superuser=True).first()
        if not user:
            self.stdout.write(self.style.ERROR('No superuser found'))
            return

        profile = Profile.objects.filter(user=user).first()
        if not profile:
            self.stdout.write(self.style.ERROR('No profile found for superuser'))
            return

        # Find an existing platinum trophy (not earned yet)
        platinum_trophy = Trophy.objects.filter(
            trophy_type='platinum',
        ).exclude(
            game__shovelware_status__in=['auto_flagged', 'manually_flagged'],
        ).exclude(
            earnedtrophy__profile=profile,
            earnedtrophy__earned=True
        ).first()

        if not platinum_trophy:
            self.stdout.write(self.style.ERROR('No available platinum trophy found to test with'))
            return

        self.stdout.write(f'Testing with platinum: {platinum_trophy.trophy_name} from {platinum_trophy.game.title_name}')
        self.stdout.write('Creating/updating EarnedTrophy to trigger signal...')

        # Create or update earned trophy
        earned_trophy, created = EarnedTrophy.objects.get_or_create(
            profile=profile,
            trophy=platinum_trophy,
            defaults={
                'earned': True,
                'earned_date_time': timezone.now(),
                'trophy_hidden': False,
                'user_hidden': False,
            }
        )

        if not created:
            # Update existing
            earned_trophy.earned = True
            earned_trophy.earned_date_time = timezone.now()
            earned_trophy.save()

        self.stdout.write(self.style.SUCCESS(f'✅ EarnedTrophy {"created" if created else "updated"}'))
        self.stdout.write('Signal should have fired. Check /notifications/ to see if notification was created.')
        self.stdout.write(f'Game ID: {platinum_trophy.game.id}')
        self.stdout.write(f'Trophy: {platinum_trophy.trophy_name}')
