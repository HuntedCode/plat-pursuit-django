"""
Management command to create test notifications with enhanced metadata.
Useful for testing the notification detail views in the inbox.

Usage:
    python manage.py create_test_notification
    python manage.py create_test_notification --type platinum
    python manage.py create_test_notification --type challenge
    python manage.py create_test_notification --type challenge --username myuser
"""
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from notifications.services.notification_service import NotificationService
from notifications.models import NotificationTemplate

User = get_user_model()

NOTIFICATION_TYPES = ['platinum', 'challenge']


class Command(BaseCommand):
    help = 'Create a test notification with enhanced metadata for a user'

    def add_arguments(self, parser):
        parser.add_argument(
            '--username',
            type=str,
            help='Username or email of the user to send notification to (defaults to first superuser)',
        )
        parser.add_argument(
            '--type',
            type=str,
            default='platinum',
            choices=NOTIFICATION_TYPES,
            help=f'Notification type to create: {", ".join(NOTIFICATION_TYPES)} (default: platinum)',
        )

    def handle(self, *args, **options):
        user = self._get_user(options)
        if not user:
            return

        notification_type = options['type']
        self.stdout.write(f'Creating test {notification_type} notification for user: {user.username}')

        handler = getattr(self, f'_create_{notification_type}', None)
        if handler:
            handler(user)
        else:
            self.stdout.write(self.style.ERROR(f'Unknown type: {notification_type}'))

    def _get_user(self, options):
        if options['username']:
            try:
                return User.objects.get(username=options['username'])
            except User.DoesNotExist:
                try:
                    return User.objects.get(email=options['username'])
                except User.DoesNotExist:
                    self.stdout.write(self.style.ERROR(f'User not found: {options["username"]}'))
                    return None
        else:
            user = User.objects.filter(is_superuser=True).first()
            if not user:
                user = User.objects.first()
            if not user:
                self.stdout.write(self.style.ERROR('No users found in database'))
            return user

    def _create_platinum(self, user):
        """Create a test platinum earned notification."""
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

        notification = NotificationService.create_from_template(
            recipient=user,
            template=template,
            context={
                'username': user.username,
                'game_name': 'Elden Ring',
                'game_id': 999999,
                'trophy_detail': 'Become the Elden Lord and restore order to the Lands Between. Obtain all trophies.',
                'trophy_earn_rate': 5.2,
                'trophy_rarity': 0,
                'trophy_icon_url': 'https://image.api.playstation.com/vulcan/ap/rnd/202110/2000/aGhopp3MHppi7kooGE2Dnt8C.png',
                'game_image': 'https://image.api.playstation.com/vulcan/ap/rnd/202110/2000/aGhopp3MHppi7kooGE2Dnt8C.png',
                'rarity_label': 'Ultra Rare',
            }
        )

        self._print_success(notification)

    def _create_challenge(self, user):
        """Create a test challenge completed notification."""
        notification = NotificationService.create_notification(
            recipient=user,
            notification_type='challenge_completed',
            title='A-Z Challenge Complete!',
            message=f'You completed your A-Z Challenge "My Epic A-Z Challenge"! Welcome to the Hall of Fame!',
            icon='\U0001f3c6',
            action_url='/challenges/',
            action_text='View Challenge',
            metadata={
                'challenge_id': 1,
                'challenge_type': 'az',
                'challenge_name': 'My Epic A-Z Challenge',
                'completed_count': 26,
                'total_items': 26,
            },
        )

        self._print_success(notification)

    def _print_success(self, notification):
        self.stdout.write(self.style.SUCCESS(f'Created test notification with ID: {notification.id}'))
        self.stdout.write(self.style.SUCCESS(f'View it at: /notifications/'))
        self.stdout.write(f'Notification metadata: {notification.metadata}')
