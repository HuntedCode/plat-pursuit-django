import time
from django.core.management.base import BaseCommand
from trophies.token_keeper import token_keeper

class Command(BaseCommand):
    def handle(self, *args, **options):
        token_keeper.initialize_instances()
        self.stdout.write("TokenKeeper started - 3 instances live!")
        try:
            while True:
                time.sleep(60)
                self.stdout.write(f"Stats: {token_keeper.stats}")
        except KeyboardInterrupt:
            self.stdout.write("TokenKeeper stopped")