"""
Management command to create a test platinum notification with enhanced metadata.
Useful for testing the enhanced notification detail view.

Usage: python manage.py create_test_notification
"""
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from notifications.services.notification_service import NotificationService
from notifications.models import NotificationTemplate

User = get_user_model()


class Command(BaseCommand):
    help = 'Create a test platinum notification with enhanced metadata for the current user'

    def add_arguments(self, parser):
        parser.add_argument(
            '--username',
            type=str,
            help='Username or email of the user to send notification to (defaults to first superuser)',
        )

    def handle(self, *args, **options):
        # Get user
        if options['username']:
            try:
                user = User.objects.get(username=options['username'])
            except User.DoesNotExist:
                try:
                    user = User.objects.get(email=options['username'])
                except User.DoesNotExist:
                    self.stdout.write(self.style.ERROR(f'User not found: {options["username"]}'))
                    return
        else:
            user = User.objects.filter(is_superuser=True).first()
            if not user:
                user = User.objects.first()

            if not user:
                self.stdout.write(self.style.ERROR('No users found in database'))
                return

        self.stdout.write(f'Creating test notification for user: {user.email}')

        # Get or create platinum template
        template, created = NotificationTemplate.objects.get_or_create(
            name='platinum_earned',
            defaults={
                'notification_type': 'platinum_earned',
                'title_template': '🏆 Platinum Unlocked!',
                'message_template': 'Congratulations {username}! You\'ve earned the platinum trophy for {game_name}!',
                'icon': '🏆',
                'action_url_template': '/game/{game_id}/',
                'action_text': 'View Game',
                'auto_trigger_enabled': True,
                'trigger_event': 'platinum_trophy_earned',
                'priority': 'high',
            }
        )

        if created:
            self.stdout.write(self.style.SUCCESS('Created platinum_earned template'))

        # Create notification with enhanced metadata
        notification = NotificationService.create_from_template(
            recipient=user,
            template=template,
            context={
                'username': user.email,
                'game_name': 'Elden Ring',
                'game_id': 999999,
                # Enhanced metadata fields
                'trophy_detail': 'Become the Elden Lord and restore order to the Lands Between. Obtain all trophies.',
                'trophy_earn_rate': 5.2,
                'trophy_rarity': 0,  # 0 = Ultra Rare
                'trophy_icon_url': 'https://image.api.playstation.com/vulcan/ap/rnd/202110/2000/aGhopp3MHppi7kooGE2Dnt8C.png',
                'game_image': 'https://image.api.playstation.com/vulcan/ap/rnd/202110/2000/aGhopp3MHppi7kooGE2Dnt8C.png',
                'rarity_label': 'Ultra Rare',
            }
        )

        self.stdout.write(self.style.SUCCESS(f'✅ Created test notification with ID: {notification.id}'))
        self.stdout.write(self.style.SUCCESS(f'View it at: /notifications/'))
        self.stdout.write(f'Notification metadata: {notification.metadata}')
