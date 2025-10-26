import os
import time
import logging
import json
import uuid
from dotenv import load_dotenv
from celery import current_app, Task
from .models import Profile
from .utils import redis_client

load_dotenv()
logger = logging.getLogger("psn_api")

class PSNManager:
    """Central manager for PSN API: Handles token rotation, client instances, job assignment and monitoring."""

    # Init methods
    def __init__(self):
        self.max_jobs_per_profile = int(os.getenv("MAX_JOBS_PER_PROFILE", 20))

    def assign_job(self, job_type, args, profile_id=None, priority_override=None):
        """Assign job to queue, respecting priorities."""
        match priority_override:
            case "high":
                queue = "high_priority"
            case "medium":
                queue = "medium_priority"
            case "low":
                queue = "low_priority"
            case _:
                queue = self._get_queue_for_job(job_type)

        if profile_id and queue != "high_priority":
            current_jobs = int(redis_client.get(f"profile_jobs:{profile_id}") or 0)
            if current_jobs >= self.max_jobs_per_profile:
                logger.info(
                    f"Trickling: Profile {profile_id} at max jobs ({current_jobs}) - deferring"
                )
                self.defer_job(profile_id, job_type, args, priority_override)
                return None
            redis_client.incr(f"profile_jobs:{profile_id}")
            redis_client.sadd("active_profiles", profile_id)
        
        task_id = current_app.send_task(
            f"trophies.tasks.{job_type}", args=[*args, queue], queue=queue
        ).id

        logger.info(f"Assigned {job_type} for profile {profile_id} to queue {queue}")
        return task_id

    def complete_job(self, profile_id, queue):
        """Handle finished job, check for deferred."""
        if profile_id and queue != 'high_priority':
            redis_client.decr(f"profile_jobs:{profile_id}")
            current_jobs = int(redis_client.get(f"profile_jobs:{profile_id}") or 0)
            if current_jobs <= 0:
                redis_client.delete(f"profile_jobs:{profile_id}")
                redis_client.srem("active_profiles", profile_id)
            job_json = redis_client.lpop(f"deferred_jobs:{profile_id}")
            if job_json:
                job_data = json.loads(job_json)
                self.assign_job(job_data['type'], job_data['args'], profile_id, job_data.get('priority_override'))

    def _get_queue_for_job(self, job_type):
        """Map job type to queue."""
        if job_type in ["initial_sync", "profile_refresh"]:
            return "high_priority"
        elif job_type == "sync_game_trophies":
            return "medium_priority"
        return "low_priority"
    
    def defer_job(self, profile_id, job_type, args, priority_override=None):
        redis_client.rpush(
            f"deferred_jobs:{profile_id}",
            json.dumps({"type": job_type, "args": args, 'priority_override': priority_override}),
        )
        redis_client.expire(f"deferred_jobs:{profile_id}", 86400) # 1 Day

    def publish_request(self, profile: Profile, job_type: str, request: str, timeout=30, args={}):
        task_id = str(uuid.uuid4())
        pubsub = redis_client.pubsub()
        pubsub.subscribe(f"psn_api_responses:{task_id}")

        redis_client.publish(
            "psn_api_requests",
            json.dumps({
                'task_id': task_id,
                'job_type': job_type,
                'request': request,
                'profile_id': profile.id,
                'args': args
            })
        )

        start = time.time()
        while time.time() - start < timeout:
            message = pubsub.get_message(timeout=1)
            if message and message['type'] == 'message':
                response = json.loads(message['data'])
                if response['status'] == 'success':
                    result = response['result']
                    return result
                else:
                    raise ValueError(f"API call failed: {response['error']}")
            time.sleep(0.1)
        pubsub.unsubscribe()
        raise TimeoutError("No response from TokenKeeper")

class BasePSNTask(Task):
    """Base task for PSN-related Celery tasks with failure handling."""
    def on_failure(self, exc, task_id, args, kwargs, einfo):
        profile_id = args[0] if args else None
        queue_name = kwargs.get('queue_name') or self._get_queue_name(args[1] if len(args) > 1 else None)
        if profile_id:
            manager = PSNManager()
            manager.complete_job(profile_id, queue_name)
        super().on_failure(exc, task_id, args, kwargs, einfo)
    
    def _get_queue_name(self, job_type):
        if job_type in ["initial_sync", "profile_refresh"]:
            return "high_priority"
        elif job_type == "sync_game_trophies":
            return "medium_priority"
        return "low_priority"