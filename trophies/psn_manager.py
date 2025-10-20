import os
import time
import redis
import hashlib
import requests
import logging
from dotenv import load_dotenv
from psnawp_api import PSNAWP
from celery import current_app
from pyrate_limiter import Rate

load_dotenv()
logger = logging.getLogger("psn_api")
redis_client = redis.Redis.from_url(
    os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
)


class PSNManager:
    """Central manager for PSN API: Handles token rotation, client instances, job assignment and monitoring."""

    def __init__(self):
        self.tokens = os.getenv("PSN_TOKENS", "").split(",")
        self.rate_limit_delay = float(os.getenv("PSN_RATE_LIMIT_DELAY", 1.0))
        self.max_calls_per_window = int(os.getenv("MAX_CALLS_PER_WINDOW", 300))
        self.window_seconds = int(os.getenv("WINDOW_SECONDS", 900))
        self.health_check_interval = 300
        self.max_jobs_per_profile = 20

        if not self.tokens:
            raise ValueError("PSN_TOKENS must be set in .env")
        if len(self.tokens) != 3:
            logger.warning(
                f"Expected 3 tokens for rotation - found {len(self.tokens)}."
            )

        self.instances = self._init_instances()

    def _init_instances(self):
        """Create token instances for rotation."""
        instances = []
        for i, token in enumerate(self.tokens):
            instances.append(
                {
                    "token": token,
                    "instance_id": i,
                    "client": None,
                    "user_cache": {},
                    "last_refresh": 0,
                    "last_health": time.time(),
                    "last_switch": 0,
                }
            )
        return instances

    def _is_healthy(self, instance):
        """Check token health - refresh if needed."""
        if time.time() - instance["last_health"] > self.health_check_interval:
            instance["last_health"] = time.time()
        if instance["last_health"] <= 0:
            return False
        if instance["client"] is not None:
            auth = instance["client"].authenticator
            if auth.access_token_expiration_in < 300:
                self._refresh_token(instance)
        return True

    def _refresh_token(self, instance):
        """Refresh access token - disable instance if fail."""
        start_time = time.time()
        try:
            instance["client"].authenticator.fetch_access_token_from_refresh()
            instance["last_refresh"] = time.time()
            log_api_call(
                "token_refresh", instance["token"], None, 200, time.time() - start_time
            )
            self._record_call(instance["token"])
            logger.info(f"Refreshed token for instance {instance['instance_id']}")
        except Exception as e:
            log_api_call(
                "token_refresh",
                instance["token"],
                None,
                500,
                time.time() - start_time,
                str(e),
            )
            logger.error(f"Failed refresh for instance {instance['instance_id']}: {e}")
            instance["last_health"] = 0

    def _get_calls_in_window(self, token):
        """Count calls in rolling time window."""
        now = time.time()
        redis_client.zremrangebyscore(
            f"token:{token}:timestamps", 0, now - self.window_seconds
        )
        return redis_client.zcard(f"token:{token}:timestamps")

    def get_instance_for_job(self, job_type="standard", profile_id=None):
        """Assign job to least-loaded healthy instance, preferring consistency for profile."""
        if profile_id:
            lock_key = f"profile_lock:{profile_id}"
            locked_instance_id = redis_client.get(lock_key)
            if locked_instance_id:
                instance = next(
                    (
                        p
                        for p in self.instances
                        if p["instance_id"] == int(locked_instance_id)
                    ),
                    None,
                )
                if instance and self._is_healthy(instance):
                    return instance

        instance_scores = {}
        for instance in self.instances:
            if not self._is_healthy(instance):
                continue
            calls = self._get_calls_in_window(instance["token"])
            switch_penalty = 10 if instance["last_switch"] > time.time() - 60 else 0
            instance_scores[instance["instance_id"]] = calls + switch_penalty
        if not instance_scores:
            raise ValueError("No healthy instances available")
        best_id = min(instance_scores, key=instance_scores.get)
        instance = next(p for p in self.instances if p["instance_id"] == best_id)

        if profile_id:
            redis_client.set(lock_key, best_id, ex=3600)  # Locks for 1hr
        return instance

    def assign_job(self, job_type, args, profile_id=None):
        """Assign job to instance, respecting per-profile limits and queue priorities."""
        queue = self._get_queue_for_job(job_type)
        if profile_id and queue != "high_priority":
            current_jobs = int(redis_client.get(f"profile_jobs:{profile_id}") or 0)
            if current_jobs >= self.max_jobs_per_profile:
                logger.info(
                    f"Trickling: Profile {profile_id} at max jobs ({current_jobs}) - deffering"
                )
                redis_client.rpush(f"deferred_jobs:{profile_id}", f"{job_type}:{args}")
                return None
            redis_client.incr(f"profile_jobs:{profile_id}")
            redis_client.sadd("active_profiles", profile_id)

        instance = self.get_instance_for_job(job_type, profile_id)
        queue_name = f"instance_{instance['instance_id']}_queue"
        task_id = current_app.send_task(
            f"trophies.tasks.{job_type}", args=args, queue=queue_name
        ).id
        logger.info(
            f"Assigned {job_type} for profile {profile_id} to instance {instance['instance_id']} on {queue_name}"
        )
        return task_id

    def complete_job(self, profile_id, task_id=None):
        """Mark job complete, release profile lock and queue deferred if available."""
        if profile_id:
            redis_client.decr(f"profile_jobs:{profile_id}")
            if int(redis_client.get(f"profile_jobs:{profile_id}") or 0 <= 0):
                redis_client.delete(f"profile_jobs:{profile_id}")
                redis_client.srem("active_profiles", profile_id)
            job = redis_client.lpop(f"deferred_jobs:{profile_id}")
            if job:
                job_type, args = job.decode("utf-8").split(":", 1)
                self.assign_job(job_type, eval(args), profile_id)

    def _get_queue_for_job(self, job_type):
        """Map job type to queue."""
        if job_type in ["general_sync", "manual_refresh"]:
            return "high_priority"
        elif job_type == "bulk_trophies":
            return "medium_priority"
        return "low_priority"

    def execute_call(self, instance, psn_username, endpoint, account_id=None, **kwargs):
        """Execute API call on instance, with logging and error handling."""
        lookup_key = account_id or psn_username
        if instance["client"] is None or lookup_key not in instance["user_cache"]:
            instance["client"], instance["user_cache"][lookup_key] = (
                self._init_client_for_instance(instance, psn_username, account_id)
            )
            user = instance["user_cache"][lookup_key]
            start_time = time.time()
            self._record_call(instance["token"])
            try:
                time.sleep(self.rate_limit_delay)
                data = self._perform_endpoint_call(user, endpoint, **kwargs)
                log_api_call(
                    endpoint,
                    instance["token"],
                    kwargs.get("profile_id"),
                    200,
                    time.time() - start_time,
                )
                return data
            except requests.HTTPError as e:
                log_api_call(
                    endpoint,
                    instance["token"],
                    kwargs.get("profile_id"),
                    e.response.status_code,
                    time.time() - start_time,
                    str(e),
                )
                self._rollback_call(instance["token"])
                if e.response.status_code == 429:
                    self._handle_rate_limit(instance)
                raise

    def _init_client_for_instance(self, instance, psn_username, account_id=None):
        """Init client/user with custom rate_limit, preferring account_id."""
        start_time = time.time()
        try:
            rate_limit = Rate(
                self.max_calls_per_window, self.window_seconds * len(self.tokens)
            )
            client = PSNAWP(instance["token"], rate_limit=rate_limit)
            log_api_call(
                "client_init", instance["token"], None, 200, time.time() - start_time
            )
            self._record_call(instance["token"])
            logger.info(f"Client for instance {instance['instance_id']} initiated.")
            start_time = time.time()
            if account_id:
                user = client.user(account_id=account_id)
            else:
                user = client.user(online_id=psn_username)
            log_api_call(
                "user_fetch", instance["token"], None, 200, time.time() - start_time
            )
            self._record_call(instance["token"])
            instance["last_switch"] = time.time()
            logger.info(
                f"User {psn_username} fetched for instance {instance['instance_id']}"
            )
            return client, user
        except requests.HTTPError as e:
            log_api_call(
                "client_init_or_user",
                instance["token"],
                None,
                e.response.status_code,
                time.time() - start_time,
                str(e),
            )
            self._rollback_call(instance["token"])
            raise

    def _perform_endpoint_call(self, user, endpoint, **kwargs):
        """Dispatch endpoint call."""
        if endpoint == "get_profile_legacy":
            return user.get_profile_legacy()
        elif endpoint == "trophy_titles":
            return user.trophy_titles(**kwargs)
        elif endpoint == "trophies":
            return user.trophies(**kwargs)
        raise ValueError(f"Unknown endpoint: {endpoint}")

    def _record_call(self, token):
        """Record call timestamp for rolling window."""
        now = time.time()
        redis_client.zadd(f"token:{token}:timestamps", {now: now})

    def _rollback_call(self, token):
        """Remove last call timestamp on error."""
        now = time.time()
        redis_client.zremrangebyscore(f"token:{token}:timestamps", now - 1, now)

    def _handle_rate_limit(self, instance):
        """Handle 429: Park instance and reassign."""
        logger.warning(
            f"Rate limit hit for instance {instance['instance_id']}. Parking for 60s."
        )
        instance["last_health"] = 0
        time.sleep(60)
        instance["last_health"] = time.time()


def log_api_call(
    endpoint, token, profile_id, status_code, response_time, error_message=""
):
    """Log API call to APIAuditLog."""
    from .models import APIAuditLog

    APIAuditLog.objects.create(
        token_id=hashlib.sha256(token.encode()).hexdigest()[:64],
        ip_used=(
            requests.get("https://api.ipify.org", timeout=5).text
            if not error_message
            else "unknown"
        ),
        endpoint=endpoint,
        profile_id=profile_id,
        status_code=status_code,
        response_time=response_time,
        error_message=error_message,
        calls_remaining=max(
            0, 300 - int(redis_client.zcard(f"token:{token}:timestamps") or 0)
        ),
    )
