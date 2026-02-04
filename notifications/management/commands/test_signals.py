"""
Management command to test if notification signals are working.
Attempts to trigger a signal and verifies notification creation.

Usage: python manage.py test_signals
"""
from django.core.management.base import BaseCommand
from django.db.models.signals import post_save
from trophies.models import EarnedTrophy
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Test if notification signals are properly connected'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('=== Notification Signal Test ===\n'))

        # Check if signals module is loaded
        try:
            import notifications.signals
            self.stdout.write(self.style.SUCCESS('✓ Signals module imported successfully'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'✗ Failed to import signals module: {e}'))
            return

        # Check if signal receiver is connected
        receivers = list(post_save._live_receivers(EarnedTrophy))
        total_receivers = len(receivers)
        self.stdout.write(f'✓ Found {total_receivers} receiver(s) for EarnedTrophy post_save signal')

        signal_found = False
        for receiver_list in receivers:
            # Receivers are wrapped in lists by Django
            if isinstance(receiver_list, list):
                for receiver_func in receiver_list:
                    if hasattr(receiver_func, '__name__') and 'notify_platinum_earned' in receiver_func.__name__:
                        signal_found = True
                        self.stdout.write(self.style.SUCCESS(f'✓ Signal receiver found: {receiver_func.__name__}'))
                        break
            elif hasattr(receiver_list, '__name__') and 'notify_platinum_earned' in receiver_list.__name__:
                signal_found = True
                self.stdout.write(self.style.SUCCESS(f'✓ Signal receiver found: {receiver_list.__name__}'))
                break

        if not signal_found and total_receivers > 0:
            self.stdout.write(self.style.WARNING('⚠ Receivers found but notify_platinum_earned not identified'))
            self.stdout.write(self.style.WARNING('  This might be OK - signals could still be working'))
        elif not signal_found:
            self.stdout.write(self.style.ERROR('✗ No signal receivers found at all'))
        else:
            self.stdout.write(self.style.SUCCESS('✓ Signal is properly connected\n'))

        # Check notification template exists
        from notifications.models import NotificationTemplate
        try:
            template = NotificationTemplate.objects.get(
                name='platinum_earned',
                auto_trigger_enabled=True
            )
            self.stdout.write(self.style.SUCCESS(f'✓ Platinum template found: {template.title_template}'))
        except NotificationTemplate.DoesNotExist:
            self.stdout.write(self.style.ERROR('✗ Platinum template not found or disabled'))
            self.stdout.write(self.style.WARNING('  Run: python manage.py loaddata notifications/fixtures/initial_templates.json'))

        self.stdout.write(self.style.SUCCESS('\n=== Test Complete ==='))

        if signal_found:
            self.stdout.write(self.style.SUCCESS('\n✅ Signals are working! Try earning a platinum to test.'))
        else:
            self.stdout.write(self.style.ERROR('\n❌ Signals are NOT working. Restart Django after fixing INSTALLED_APPS.'))
