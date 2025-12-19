from django.core.management.base import BaseCommand
from django.conf import settings
from trophies.utils import redis_client
import logging

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = "Admin commands for redis operations"

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument(
            '--flushall',
            action='store_true',
            help='Flush all data from Redis (destructive -- requires confirmation).'
        )
        group.add_argument(
            '--flush-index',
            action='store_true',
            help='Flush keys/caches related to the index page.'
        )

    def handle(self, *args, **options):
        if not settings.DEBUG:
            self.stdout.write(self.style.WARNING("Running in production-proceed with extreme caution!"))
        
        if options['flushall']:
            self._handle_flushall()
        elif options['flush_index']:
            self._handle_flush_index()
        
    def _confirm_action(self, action_desc):
        confirm = input(f"Are you sure you want to {action_desc}? (y/n):").strip().lower()
        return confirm == 'y'
    
    def _handle_flushall(self):
        if not self._confirm_action("flush ALL data from Redis (irreversible)"):
            self.stdout.write(self.style.ERROR("Operation cancelled."))
            return
        try:
            redis_client.flushall()
            logger.info("Redis FLUSHALL executed successfully.")
            self.stdout.write(self.style.SUCCESS("Redis fully flushed."))
        except Exception as e:
            logger.error(f"Error during FLUSHALL: {e}")
            self.stdout.write(self.style.ERROR(f"Error: {e}"))
    
    def _handle_flush_index(self):
        if not self._confirm_action("flush index page caches"):
            self.stdout.write(self.style.ERROR("Operation cancelled."))
            return
        
        cache_config = settings.CACHES['default']
        prefix = f"{cache_config['KEY_PREFIX']}:1:"
        
        index_patterns = [
            f"{prefix}community_stats_*",
            f"{prefix}featured_games_*",
            f"{prefix}featured_guide_*",
            f"{prefix}featured_guide:*",
            f"{prefix}latest_platinums_*",
            f"{prefix}playing_now_*",
            f"{prefix}latest_psn_rares_*",
            f"{prefix}latest_pp_rares_*",
            f"{prefix}featured_profile_*",
            f"{prefix}upcoming_events_*"
        ]
        
        try:
            deleted_count = 0
            for pattern in index_patterns:
                matching_keys = redis_client.keys(pattern)
                if matching_keys:
                    redis_client.delete(*matching_keys)
                    deleted_count += len(matching_keys)
            logger.info(f"Flushed {deleted_count} index-related keys.")
            self.stdout.write(self.style.SUCCESS(f"Flushed {deleted_count} keys for index page."))
        except Exception as e:
            logger.error(f"Error during index flush: {e}")
            self.stdout.write(self.style.ERROR(f"Error: {e}"))
