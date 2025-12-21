from django.core.management.base import BaseCommand
from trophies.token_keeper import TokenKeeper
import logging
import time

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Control TokenKeeper: start, stop or restart.'

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument('--start', action='store_true', help='Start TokenKeeper if not running.')
        group.add_argument('--stop', action='store_true', help='Gracefully stop TokenKeeper.')
        group.add_argument('--restart', action='store_true', help='Stop then start TokenKeeper.')

    def handle(self, *args, **options):
        keeper = TokenKeeper()
        if options['stop'] or options['restart']:
            self._stop_keeper(keeper)
        if options['start'] or options['restart']:
            self._start_keeper(keeper)
    
    def _stop_keeper(self, keeper):
        self.stdout.write("Stopping TokenKeeper...")
        try:
            for t in (keeper._health_thread or []) + (keeper._stats_thread or []) + (keeper._job_workers or []):
                if t and t.is_alive():
                    t.join(timeout=10)
            keeper._cleanup()
            self.stdout.write(self.style.SUCCESS('TokenKeeper stopped.'))
        except Exception as e:
            logger.error(f"Error stopping TokenKeeper: {e}")
            self.stdout.write(self.style.ERROR(f"Error: {e}"))
        
    def _start_keeper(self, keeper):
        self.stdout.write("Starting TokenKeeper...")
        try:
            keeper._init()
            self.stdout.write(self.style.SUCCESS("TokenKeeper started."))
        except Exception as e:
            logger.error(f"Error starting TokenKeeper: {e}")
            self.stdout.writer(self.style.ERROR(f"Error: {e}"))