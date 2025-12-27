import os
import logging
import json
import time
from dotenv import load_dotenv
from .models import Profile, Game
from .utils import redis_client

load_dotenv()
logger = logging.getLogger("psn_api")

class PSNManager:
    """Facade/Interface for PSN API, queuing jobs for TokenKeeper."""
    
    max_jobs_per_profile = int(os.getenv("MAX_JOBS_PER_PROFILE", 3))
    
    @classmethod
    def assign_job(cls, job_type: str, args: list, profile_id: int, priority_override: str=None):
        """Assign job to queue, respecting priorities."""
        queue_name = priority_override or cls._get_queue_for_job(job_type)
        if queue_name == "low_priority" or "medium_priority":
            current_jobs = int(redis_client.get(f"profile_jobs:{profile_id}:{queue_name}") or 0)
            if current_jobs >= cls.max_jobs_per_profile:
                logger.info(f"Trickling: Profile {profile_id} at max jobs ({current_jobs}) - deferring")
                cls._defer_job(profile_id, job_type, args, priority_override)
                return
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
    def _get_queue_for_job(self, job_type):
        """Map job type to queue."""
        if job_type in ["sync_profile_data", "sync_trophy_titles", "profile_refresh", "check_profile_health"]:
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
            cls.assign_job('sync_profile_data', args=[], profile_id=profile.id)
            cls.assign_job('sync_trophy_titles', args=[], profile_id=profile.id)
    
    @classmethod
    def profile_refresh(cls, profile: Profile):
        if profile.sync_status == 'error':
            cls.initial_sync(profile)
        elif profile.sync_status == 'synced':
            profile.reset_sync_progress()
            profile.set_sync_status('syncing')
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