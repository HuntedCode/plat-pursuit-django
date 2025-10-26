import time
from django.core.management.base import BaseCommand
from trophies.token_keeper import TokenKeeper

class Command(BaseCommand):
    def handle(self, *args, **options):
        token_keeper = TokenKeeper()
        if token_keeper is None:
            self.stdout.write("TokenKeeper already running in another process")
            return
        token_keeper.initialize_instances()
        self.stdout.write("TokenKeeper started - 3 instances live!")
        try:
            while True:
                time.sleep(60)
                self.stdout.write(f"Stats: {token_keeper.stats}")
        except KeyboardInterrupt:
            token_keeper._cleanup()
            self.stdout.write("TokenKeeper stopped and Redis state cleaned")