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
        group.add_argument(
            '--flush-concept',
            type=int,
            help='Flush image/trophy/stats caches for all games under a specific concept ID.'
        )
        group.add_argument(
            '--flush-community',
            action='store_true',
            help='Flush Community Hub caches (review recommendations + DLC rating averages).'
        )
        group.add_argument(
            '--get-bulk-threshold',
            action='store_true',
            help='Get the current bulk priority queue threshold.'
        )
        group.add_argument(
            '--set-bulk-threshold',
            type=int,
            help='Set the bulk priority queue threshold (jobs above this go to bulk_priority queue).'
        )
        group.add_argument(
            '--move-whale-jobs',
            action='store_true',
            help='Move sync_trophies jobs from low_priority to bulk_priority for profiles exceeding the bulk threshold.'
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
        elif options['flush_concept']:
            self._handle_flush_concept(options['flush_concept'])
        elif options['flush_dashboard']:
            self._handle_flush_dashboard(options['flush_dashboard'])
        elif options['flush_community']:
            self._handle_flush_community()
        elif options['get_bulk_threshold']:
            self._handle_get_bulk_threshold()
        elif options['set_bulk_threshold'] is not None:
            self._handle_set_bulk_threshold(options['set_bulk_threshold'])
        elif options['move_whale_jobs']:
            self._handle_move_whale_jobs()

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

    def _handle_flush_concept(self, concept_id: int):
        from trophies.models import Game

        np_ids = list(
            Game.objects.filter(concept_id=concept_id)
            .values_list('np_communication_id', flat=True)
        )

        if not np_ids:
            self.stdout.write(self.style.WARNING(f"No games found for concept {concept_id}."))
            return

        self.stdout.write(f"Found {len(np_ids)} game(s) for concept {concept_id}: {', '.join(filter(None, np_ids))}")

        if not self._confirm_action(f"flush image/trophy/stats caches for these {len(np_ids)} game(s)"):
            self.stdout.write(self.style.ERROR("Operation cancelled."))
            return

        cache_config = settings.CACHES['default']
        prefix = f"{cache_config['KEY_PREFIX']}:1:"

        try:
            deleted_count = 0
            for np_id in np_ids:
                if not np_id:
                    continue
                deleted_count += redis_client.delete(
                    f"{prefix}game:imageurls:{np_id}",
                    f"{prefix}game:trophygroups:{np_id}",
                )
                for key in redis_client.scan_iter(match=f"{prefix}game:stats:{np_id}:*"):
                    deleted_count += redis_client.delete(key)
            logger.info(f"Flushed {deleted_count} keys for concept {concept_id}.")
            self.stdout.write(self.style.SUCCESS(f"Flushed {deleted_count} keys for concept {concept_id}."))
        except Exception as e:
            logger.exception(f"Error during concept flush: {e}")
            self.stdout.write(self.style.ERROR(f"Error: {e}"))

    def _handle_flush_token_keeper(self):
        if not self._confirm_action("flush TokenKeeper queues, profile jobs, deferred jobs, and active profiles (irreversible)"):
            self.stdout.write(self.style.ERROR("Operation cancelled."))
            return
        
        try:
            deleted_count = 0

            # Clear job queues
            queues = ['orchestrator_jobs', 'high_priority_jobs', 'medium_priority_jobs', 'low_priority_jobs', 'bulk_priority_jobs']
            for queue in queues:
                deleted_count += redis_client.delete(queue)

            # Clear profile_jobs:* (all queues), sync locks, orchestrator pending flags, and dedup sets
            for pattern in ['profile_jobs:*', 'deferred_jobs:*', 'pending_sync_complete:*', 'sync_started_at:*', 'sync_trophies_lock:*', 'shovelware_concept_lock:*', 'sync_orchestrator_pending:*', 'sync_queued_games:*', 'sync_complete_in_progress:*']:
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
            orchestrator_key = f"sync_orchestrator_pending:{profile_id}"
            dedup_key = f"sync_queued_games:{profile_id}"
            sync_complete_key = f"sync_complete_in_progress:{profile_id}"
            redis_client.delete(lock_key)
            self.stdout.write(self.style.SUCCESS(f"Lock successfully flushed!"))
            redis_client.delete(profile_jobs_key)
            self.stdout.write(self.style.SUCCESS(f"Pending complete successfully flushed!"))
            redis_client.delete(sync_started_key)
            self.stdout.write(self.style.SUCCESS(f"Sync started timestamp successfully flushed!"))
            redis_client.delete(orchestrator_key)
            self.stdout.write(self.style.SUCCESS(f"Orchestrator pending flag successfully flushed!"))
            redis_client.delete(dedup_key)
            self.stdout.write(self.style.SUCCESS(f"Queued games dedup set successfully flushed!"))
            redis_client.delete(sync_complete_key)
            self.stdout.write(self.style.SUCCESS(f"Sync complete in-progress flag successfully flushed!"))
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

    def _handle_flush_community(self):
        if not self._confirm_action("flush Community Hub caches (review recommendations + DLC rating averages)"):
            self.stdout.write(self.style.ERROR("Operation cancelled."))
            return

        cache_config = settings.CACHES['default']
        prefix = f"{cache_config['KEY_PREFIX']}:1:"

        community_patterns = [
            f"{prefix}review:recommend:*",
            f"{prefix}concept:averages:*:group:*",
        ]

        try:
            deleted_count = 0
            for pattern in community_patterns:
                matching_keys = redis_client.keys(pattern)
                if matching_keys:
                    redis_client.delete(*matching_keys)
                    deleted_count += len(matching_keys)
            logger.info(f"Flushed {deleted_count} Community Hub cache keys.")
            self.stdout.write(self.style.SUCCESS(f"Flushed {deleted_count} Community Hub cache keys."))
        except Exception as e:
            logger.exception(f"Error during community flush: {e}")
            self.stdout.write(self.style.ERROR(f"Error: {e}"))

    def _handle_get_bulk_threshold(self):
        try:
            value = redis_client.get('sync:bulk_threshold')
            if value is None:
                self.stdout.write(f"Bulk threshold: not set (default: 5000)")
            else:
                self.stdout.write(f"Bulk threshold: {int(value)}")
        except Exception as e:
            logger.exception(f"Error reading bulk threshold: {e}")
            self.stdout.write(self.style.ERROR(f"Error: {e}"))

    def _handle_set_bulk_threshold(self, value: int):
        if value < 0:
            self.stdout.write(self.style.ERROR("Threshold must be a non-negative integer."))
            return
        try:
            redis_client.set('sync:bulk_threshold', str(value))
            logger.info(f"Bulk threshold set to {value}.")
            self.stdout.write(self.style.SUCCESS(f"Bulk threshold set to {value}."))
        except Exception as e:
            logger.exception(f"Error setting bulk threshold: {e}")
            self.stdout.write(self.style.ERROR(f"Error: {e}"))

    def _handle_move_whale_jobs(self):
        """Scan low_priority_jobs, group by profile, move whale profiles to bulk_priority."""
        import json as json_mod
        try:
            bulk_threshold = int(redis_client.get('sync:bulk_threshold') or 5000)
            self.stdout.write(f"Using bulk threshold: {bulk_threshold}")

            # Read all jobs from low_priority without removing them yet
            all_jobs = redis_client.lrange('low_priority_jobs', 0, -1)
            if not all_jobs:
                self.stdout.write(self.style.WARNING("No jobs in low_priority_jobs."))
                return

            self.stdout.write(f"Found {len(all_jobs)} jobs in low_priority_jobs.")

            # Group jobs by profile_id
            profile_jobs = {}
            for raw_job in all_jobs:
                try:
                    job = json_mod.loads(raw_job)
                    pid = job.get('profile_id')
                    if pid is not None:
                        profile_jobs.setdefault(pid, []).append(raw_job)
                except (json_mod.JSONDecodeError, TypeError):
                    continue

            # Find profiles exceeding threshold
            whale_profiles = {pid: jobs for pid, jobs in profile_jobs.items() if len(jobs) > bulk_threshold}
            if not whale_profiles:
                self.stdout.write(self.style.WARNING(f"No profiles exceed the threshold of {bulk_threshold} jobs in low_priority."))
                return

            summary = ", ".join(f"profile {pid} ({len(jobs)} jobs)" for pid, jobs in whale_profiles.items())
            self.stdout.write(f"Whale profiles found: {summary}")

            if not self._confirm_action(f"move {sum(len(j) for j in whale_profiles.values())} jobs from low_priority to bulk_priority"):
                self.stdout.write(self.style.ERROR("Operation cancelled."))
                return

            # Use a pipeline for atomicity: remove whale jobs from low, push to bulk, fix counters
            total_moved = 0
            for pid, jobs in whale_profiles.items():
                pipe = redis_client.pipeline(transaction=True)
                for raw_job in jobs:
                    pipe.lrem('low_priority_jobs', 1, raw_job)
                    pipe.lpush('bulk_priority_jobs', raw_job)
                pipe.execute()

                # Fix per-profile counters: move count from low_priority to bulk_priority
                job_count = len(jobs)
                low_counter = int(redis_client.get(f"profile_jobs:{pid}:low_priority") or 0)
                bulk_counter = int(redis_client.get(f"profile_jobs:{pid}:bulk_priority") or 0)

                new_low = max(low_counter - job_count, 0)
                new_bulk = bulk_counter + job_count

                if new_low > 0:
                    redis_client.set(f"profile_jobs:{pid}:low_priority", str(new_low))
                else:
                    redis_client.delete(f"profile_jobs:{pid}:low_priority")

                redis_client.set(f"profile_jobs:{pid}:bulk_priority", str(new_bulk))
                redis_client.sadd("active_profiles", pid)

                total_moved += job_count
                self.stdout.write(f"  Moved {job_count} jobs for profile {pid}")

            logger.info(f"Moved {total_moved} whale jobs from low_priority to bulk_priority.")
            self.stdout.write(self.style.SUCCESS(f"Done! Moved {total_moved} total jobs to bulk_priority."))
        except Exception as e:
            logger.exception(f"Error during whale job migration: {e}")
            self.stdout.write(self.style.ERROR(f"Error: {e}"))
