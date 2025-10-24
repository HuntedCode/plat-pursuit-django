import os
import time
import logging
import json
from dotenv import load_dotenv
from psnawp_api.models.trophies.trophy_constants import PlatformType
from celery import current_app, Task
from requests import HTTPError
from .models import Game, Profile
from .token_keeper import token_keeper
from .utils import redis_client, log_api_call

load_dotenv()
logger = logging.getLogger("psn_api")

class PSNManager:
    """Central manager for PSN API: Handles token rotation, client instances, job assignment and monitoring."""

    # Init methods
    def __init__(self):
        self.max_calls_per_window = int(os.getenv("MAX_CALLS_PER_WINDOW", 300))
        self.window_seconds = int(os.getenv("WINDOW_SECONDS", 900))
        self.max_jobs_per_profile = int(os.getenv("MAX_JOBS_PER_PROFILE", 20))
        self.reserved_high_prio_calls = int(self.max_calls_per_window * 0.1)

    def get_instance_for_job(self, job_type, profile_id=None):
        """Checkout live instance from keeper."""
        instance = token_keeper.checkout(timeout=5)
        if not instance:
            raise ValueError("No healthy instances available")
        return instance

    def assign_job(self, job_type, args, profile_id=None, priority_override=None):
        """Assign job to instance, respecting queue priorities."""
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

        try:
            instance = self.get_instance_for_job(job_type, profile_id)
        except ValueError as e:
            logger.warning(f"No instance available for {job_type} - deferring")
            if profile_id:
                self.defer_job(profile_id, job_type, args, priority_override)
            return None
        
        task_id = current_app.send_task(
            f"trophies.tasks.{job_type}", args=[*args, instance.instance_id], queue=queue
        ).id

        logger.info(
            f"Assigned {job_type} to instance {instance.instance_id}"
        )
        return task_id

    def complete_job(self, profile_id, queue, instance_id):
        """Checkin instance and handle deferred."""
        token_keeper.checkin(instance_id)
        if profile_id:
            if queue != 'high_priority':
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

    # Call methods
    def call_user_profile_legacy(self, instance, profile: Profile):
        token_keeper.record_call(instance.token)
        return self._execute_call(instance, profile, "get_profile_legacy")

    def call_user_trophy_titles(self, instance, profile: Profile, limit=200, offset=0):
        token_keeper.record_call(instance.token)
        data = self._execute_call(
            instance,
            profile,
            "trophy_titles",
            limit=limit,
            offset=offset,
            page_size=limit,
        )
        data_list = list(data) # Generator will exhaust, so must store list in separate variable.
        is_full = len(data_list) >= limit
        return data_list, is_full

    def call_user_title_stats(self, instance, profile: Profile, limit=200, offset=0):
        token_keeper.record_call(instance.token)
        data = self._execute_call(
            instance,
            profile,
            "title_stats",
            limit=limit,
            offset=offset,
            page_size=limit,
        )
        data_list = list(data) # Generator will exhaust, so must store list in separate variable.
        is_full = len(data_list) >= limit
        return data_list, is_full

    def call_user_trophies(
        self,
        instance,
        profile : Profile,
        game : Game,
        limit=500,
        offset=0,
        include_progress=True,
        trophy_group_id="all",
    ):
        token_keeper.record_call(instance.token)
        if include_progress:
            token_keeper.record_call(instance.token)
        data = self._execute_call(
            instance,
            profile,
            "trophies",
            np_communication_id=game.np_communication_id,
            platform=PlatformType(game.title_platform[0]),
            include_progress=include_progress,
            trophy_group_id=trophy_group_id,
            limit=limit,
            offset=offset,
            page_size=limit,
        )
        data_list = list(data) # Generator will exhaust, so must store list in separate variable.
        is_full = len(data_list) >= limit
        return data_list, is_full

    def _execute_call(self, instance, profile: Profile, endpoint, **kwargs):
        """Execute API call on instance, with logging and error handling."""
        user = self._get_user_from_cache(instance, profile)
        start_time = time.time()
        try:
            data = self._perform_endpoint_call(user, endpoint, **kwargs)
            log_api_call(
                endpoint,
                instance["token"],
                profile.id,
                200,
                time.time() - start_time,
            )
            return data
        except HTTPError as e:
            log_api_call(
                endpoint,
                instance["token"],
                profile.id,
                e.response.status_code,
                time.time() - start_time,
                str(e),
            )
            if e.response.status_code == 429:
                self._handle_rate_limit(instance)
            raise
    
    def _get_user_from_cache(self, instance, profile : Profile):
        lookup_key = profile.account_id if profile.account_id else profile.psn_username
        if lookup_key not in instance.user_cache:
            instance.user_cache[lookup_key] = {
                "user": (instance.client.user(account_id=profile.account_id) if profile.account_id else instance.client.user(online_id=profile.psn_username)),
                "timestamp": time.time()
            }
        return instance.user_cache[lookup_key]['user']

    def _perform_endpoint_call(self, user, endpoint, **kwargs):
        """Dispatch endpoint call."""
        if endpoint == "get_profile_legacy":
            return user.get_profile_legacy()
        elif endpoint == "trophy_titles":
            return user.trophy_titles(**kwargs)
        elif endpoint == "title_stats":
            return user.title_stats(**kwargs)
        elif endpoint == "trophies":
            return user.trophies(**kwargs)
        raise ValueError(f"Unknown endpoint: {endpoint}")

    def _handle_rate_limit(self, instance):
        """Handle 429: Park instance and reassign."""
        logger.warning(
            f"Rate limit hit for instance {instance.instance_id}. Parking for 60s."
        )
        instance["last_health"] = 0
        time.sleep(60)
        instance["last_health"] = time.time()

class BasePSNTask(Task):
    """Base task for PSN-related Celery tasks with failure handling."""
    def on_failure(self, exc, task_id, args, kwargs, einfo):
        profile_id = args.get('profile_id')
        if profile_id:
            manager = PSNManager()
            manager.complete_job(profile_id, self.request.queue)
        super().on_failure(exc, task_id, args, kwargs, einfo)