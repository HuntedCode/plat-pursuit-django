"""
Debug command to understand why signals aren't connecting.
"""
from django.core.management.base import BaseCommand
from django.db.models.signals import post_save


class Command(BaseCommand):
    help = 'Debug signal connection issues'

    def handle(self, *args, **options):
        self.stdout.write(self.style.WARNING('=== Signal Debug ===\n'))

        # Try to import the signals module
        self.stdout.write('Attempting to import notifications.signals...')
        try:
            import notifications.signals as sig_module
            self.stdout.write(self.style.SUCCESS('✓ Module imported\n'))

            # Check what's in the module
            self.stdout.write('Module attributes:')
            for attr in dir(sig_module):
                if not attr.startswith('_'):
                    self.stdout.write(f'  - {attr}')

            # Try to find the function
            if hasattr(sig_module, 'notify_platinum_earned'):
                self.stdout.write(self.style.SUCCESS('\n✓ notify_platinum_earned function exists'))
                func = getattr(sig_module, 'notify_platinum_earned')
                self.stdout.write(f'  Type: {type(func)}')
                self.stdout.write(f'  Callable: {callable(func)}')
            else:
                self.stdout.write(self.style.ERROR('\n✗ notify_platinum_earned function NOT found'))

        except Exception as e:
            self.stdout.write(self.style.ERROR(f'✗ Failed to import: {e}'))
            import traceback
            traceback.print_exc()
            return

        # Check registered receivers
        self.stdout.write('\n\nChecking post_save signal receivers:')
        from trophies.models import EarnedTrophy

        receivers = post_save._live_receivers(EarnedTrophy)
        self.stdout.write(f'Total receivers for EarnedTrophy: {len(list(receivers))}')

        for idx, receiver in enumerate(post_save._live_receivers(EarnedTrophy)):
            self.stdout.write(f'\nReceiver {idx + 1}:')
            self.stdout.write(f'  Type: {type(receiver)}')
            if hasattr(receiver, '__name__'):
                self.stdout.write(f'  Name: {receiver.__name__}')
            if hasattr(receiver, '__module__'):
                self.stdout.write(f'  Module: {receiver.__module__}')

            # Check if it's our function
            if hasattr(sig_module, 'notify_platinum_earned'):
                if receiver == getattr(sig_module, 'notify_platinum_earned'):
                    self.stdout.write(self.style.SUCCESS('  ✓ This is our signal handler!'))

        self.stdout.write('\n\n=== Debug Complete ===')
