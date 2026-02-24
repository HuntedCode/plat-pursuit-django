from django.core.management.base import BaseCommand
from django.conf import settings
from trophies.util_modules.cache import redis_client
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
        group.add_argument(
            '--flush-game-page',
            type=str,
            help='Flush keys/caches related to specific game page (np_communication_id required)'
        )
        group.add_argument(
            '--flush-token-keeper',
            action='store_true',
            help='Flush TokenKeeper queues, profile jobs, deferred jobs, and active profiles (destructive -- requires confirmation).'
        )
        group.add_argument(
            '--flush-complete-lock',
            type=int,
            help='Flush TokenKeeper for a specific profile.'
        )
        group.add_argument(
            '--flush-dashboard',
            type=int,
            help='Flush dashboard module caches for a specific profile ID.'
        )

    def handle(self, *args, **options):
        if not settings.DEBUG:
            self.stdout.write(self.style.WARNING("Running in production-proceed with extreme caution!"))
        
        if options['flushall']:
            self._handle_flushall()
        elif options['flush_index']:
            self._handle_flush_index()
        elif options['flush_game_page']:
            self._handle_flush_game_page(options['flush_game_page'])
        elif options['flush_token_keeper']:
            self._handle_flush_token_keeper()
        elif options['flush_complete_lock']:
            self._handle_flush_complete_lock(options['flush_complete_lock'])
        elif options['flush_dashboard']:
            self._handle_flush_dashboard(options['flush_dashboard'])

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
            logger.exception(f"Error during FLUSHALL: {e}")
            self.stdout.write(self.style.ERROR(f"Error: {e}"))
    
    def _handle_flush_index(self):
        if not self._confirm_action("flush index page caches"):
            self.stdout.write(self.style.ERROR("Operation cancelled."))
            return
        
        cache_config = settings.CACHES['default']
        prefix = f"{cache_config['KEY_PREFIX']}:1:"
        
        index_patterns = [
            f"{prefix}featured_games_*",
            f"{prefix}featured_guide_*",
            f"{prefix}featured_guide:*",
            f"{prefix}playing_now_*",
            f"{prefix}featured_badges_*",
            f"{prefix}featured_checklists_*",
            f"{prefix}whats_new_*",
            f"{prefix}latest_badges_*"
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
            logger.exception(f"Error during index flush: {e}")
            self.stdout.write(self.style.ERROR(f"Error: {e}"))

    def _handle_flush_game_page(self, np_communication_id: str):
        if not self._confirm_action(f"flush game page cache for {np_communication_id}"):
            self.stdout.write(self.style.ERROR("Operation cancelled."))
            return
        
        cache_config = settings.CACHES['default']
        prefix = f"{cache_config['KEY_PREFIX']}:1:"

        exact_keys = [
            f"{prefix}game:imageurls:{np_communication_id}",
            f"{prefix}game:trophygroups:{np_communication_id}",
        ]
        stats_pattern = f"{prefix}game:stats:{np_communication_id}:*"

        try:
            deleted_count = 0
            for key in exact_keys:
                deleted_count += redis_client.delete(key)
            for key in redis_client.scan_iter(match=stats_pattern):
                deleted_count += redis_client.delete(key)
            logger.info(f"Flushed {deleted_count} index-related keys.")
            self.stdout.write(self.style.SUCCESS(f"Flushed {deleted_count} keys for game {np_communication_id} page."))
        except Exception as e:
            logger.exception(f"Error during game page flush: {e}")
            self.stdout.write(self.style.ERROR(f"Error: {e}"))

    def _handle_flush_token_keeper(self):
        if not self._confirm_action("flush TokenKeeper queues, profile jobs, deferred jobs, and active profiles (irreversible)"):
            self.stdout.write(self.style.ERROR("Operation cancelled."))
            return
        
        try:
            deleted_count = 0

            # Clear job queues
            queues = ['high_priority_jobs', 'medium_priority_jobs', 'low_priority_jobs']
            for queue in queues:
                deleted_count += redis_client.delete(queue)

            # Clear profile_jobs:* (all queues) and sync locks
            for pattern in ['profile_jobs:*', 'deferred_jobs:*', 'pending_sync_complete:*', 'sync_started_at:*', 'sync_trophies_lock:*', 'shovelware_concept_lock:*']:
                matching_keys = redis_client.keys(pattern)
                if matching_keys:
                    deleted_count += redis_client.delete(*matching_keys)

            # Clear active_profiles set
            deleted_count += redis_client.delete('active_profiles')

            # Clear high sync volume banner flag
            deleted_count += redis_client.delete('site:high_sync_volume')

            logger.info(f"Flushed {deleted_count} TokenKeeper-related keys/queues.")
            self.stdout.write(self.style.SUCCESS(f"Flushed {deleted_count} TokenKeeper queues and profiles."))
        except Exception as e:
            logger.exception(f"Error during TokenKeeper flush: {e}")
            self.stdout.write(self.style.ERROR(f"Error: {e}"))

    def _handle_flush_complete_lock(self, profile_id: int):
        if not self._confirm_action(f"flush TokenKeeper lock and pending complete for profile {profile_id} only (irreversible)"):
            self.stdout.write(self.style.ERROR("Operation cancelled."))
            return

        try:
            lock_key = f"complete_lock:{profile_id}"
            profile_jobs_key = f"pending_sync_complete:{profile_id}"
            sync_started_key = f"sync_started_at:{profile_id}"
            redis_client.delete(lock_key)
            self.stdout.write(self.style.SUCCESS(f"Lock successfully flushed!"))
            redis_client.delete(profile_jobs_key)
            self.stdout.write(self.style.SUCCESS(f"Pending complete successfully flushed!"))
            redis_client.delete(sync_started_key)
            self.stdout.write(self.style.SUCCESS(f"Sync started timestamp successfully flushed!"))
        except Exception as e:
            logger.exception(f"Error during complete lock flush: {e}")
            self.stdout.write(self.style.ERROR(f"Error: {e}"))

    def _handle_flush_dashboard(self, profile_id: int):
        try:
            from trophies.services.dashboard_service import invalidate_dashboard_cache
            invalidate_dashboard_cache(profile_id)
            self.stdout.write(self.style.SUCCESS(f"Dashboard caches flushed for profile {profile_id}."))
        except Exception as e:
            logger.exception(f"Error during dashboard flush: {e}")
            self.stdout.write(self.style.ERROR(f"Error: {e}"))
