import os
import logging
import json
import time
from dotenv import load_dotenv
from .models import Profile, Game
from trophies.util_modules.cache import redis_client

load_dotenv()
logger = logging.getLogger("psn_api")

class PSNManager:
    """Facade/Interface for PSN API, queuing jobs for TokenKeeper."""
    
    max_jobs_per_profile = int(os.getenv("MAX_JOBS_PER_PROFILE", 3))
    
    # Queues that track per-profile job counters for sync completion detection
    COUNTED_QUEUES = ("low_priority", "medium_priority", "bulk_priority")

    @classmethod
    def assign_job(cls, job_type: str, args: list, profile_id: int, priority_override: str=None):
        """Assign job to queue, respecting priorities."""
        queue_name = priority_override or cls._get_queue_for_job(job_type)
        if queue_name in cls.COUNTED_QUEUES:
            redis_client.incr(f"profile_jobs:{profile_id}:{queue_name}")
            redis_client.sadd("active_profiles", profile_id)

        json_data = json.dumps({
            'job_type': job_type,
            'args': args,
            'profile_id': profile_id
        })
        redis_client.lpush(f"{queue_name}_jobs", json_data)
        logger.info(f"Assigned {job_type} for profile {profile_id} to queue {queue_name}")

    @classmethod
    def _get_queue_for_job(cls, job_type):
        """Map job type to queue.

        Queue priority order (highest first):
        - orchestrator: profile-level orchestrators (sync_trophy_titles, profile_refresh, sync_profile_data, sync_complete)
        - high_priority: check_profile_health, handle_privacy_error
        - medium_priority: sync_title_stats, sync_title_id, sync_trophy_groups
        - low_priority: sync_trophies (default)
        - bulk_priority: large account sync_trophies (assigned via threshold override)
        """
        if job_type in ["sync_profile_data", "sync_trophy_titles", "profile_refresh", "sync_complete"]:
            return "orchestrator"
        elif job_type in ["check_profile_health"]:
            return "high_priority"
        elif job_type in ["sync_title_stats", "sync_title_id", "sync_trophy_groups"]:
            return "medium_priority"
        elif job_type in ["sync_trophies"]:
            return "low_priority"
        else:
            return "low_priority"
    
    @classmethod
    def _defer_job(cls, profile_id, job_type, args, priority_override=None):
        redis_client.rpush(
            f"deferred_jobs:{profile_id}",
            json.dumps({"type": job_type, "args": args, 'priority_override': priority_override}),
        )
        redis_client.expire(f"deferred_jobs:{profile_id}", 86400) # 1 Day
    
    # Public Tasks
    @classmethod
    def initial_sync(cls, profile: Profile):
        if not profile.sync_status == 'syncing':
            profile.reset_sync_progress()
            profile.set_sync_status('syncing')
            redis_client.set(f"sync_started_at:{profile.id}", str(time.time()), ex=7200)
            # Mark orchestrator as pending so the stuck checker doesn't fire
            # sync_complete before sync_trophy_titles has created the real jobs
            redis_client.set(f"sync_orchestrator_pending:{profile.id}", "1", ex=1800)
            cls.assign_job('sync_profile_data', args=[], profile_id=profile.id)
            cls.assign_job('sync_trophy_titles', args=[], profile_id=profile.id)

    @classmethod
    def profile_refresh(cls, profile: Profile):
        if profile.sync_status == 'error':
            cls.initial_sync(profile)
        elif profile.sync_status == 'synced':
            profile.reset_sync_progress()
            profile.set_sync_status('syncing')
            redis_client.set(f"sync_started_at:{profile.id}", str(time.time()), ex=7200)
            redis_client.set(f"sync_orchestrator_pending:{profile.id}", "1", ex=1800)
            cls.assign_job('profile_refresh', args=[], profile_id=profile.id)

    @classmethod
    def check_profile_badges(cls, profile: Profile, priority: str):
        cls.assign_job('check_profile_badges', args=[], profile_id=profile.id, priority_override=priority)
    
    @classmethod
    def sync_complete(cls, profile: Profile, priority: str, touched_profilegame_ids: list[int]):
        args = [touched_profilegame_ids, priority]
        cls.assign_job('sync_complete', args=args, profile_id=profile.id, priority_override=priority)
    
    @classmethod
    def check_profile_health(cls, profile: Profile):
        cls.assign_job('check_profile_health', args=[], profile_id=profile.id)
    
    @classmethod
    def sync_profile_data(cls, profile: Profile):
        cls.assign_job('sync_profile_data', args=[], profile_id=profile.id)

    @classmethod
    def assign_sync_trophies(cls, profile_id: int, np_communication_id: str, platform: str, priority_override: str = None):
        """Queue a sync_trophies job with per-profile deduplication.

        Uses a Redis set to skip games already queued for this sync cycle.
        Returns True if the job was queued, False if it was a duplicate.
        """
        dedup_key = f"sync_queued_games:{profile_id}"
        if redis_client.sismember(dedup_key, np_communication_id):
            logger.info(f"sync_trophies for {np_communication_id} already queued for profile {profile_id}, skipping")
            return False
        redis_client.sadd(dedup_key, np_communication_id)
        redis_client.expire(dedup_key, 7200)  # 2 hour TTL as safety net
        args = [np_communication_id, platform]
        cls.assign_job('sync_trophies', args, profile_id, priority_override=priority_override)
        return True

    @classmethod
    def sync_profile_game_trophies(cls, profile: Profile, game: Game):
        args = [game.np_communication_id, game.title_platform[0] if not game.title_platform[0] == 'PSPC' else game.title_platform[1]]
        PSNManager.assign_job('sync_trophies', args, profile.id, priority_override='medium_priority')
    
    @classmethod
    def sync_title_id(cls, profile: Profile, title_id_str: str, np_communication_id: str):
        args = [title_id_str, np_communication_id]
        PSNManager.assign_job('sync_title_id', args, profile.id, priority_override='high_priority')
    
    @classmethod
    def handle_privacy_error(cls, profile: Profile):
        cls.assign_job('handle_privacy_error', args=[], profile_id=profile.id, priority_override='high_priority')