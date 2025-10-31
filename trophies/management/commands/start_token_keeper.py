import time
import signal
import sys
from django.core.management.base import BaseCommand
from trophies.token_keeper import TokenKeeper

class Command(BaseCommand):
    help = 'Starts the TokenKeeper singleton process for managing PSN API tokens and job queues.'

    def handle(self, *args, **options):
        token_keeper = TokenKeeper()
        if token_keeper is None:
            self.stdout.write("TokenKeeper already running in another process")
            return
        self.stdout.write("TokenKeeper started - 3 instances live!")

        def signal_handler(sig, frame):
            self.stdout.write("Signal received, shutting down TokenKeeper...")
            token_keeper._cleanup()
            self.stdout.write("TokenKeeper stopped and Redis state cleaned")
            sys.exit(0)
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        try:
            while True:
                time.sleep(60)
                stats = token_keeper.stats
                self.stdout.write(f"Current stats: {stats}")
        except Exception as e:
            self.stderr.write(f"Unexpected error: {e}")
            token_keeper._cleanup()
            raise