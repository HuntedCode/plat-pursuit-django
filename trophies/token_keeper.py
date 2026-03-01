import json
from random import choice
import time
import threading
import logging
import os
import atexit
from pyrate_limiter import Duration, Rate, Limiter, InMemoryBucket
import requests
from typing import Dict
from dataclasses import dataclass
from datetime import datetime, timedelta
from django.utils import timezone
from django.db import connection, transaction, OperationalError
from django.db.models import Sum
from django.db.models.functions import Coalesce
from psnawp_api import PSNAWP as BasePSNAWP
from psnawp_api.core.request_builder import RequestBuilder as BaseRequestBuilder
from psnawp_api.core.authenticator import Authenticator as BaseAuthenticator
from psnawp_api.core.psnawp_exceptions import PSNAWPForbiddenError
from psnawp_api.models.trophies.trophy_constants import PlatformType
from requests import HTTPError
from requests.exceptions import ConnectionError, Timeout
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type, wait_fixed
from .models import Profile, Game, Concept, TitleID, TrophyGroup, ProfileGame, EarnedTrophy
from .services.psn_api_service import PsnApiService
from .psn_manager import PSNManager
from trophies.util_modules.cache import redis_client, log_api_call
from trophies.util_modules.constants import TITLE_ID_BLACKLIST, TITLE_STATS_SUPPORTED_PLATFORMS
from trophies.util_modules.language import detect_asian_language
from trophies.util_modules.region import detect_region_from_details
from trophies.services.profile_stats_service import update_profile_games, update_profile_trophy_counts
from trophies.services.badge_service import check_profile_badges
from trophies.services.milestone_service import check_all_milestones_for_user

logger = logging.getLogger("psn_api")

class ProxiedRequestBuilder(BaseRequestBuilder):
    def __init__(self, common_headers, rate_limit, proxy_url=None):
        # DO NOT call super().__init__() - it creates an SQLite bucket which causes
        # "database is locked" errors in multi-threaded environments.
        # Instead, replicate initialization with thread-safe in-memory bucket.

        from typing import cast
        self.common_headers = cast("dict[str, str]", common_headers)

        # Create thread-safe limiter with in-memory bucket (not SQLite)
        bucket = InMemoryBucket([rate_limit])
        self.limiter = Limiter(bucket, raise_when_fail=False, max_delay=Duration.SECOND * 3)

        # Initialize session (same as base class)
        self.session = requests.Session()
        self.session.headers.update(self.common_headers)

        # Add proxy support
        if proxy_url:
            self.session.proxies = {'http': proxy_url, 'https': proxy_url}

class ProxiedAuthenticator(BaseAuthenticator):
    def __init__(self, npsso_cookie, common_headers, rate_limit, proxy_url=None):
        # DO NOT call super().__init__() - it creates a RequestBuilder with an SQLite
        # bucket that spawns a Leaker daemon thread. That thread persists even after we
        # replace request_builder, causing "database is locked" errors.
        # Instead, replicate the parent's initialization with our proxied builder.
        import uuid
        self.npsso_cookie = npsso_cookie
        self.common_headers = common_headers
        self.request_builder = ProxiedRequestBuilder(common_headers, rate_limit, proxy_url=proxy_url)
        self.token_response = None
        self.cid = str(uuid.UUID(int=uuid.getnode()))

class ProxiedPSNAWP(BasePSNAWP):
    def __init__(self, npsso_cookie, headers=None, rate_limit=None, proxy_url=None):
        random_ua = [
            "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/55.0.6598.1817 Mobile Safari/537.36",
            "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Mobile Safari/537.36",
            "Mozilla/5.0 (Linux; Android 5.0; SM-G900P Build/LRX21T) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/39.0.5707.1741 Mobile Safari/537.36",
            "Mozilla/5.0 (Android 14; Mobile; rv:137.0) Gecko/137.0 Firefox/137.0",
            "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/55.0.9318.1385 Mobile Safari/537.36",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 18_3_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/135.0.7049.83 Mobile/15E148 Safari/604.1",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Mobile/15E148 Safari/604.1",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 11_0 like Mac OS X) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/44.0.2469.1901 Mobile Safari/537.36",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 18_3_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.3.1 Mobile/15E148 Safari/604.1",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 18_3_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.3.1 Mobile/15E148 Safari/604.1",
        ]
        default_headers = {
            "User-Agent": choice(random_ua),
            "Accept-Language": "en-US,en;q=0.9",
            "Country": "US",
        }

        if rate_limit is None:
            rate_limit = Rate(1, Duration.SECOND * 3)

        self.authenticator = ProxiedAuthenticator(
            npsso_cookie=npsso_cookie,
            common_headers=default_headers,
            rate_limit=rate_limit,
            proxy_url=proxy_url
        )

@dataclass
class TokenInstance:
    instance_id: int
    token: str
    client: ProxiedPSNAWP
    user_cache: dict = None
    access_expiry: datetime = None
    refresh_expiry: datetime = None
    last_health: float = time.time()
    last_refresh: float = 0
    is_busy: bool = False
    last_error: str = None
    outbound_ip: str = None
    proxy_url: str = None
    group_id: int = None
    job_start_time: float = 0  # Track when current job started for stuck detection

    def __post_init__(self):
        if self.user_cache is None:
            self.user_cache = {}
        if self.access_expiry is None or self.refresh_expiry is None:
            self.update_expiry_times()
    
    def update_expiry_times(self):
        if self.client:
            auth = self.client.authenticator
            self.access_expiry = datetime.fromtimestamp(auth.access_token_expiration_time)
            # Only set refresh_expiry on first initialization. Proactive access token
            # refreshes create a new client (full re-auth), which recomputes
            # refresh_token_expires_at from time.time() -- overwriting the original
            # expiry and making all instances show identical values on the dashboard.
            if self.refresh_expiry is None:
                self.refresh_expiry = datetime.fromtimestamp(auth.refresh_token_expiration_time)

    def get_access_expiry_in_seconds(self):
        if self.access_expiry:
            return (self.access_expiry - datetime.now()).total_seconds()
        return -1
    
    def get_refresh_expiry_in_seconds(self):
        if self.refresh_expiry:
            return (self.refresh_expiry - datetime.now()).total_seconds()
        return -1
        
    def cleanup_cache(self, ttl_minutes=5):
        """Remove cache entries older than ttl_minutes."""
        now = datetime.now()
        expired = [
            key for key, entry in self.user_cache.items()
            if now - entry['timestamp'] > timedelta(minutes=ttl_minutes)
        ]
        for key in expired:
            del self.user_cache[key]
        logger.info(f"Cleaned {len(expired)} expired users from instance {self.instance_id}")

class TokenKeeper:
    """Singleton: Maintains 3 live PSNAWP instances and handles API requests via pub/sub."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._init()
        return cls._instance

    def _init(self):
        from dotenv import load_dotenv
        load_dotenv()
        self.tokens = os.getenv("PSN_TOKENS", "").split(",")
        self.health_interval = 60
        self.refresh_threshold = 300
        self.token_wait_interval = 120
        self.window_seconds = int(os.getenv("WINDOW_SECONDS", 900))
        self.max_calls_per_window = int(os.getenv("MAX_CALLS_PER_WINDOW", 300))
        self.max_jobs_per_profile = int(os.getenv("MAX_JOBS_PER_PROFILE", 3))
        self.stats_interval = 5
        self.machine_id = os.getenv("MACHINE_ID", "default")
        token_groups_str = os.getenv('TOKEN_GROUPS', os.getenv('PSN_TOKENS', ''))
        proxy_ips_str = os.getenv('PROXY_IPS', '')
        self.token_groups = [group.split(',') for group in token_groups_str.split('|') if group]
        self.proxy_ips = [proxy if proxy else None for proxy in proxy_ips_str.split("|") if proxy_ips_str]

        if not self.token_groups or any(len(group) != 3 for group in self.token_groups):
            raise ValueError("TOKEN GROUPS must be pipe-separated groups of exactly 3 comma-separated tokens each")
        
        if len(self.proxy_ips) > 0 and len(self.proxy_ips) != len(self.token_groups):
            logger.warning("PROXY_IPS count doesn't match TOKEN_GROUPS - using available proxies")

        self.group_instances = {}
        
        self._health_thread = None
        self._stats_thread = None
        self._job_workers = []

        # Database lock error tracking for automatic recovery
        self._db_lock_errors = []  # List of timestamps when DB lock errors occurred
        self._db_lock_threshold = int(os.getenv("DB_LOCK_THRESHOLD", 5))  # Errors before restart
        self._db_lock_window = int(os.getenv("DB_LOCK_WINDOW", 60))  # Window in seconds
        self._restart_cooldown = int(os.getenv("DB_LOCK_COOLDOWN", 30))  # Cooldown before restart
        self._shutdown_requested = False
        self._db_lock_lock = threading.Lock()  # Thread safety for error tracking

        running_key = f"token_keeper:running:{self.machine_id}"
        if redis_client.get(running_key):
            raise RuntimeError(f"TokenKeeper already running for machine {self.machine_id}. Clear Redis key '{running_key}' to force start.")
        self.initialize_groups()
        self._start_health_monitor()
        self._start_stats_publisher()
        self._start_job_workers()
        redis_client.set(running_key, "1", ex=3600)
        atexit.register(self._cleanup)
    
    def _publish_stats_loop(self):
        while not self._shutdown_requested:
            time.sleep(self.stats_interval)
            try:
                stats = self.stats
                stats_with_id = {"machine_id": self.machine_id, "instances": stats}
                redis_client.publish(f"token_keeper_stats:{self.machine_id}", json.dumps(stats_with_id))
                redis_client.set(f"token_keeper_latest_stats:{self.machine_id}", json.dumps(stats_with_id), ex=60)
            except Exception as e:
                logger.error(f"Error publishing stats: {e}")

    def _cleanup(self):
        """Clean up Redis stat on process exit."""
        logger.info("Cleaning up TokenKeeper Redis state")
        running_key = f"token_keeper:running:{self.machine_id}"
        redis_client.delete(running_key)
        for group_id, group in self.group_instances.items():
            for i in range(len(group['instances'])):
                redis_client.delete(f"token_keeper:instance:{self.machine_id}:{group_id}:{i}:token")
                redis_client.delete(f"instance_lock:{self.machine_id}:{group_id}:{i}")
                redis_client.delete(f"token_keeper:pending_refresh:{self.machine_id}:{group_id}:{i}")
        logger.info("TokenKeeper Redis state cleaned")

    def _record_db_lock_error(self):
        """Record a database lock error and trigger restart if threshold exceeded."""
        should_restart = False
        with self._db_lock_lock:
            if self._shutdown_requested:
                return  # Already shutting down, don't record more errors

            now = time.time()
            self._db_lock_errors.append(now)
            # Keep only errors within the tracking window
            self._db_lock_errors = [t for t in self._db_lock_errors if now - t < self._db_lock_window]

            error_count = len(self._db_lock_errors)
            logger.warning(f"Database lock error recorded ({error_count}/{self._db_lock_threshold} in {self._db_lock_window}s window)")

            if error_count >= self._db_lock_threshold:
                self._shutdown_requested = True
                should_restart = True

        # Perform slow restart OUTSIDE the lock so other threads aren't blocked
        if should_restart:
            self._initiate_restart()

    def _initiate_restart(self):
        """Gracefully shutdown, wait, and restart the TokenKeeper.

        _shutdown_requested must already be True before calling this method.
        This method performs slow operations (thread joins, sleep) and must
        NOT be called while holding _db_lock_lock.
        """
        logger.critical(f"Database lock threshold exceeded! Initiating restart in {self._restart_cooldown}s...")

        # Cleanup current state
        self._cleanup()

        # Wait for old threads to notice the shutdown flag and exit
        logger.info("Waiting for old threads to exit...")
        if self._health_thread and self._health_thread.is_alive():
            self._health_thread.join(timeout=self.health_interval + 5)
        if self._stats_thread and self._stats_thread.is_alive():
            self._stats_thread.join(timeout=self.stats_interval + 5)
        for worker in self._job_workers:
            if worker.is_alive():
                worker.join(timeout=5)
        self._job_workers = []

        # Wait for database to recover
        logger.info(f"Waiting {self._restart_cooldown}s for database to recover...")
        time.sleep(self._restart_cooldown)

        # Reset shutdown flag and error tracking (inside lock for thread safety)
        with self._db_lock_lock:
            self._shutdown_requested = False
            self._db_lock_errors = []

        logger.info("Reinitializing TokenKeeper...")
        self.initialize_groups()
        self._start_health_monitor()
        self._start_stats_publisher()
        self._start_job_workers()
        redis_client.set(f"token_keeper:running:{self.machine_id}", "1", ex=3600)

        logger.info("TokenKeeper restarted successfully after database lock recovery")

    def _start_health_monitor(self):
        """Start background thread for proactive health checks."""
        self._health_thread = threading.Thread(target=self._health_loop, daemon=True, name="tk-health")
        self._health_thread.start()
        logger.info("TokenKeeper health monitor started")

    def _start_stats_publisher(self):
        self._stats_thread = threading.Thread(target=self._publish_stats_loop, daemon=True, name="tk-stats")
        self._stats_thread.start()
        logger.info("TokenKeeper stats publisher started")

    def _start_job_workers(self):
        num_workers_per_group = 3
        total_workers = num_workers_per_group * len(self.token_groups)
        for i in range(total_workers):
            t = threading.Thread(target=self._job_worker_loop, daemon=True, name=f"tk-worker-{i}")
            t.start()
            self._job_workers.append(t)
        logger.info(f"Started {total_workers} job worker threads")
    
    def _health_loop(self):
        """Infinite loop: Check health every interval."""
        while not self._shutdown_requested:
            time.sleep(self.health_interval)
            redis_client.set(f"token_keeper:running:{self.machine_id}", "1", ex=3600)
            # Check for stuck instances on every health loop iteration
            self._check_stuck_instances()
            # Check for stuck syncing profiles on every health loop iteration
            self._check_stuck_syncing_profiles()
            self._check_high_sync_volume()
            for group_id, group in self.group_instances.items():
                for instance_id, inst in group['instances'].items():
                    self._check_and_refresh(inst)

    def initialize_groups(self):
        """Create groups of 3 live PSNAWP clients."""
        for group_id, tokens in enumerate(self.token_groups):
            proxy = self.proxy_ips[group_id] if group_id < len(self.proxy_ips) else None
            instances = {}
            for i, token in enumerate(tokens):
                start_time = time.time()
                try:
                    client = ProxiedPSNAWP(token, proxy_url=proxy)
                    self._record_call(token)
                    client.user(online_id='PlatPursuit') # Generates refresh tokens, etc.
                    inst = TokenInstance(
                        instance_id=i,
                        token=token,
                        client=client,
                        user_cache={},
                        proxy_url=proxy,
                        group_id=group_id,
                        last_health=time.time()
                    )
                except Exception as e:
                    inst = TokenInstance(
                        instance_id=i,
                        token=token,
                        client=None,
                        user_cache={},
                        proxy_url=proxy,
                        group_id=group_id,
                        last_health=0
                    )
                    logger.warning(f"Failed to init client for instance {i} | {token}")
                try:
                    session = requests.Session()
                    if inst.proxy_url:
                        session.proxies = {'http': inst.proxy_url, 'https': inst.proxy_url}
                    ip_response = session.get('https://api.ipify.org', timeout=2)
                    inst.outbound_ip = ip_response.text
                    logger.info(f"Group {group_id} Instance {inst.instance_id} using IP: {inst.outbound_ip}")
                except Exception as e:
                    logger.warning(f"Failed to fetch IP for group {group_id} instance {inst.instance_id}: {e}")
                    inst.outbound_ip = 'Unknown'
                instances[i] = inst
                redis_client.set(f"token_keeper:instance:{self.machine_id}:{group_id}:{i}:token", token)
                logger.info(f"Group {group_id} Instance {i} initialized with live client")
                log_api_call("client_init", token, None, 200, time.time() - start_time)
            self.group_instances[group_id] = {'instances': instances, 'proxy': proxy}

    def _is_healthy(self, inst : TokenInstance) -> bool:
        """Quick health check."""
        try:
            return inst.client is not None
        except:
            return False
    
    def _check_and_refresh(self, inst : TokenInstance):
        """Refresh token if time remaining less than refresh threshold and clean cache."""
        group_id = inst.group_id
        try:
            if inst.get_access_expiry_in_seconds() < self.refresh_threshold:
                if inst.is_busy:
                    redis_client.set(f"token_keeper:pending_refresh:{self.machine_id}:{group_id}:{inst.instance_id}", "1", ex=3600)
                    logger.debug(f"Group {group_id} Instance {inst.instance_id} needs refresh but is busy.")
                    return
                start = time.time()
                inst.client = ProxiedPSNAWP(inst.token, inst.proxy_url)
                inst.client.user(online_id='PlatPursuit') # Generates refresh tokens, etc.
                inst.user_cache = {}
                inst.update_expiry_times()
                inst.last_refresh = time.time()
                self._record_call(inst.token)
                # Log API call separately - DB errors here shouldn't mark instance unhealthy
                try:
                    log_api_call("keeper_refresh", inst.token, None, 200, time.time() - start)
                except Exception as log_err:
                    logger.warning(f"Failed to log API call for instance {inst.instance_id}: {log_err}")
                logger.info(f"Instance {inst.instance_id} refreshed proactively")
                try:
                    session = requests.Session()
                    if inst.proxy_url:
                        session.proxies = {'http': inst.proxy_url, 'https': inst.proxy_url}
                    ip_response = session.get('https://api.ipify.org', timeout=2)
                    inst.outbound_ip = ip_response.text
                    logger.info(f"Instance {inst.instance_id} using IP: {inst.outbound_ip}")
                except Exception as e:
                    logger.warning(f"Failed to fetch IP for instance {inst.instance_id}: {e}")
                    inst.outbound_ip = 'Unknown'
            inst.cleanup_cache()
        except OperationalError as db_err:
            # Database lock errors are transient - don't mark instance unhealthy
            logger.warning(f"Database error in health check for instance {inst.instance_id}: {db_err}")
            if "database is locked" in str(db_err).lower():
                self._record_db_lock_error()
        except Exception as e:
            logger.error(f"Health check failed for {inst.instance_id}: {e}")
            inst.last_error = f"{datetime.now().isoformat()} Refresh error: {str(e)}"
            inst.last_health = 0
        else:
            inst.last_health = time.time()

    # Job Assignment & Handling

    def _job_worker_loop(self):
        while not self._shutdown_requested:
            profile_id = None
            queue_name = None
            job_start = None
            job_type = None
            try:
                queue_b, job_json = redis_client.brpop(['orchestrator_jobs', 'high_priority_jobs', 'medium_priority_jobs', 'low_priority_jobs', 'bulk_priority_jobs'])
                queue_name = queue_b.decode()[:-5] # remove '_jobs'
                job_data = json.loads(job_json)
                job_type = job_data['job_type']
                args = job_data['args']
                profile_id = job_data['profile_id']
                job_start = time.time()
                logger.info(f"Starting job - {job_type} for profile {profile_id} from queue {queue_name}.")

                if job_type == 'sync_profile_data':
                    self._job_sync_profile_data(profile_id)
                elif job_type == 'sync_trophy_titles':
                    self._job_sync_trophy_titles(profile_id)
                elif job_type == 'sync_trophy_groups':
                    self._job_sync_trophy_groups(profile_id, args[0], args[1])
                elif job_type == 'sync_title_stats':
                    self._job_sync_title_stats(profile_id, args[0], args[1], args[2], args[3], args[4])
                elif job_type == 'sync_trophies':
                    self._job_sync_trophies(profile_id, args[0], args[1])
                elif job_type == 'profile_refresh':
                    self._job_profile_refresh(profile_id)
                elif job_type == 'sync_title_id':
                    self._job_sync_title_id(profile_id, args[0], args[1])
                elif job_type == 'sync_complete':
                    self._job_sync_complete(profile_id, args[0], args[1])
                elif job_type == 'handle_privacy_error':
                    self._job_handle_privacy_error(profile_id)
                else:
                    raise ValueError(f"Unknown job type: {job_type}")

                # Log slow jobs for monitoring
                job_duration = time.time() - job_start
                if job_duration > 300:  # 5 minutes
                    logger.warning(f"Slow job: {job_type} for profile {profile_id} took {job_duration:.1f}s")

                logger.info(f"Job: {job_type} - Profile: {profile_id} completed successfully in {job_duration:.1f}s")
            except OperationalError as db_err:
                err_msg = str(db_err).lower()
                if "deadlock detected" in err_msg:
                    logger.warning(
                        f"Deadlock detected in {job_type} for profile {profile_id}, "
                        f"re-queuing after 2s delay: {db_err}"
                    )
                    time.sleep(2)
                    if job_type and args is not None and profile_id:
                        PSNManager.assign_job(
                            job_type, args=args, profile_id=profile_id,
                            priority_override=queue_name
                        )
                elif "database is locked" in err_msg:
                    logger.error(f"Database error in job worker: {db_err}")
                    self._record_db_lock_error()
                else:
                    logger.error(f"Database error in job worker: {db_err}")
                self._check_stuck_instances()
            except Exception as e:
                logger.error(f"Error in job worker: {e}")
                # Reset any instances stuck in busy state for too long
                self._check_stuck_instances()
            finally:
                if profile_id and queue_name not in ('high_priority', 'orchestrator'):
                    self._complete_job(profile_id, queue_name)
                # Close stale DB connections to prevent pool exhaustion
                connection.close()

    @retry(stop=stop_after_attempt(5), wait=wait_fixed(0.2))
    def _complete_job(self, profile_id, queue_name):
        """Handle finished job, check for deferred."""
        if queue_name in ('low_priority', 'medium_priority', 'bulk_priority'):

            counter_key = f"profile_jobs:{profile_id}:{queue_name}"

            try:
                redis_client.decr(counter_key)
                current_jobs = int(redis_client.get(f"profile_jobs:{profile_id}:{queue_name}") or 0)
                if current_jobs <= 0:
                    redis_client.delete(f"profile_jobs:{profile_id}:{queue_name}")
                    redis_client.srem("active_profiles", profile_id)

                current_jobs = self._get_current_jobs_for_profile(profile_id)
                pending_key = f"pending_sync_complete:{profile_id}"
                if current_jobs <= 0 and redis_client.exists(pending_key):
                    # Don't trigger sync_complete if one is already running for this profile
                    sync_complete_key = f"sync_complete_in_progress:{profile_id}"
                    if redis_client.get(sync_complete_key):
                        logger.info(f"sync_complete already in progress for profile {profile_id}, leaving pending data for follow-up")
                    else:
                        raw_pending = redis_client.get(pending_key)
                        try:
                            pending_data = json.loads(raw_pending)
                            if not isinstance(pending_data, dict):
                                raise ValueError("Pending data is not a dictionary")
                            args = [pending_data['touched_profilegame_ids'], pending_data['queue_name']]
                            PSNManager.assign_job('sync_complete', args, profile_id, priority_override=pending_data['queue_name'])
                            redis_client.delete(pending_key)
                            logger.info(f"Triggered sync_complete for profile {profile_id}")
                        except (json.JSONDecodeError, ValueError, KeyError) as parse_err:
                            logger.error(f"Failed to parse pending_sync_complete for profile {profile_id}: {parse_err}")
            except Exception as e:
                logger.error(f"Error in _complete_job for profile {profile_id}: {e}")

    def _get_current_jobs_for_profile(self, profile_id):
        total = 0
        for queue in ['low_priority', 'medium_priority', 'bulk_priority']:
            total += int(redis_client.get(f"profile_jobs:{profile_id}:{queue}") or 0)
        return total

    def _check_stuck_instances(self):
        """Reset any token instances that have been stuck in busy state for too long."""
        stuck_threshold = 300  # 5 minutes
        now = time.time()
        for group_id, group in self.group_instances.items():
            for inst_id, inst in group['instances'].items():
                if inst.is_busy:
                    # Use job_start_time instead of last_health for accurate stuck detection
                    time_busy = now - inst.job_start_time if inst.job_start_time > 0 else 0
                    if time_busy > stuck_threshold:
                        logger.warning(f"Resetting stuck instance {group_id}-{inst_id} (busy for {time_busy:.1f}s)")
                        self._release_instance(inst)
                        inst.last_error = f"{datetime.now().isoformat()} Reset due to stuck busy state (job ran for {time_busy:.1f}s)"

    def _check_stuck_syncing_profiles(self):
        """Periodically check for profiles stuck in 'syncing' state with no pending jobs."""
        try:
            stuck_profiles = Profile.objects.filter(sync_status='syncing')
            stuck_count = 0

            for profile in stuck_profiles:
                # Grace period: skip profiles that started syncing recently
                sync_started_at = redis_client.get(f"sync_started_at:{profile.id}")
                if sync_started_at:
                    try:
                        if time.time() - float(sync_started_at) < 90:
                            continue
                    except (ValueError, TypeError):
                        pass

                # Skip profiles with pending orchestrator jobs (sync_trophy_titles
                # or profile_refresh hasn't run yet to create the real sync jobs)
                if redis_client.get(f"sync_orchestrator_pending:{profile.id}"):
                    continue

                # Check if there are any pending jobs for this profile
                current_jobs = self._get_current_jobs_for_profile(profile.id)

                # Check if a sync_complete job is already in progress for this profile
                sync_complete_key = f"sync_complete_in_progress:{profile.id}"
                if redis_client.get(sync_complete_key):
                    continue

                if current_jobs <= 0:
                    # No pending jobs, this profile is stuck - assign sync_complete
                    logger.warning(f"Found stuck syncing profile {profile.id}, assigning sync_complete job")
                    stuck_count += 1

                    # Check for pending sync_complete data in Redis
                    pending_key = f"pending_sync_complete:{profile.id}"
                    raw_pending = redis_client.get(pending_key)

                    if raw_pending:
                        try:
                            pending_data = json.loads(raw_pending)
                            touched_profilegame_ids = pending_data.get('touched_profilegame_ids', [])
                            queue_name = pending_data.get('queue_name', 'low_priority')
                        except (json.JSONDecodeError, ValueError):
                            touched_profilegame_ids = []
                            queue_name = 'low_priority'
                        redis_client.delete(pending_key)
                    else:
                        touched_profilegame_ids = []
                        queue_name = 'low_priority'

                    args = [touched_profilegame_ids, queue_name]
                    PSNManager.assign_job('sync_complete', args, profile.id, priority_override='orchestrator')

            if stuck_count > 0:
                logger.info(f"Stuck syncing check complete. Triggered sync_complete for {stuck_count} profiles.")
        except Exception as e:
            logger.error(f"Error checking stuck syncing profiles: {e}")

    def _check_high_sync_volume(self):
        """
        Detect high sync volume and set/clear a Redis flag for site-wide banner.

        Uses hysteresis to prevent rapid toggling:
        - Activates when >= ACTIVATE_THRESHOLD profiles have >= JOB_THRESHOLD pending jobs
        - Deactivates only when count drops below DEACTIVATE_THRESHOLD
        """
        ACTIVATE_THRESHOLD = 10
        DEACTIVATE_THRESHOLD = 5
        JOB_THRESHOLD = 200
        REDIS_KEY = 'site:high_sync_volume'
        TTL = 300  # 5-minute safety TTL; refreshed each health loop if still active

        try:
            active_ids = redis_client.smembers('active_profiles')
            pids = [p.decode() if isinstance(p, bytes) else str(p) for p in active_ids]

            heavy_count = 0
            if pids:
                pipe = redis_client.pipeline(transaction=False)
                for pid in pids:
                    pipe.get(f"profile_jobs:{pid}:low_priority")
                    pipe.get(f"profile_jobs:{pid}:medium_priority")
                results = pipe.execute()

                for i in range(0, len(results), 2):
                    total = int(results[i] or 0) + int(results[i + 1] or 0)
                    if total >= JOB_THRESHOLD:
                        heavy_count += 1

            existing = redis_client.get(REDIS_KEY)
            is_currently_active = existing is not None

            if not is_currently_active and heavy_count >= ACTIVATE_THRESHOLD:
                data = json.dumps({
                    'activated_at': time.time(),
                    'heavy_count': heavy_count
                })
                redis_client.set(REDIS_KEY, data, ex=TTL)
                logger.info(f"High sync volume detected: {heavy_count} profiles with {JOB_THRESHOLD}+ jobs. Banner activated.")

            elif is_currently_active and heavy_count >= DEACTIVATE_THRESHOLD:
                # Still above deactivation threshold: refresh TTL, update count
                try:
                    existing_str = existing.decode() if isinstance(existing, bytes) else existing
                    data = json.loads(existing_str)
                    data['heavy_count'] = heavy_count
                    redis_client.set(REDIS_KEY, json.dumps(data), ex=TTL)
                except (json.JSONDecodeError, ValueError):
                    redis_client.set(REDIS_KEY, existing, ex=TTL)

            elif is_currently_active and heavy_count < DEACTIVATE_THRESHOLD:
                redis_client.delete(REDIS_KEY)
                logger.info(f"High sync volume cleared: {heavy_count} heavy profiles (below deactivation threshold).")

        except Exception as e:
            logger.error(f"Error checking high sync volume: {e}")

    def _get_instance_for_job(self, job_type: str) -> TokenInstance:
        """Selects best instance for job with atomic acquisition using Redis locks."""
        start = time.time()
        while time.time() - start < self.token_wait_interval:
            instance_scores = {}
            for group_id, group in self.group_instances.items():
                for inst_id, inst in group['instances'].items():
                    if not inst.is_busy and self._is_healthy(inst) and not inst.last_health == 0:
                        key = (group_id, inst_id)
                        instance_scores[key] = self._get_calls_in_window(inst.token)

            if instance_scores:
                # Sort by score to try best instances first
                sorted_instances = sorted(instance_scores.items(), key=lambda x: x[1])

                for (group_id, inst_id), _ in sorted_instances:
                    lock_key = f"instance_lock:{self.machine_id}:{group_id}:{inst_id}"

                    # Atomic acquisition with Redis lock (expires in 5 min as safety net)
                    acquired = redis_client.set(lock_key, "1", nx=True, ex=300)

                    if acquired:
                        inst = self.group_instances[group_id]['instances'][inst_id]
                        # Double-check after acquiring lock
                        if not inst.is_busy:
                            inst.is_busy = True
                            inst.job_start_time = time.time()
                            return inst
                        else:
                            # Another thread got it, release lock
                            redis_client.delete(lock_key)

            logger.info("Waiting for token...")
            time.sleep(0.1)
        logger.error(f"No token available for job type '{job_type}' after {self.token_wait_interval}s.")
        raise RuntimeError(f"No token instance available for job type '{job_type}' after {self.token_wait_interval}s timeout")

    def _release_instance(self, instance: TokenInstance):
        """Safely release a token instance and its Redis lock."""
        if instance is None:
            return
        try:
            group_id = instance.group_id
            if group_id is not None:
                lock_key = f"instance_lock:{self.machine_id}:{group_id}:{instance.instance_id}"
                redis_client.delete(lock_key)
            instance.is_busy = False
            instance.job_start_time = 0
        except Exception as e:
            logger.error(f"Error releasing instance {instance.instance_id}: {e}")
            # Still try to mark as not busy even if Redis fails
            instance.is_busy = False
            instance.job_start_time = 0

    @retry(
        retry=retry_if_exception_type((ConnectionError, Timeout)),
        stop=stop_after_attempt(10),
        wait=wait_exponential(multiplier=1, min=4, max=30),
        reraise=True
    )
    def _execute_api_call(self, instance : TokenInstance, profile : Profile, endpoint : str, **kwargs):       
        start_time = time.time()
        try:
            lookup_key = profile.account_id if profile.account_id else profile.psn_username
            if lookup_key not in instance.user_cache:
                start = time.time()
                instance.user_cache[lookup_key] = {
                    "user": (instance.client.user(account_id=profile.account_id) if profile.account_id else instance.client.user(online_id=profile.psn_username)),
                    "timestamp": datetime.now()
                }
                self._record_call(instance.token)
                log_api_call('init_user', instance.token, profile.id, 200, time.time() - start)
            user = instance.user_cache[lookup_key]['user']

            self._record_call(instance.token)
            if endpoint == "get_profile_legacy":
                data = user.get_profile_legacy()
            elif endpoint == "get_region":
                data = user.get_region()
            elif endpoint == "trophy_titles":
                data = list(user.trophy_titles(**kwargs))
            elif endpoint == "title_stats":
                data = list(user.title_stats(**kwargs))
            elif endpoint == "trophies":
                if "include_progress" in kwargs:
                    self._record_call(instance.token)
                data = list(user.trophies(**kwargs))
            elif endpoint == "trophy_groups_summary":
                data = user.trophy_groups_summary(**kwargs)
            elif endpoint == "trophy_titles_for_title":
                data = list(user.trophy_titles_for_title(**kwargs))
            elif endpoint == "trophy_summary":
                data = user.trophy_summary()
            elif endpoint == "game_title":
                data = instance.client.game_title(**kwargs)
            else:
                raise ValueError(f"Unknown endpoint: {endpoint}")
            
            log_api_call(endpoint, instance.token, profile.id if profile else None, 200, time.time() - start_time)
            if endpoint not in ['get_profile_legacy', 'get_region']:
                profile.set_history_public_flag(True)
            return data
        except PSNAWPForbiddenError as e:
            if profile:
                profile.set_history_public_flag(False)
                PSNManager.handle_privacy_error(profile)
                logger.warning(f"Privacy error for profile {profile.id}.")
            log_api_call(endpoint, instance.token, profile.id if profile else None, 500, time.time() - start_time, str(e))
            self._rollback_call(instance.token)
            raise
        except HTTPError as e:
            log_api_call(endpoint, instance.token, profile.id if profile else None, e.response.status_code, time.time() - start_time, str(e))
            self._rollback_call(instance.token)
            raise
        except Exception as e:
            log_api_call(endpoint, instance.token, profile.id if profile else None, 500, time.time() - start_time, str(e))
            instance.last_error = f"{datetime.now().isoformat()} Error: {str(e)}"
            raise
        finally:
            # Release token after each API call so it can be used by other jobs
            self._release_instance(instance)


    def _get_calls_in_window(self, token : str) -> int:
        """Count API calls in rolling window."""
        now = time.time()
        redis_client.zremrangebyscore(f"token:{token}:{self.machine_id}:timestamps", 0, now - self.window_seconds)
        return redis_client.zcard(f"token:{token}:{self.machine_id}:timestamps")
    
    def _record_call(self, token : str) -> str:
        """Record API call timestamp. Returns the member key for potential rollback."""
        now = time.time()
        member = str(now)
        redis_client.zadd(f"token:{token}:{self.machine_id}:timestamps", {member: now})
        return member

    def _rollback_call(self, token : str):
        """Rollback the most recent API call counter entry."""
        key = f"token:{token}:{self.machine_id}:timestamps"
        # Remove the highest-scored (most recent) entry
        redis_client.zpopmax(key, count=1)
    
    def _handle_rate_limit(self, instance : TokenInstance):
        """Handle token rate limiting (429 error)."""
        logger.warning(f"Rate limit hit for instance {instance.instance_id}. Parking for 60s.")
        instance.last_health = 0
        time.sleep(60)
        instance.last_health = time.time()
        
    # Job Requests

    def _job_sync_complete(self, profile_id: int, touched_profilegame_ids: list[int], queue_name: str):
        sync_complete_key = f"sync_complete_in_progress:{profile_id}"

        # Atomic guard: only one sync_complete runs at a time per profile.
        # If another is already in progress, re-store pending data and bail out.
        if not redis_client.set(sync_complete_key, "1", nx=True, ex=1800):
            logger.info(f"sync_complete already in progress for profile {profile_id}, skipping duplicate")
            pending_key = f"pending_sync_complete:{profile_id}"
            pending_data = json.dumps({
                'touched_profilegame_ids': touched_profilegame_ids,
                'queue_name': queue_name
            })
            redis_client.set(pending_key, pending_data, ex=21600)
            return

        try:
            profile = Profile.objects.get(id=profile_id)
        except Profile.DoesNotExist:
            logger.error(f"Profile {profile_id} does not exist.")
            redis_client.delete(sync_complete_key)
            return
        job_type = 'sync_complete'

        try:
            # Brief buffer to ensure the last job's DB writes have fully committed.
            # Jobs use transaction.atomic() so writes commit before job completion,
            # but 1s covers any edge cases with connection pooling or replication lag.
            time.sleep(1)

            logger.info(f"Starting complete sync job for {profile_id}...")

            # Check profile heatlh
            logger.info(f"Starting health check for {profile_id}...")
            summary = self._execute_api_call(self._get_instance_for_job(job_type), profile, 'trophy_summary')
            tracked_trophies = PsnApiService.get_profile_trophy_summary(profile)

            earned = summary.earned_trophies
            summary_total = earned.bronze + earned.silver + earned.gold + earned.platinum
            total_tracked = tracked_trophies['total'] + profile.total_hiddens
            profilegame_total = ProfileGame.objects.filter(profile=profile).aggregate(earned=Coalesce(Sum('earned_trophies_count'), 0))['earned']

            logger.info(f"Profile {profile_id} health: Summary: {summary_total} | Tracked: {total_tracked} (Hidden: {profile.total_hiddens}) | Profilegame: {profilegame_total} | {summary_total == total_tracked}")

            if summary_total != total_tracked:
                trophy_titles_to_be_updated = []
                current_tracked_games = list(ProfileGame.objects.filter(profile=profile))
                page_size = 400
                limit = page_size
                offset = 0
                is_full = True
                has_mismatch = False
                while is_full:
                    titles = self._execute_api_call(self._get_instance_for_job(job_type), profile, 'trophy_titles', limit=limit, offset=offset, page_size=page_size)
                    for title in titles:
                        try:
                            game, tracked = PsnApiService.get_tracked_trophies_for_game(profile, title.np_communication_id)
                        except Game.DoesNotExist:
                            game, _, _ = PsnApiService.create_or_update_game(title)
                            tracked = {'total': 0}  # Initialize tracked if game was just created
                            # Health check only queues sync_trophies, which has no concept
                            # assignment pathway. Assign a default concept so the game
                            # is never left without one.
                            if game.concept is None:
                                try:
                                    default_concept = Concept.create_default_concept(game)
                                    game.add_concept(default_concept)
                                    logger.info(f"Health check: created default concept for {game.title_name}")
                                except Exception:
                                    logger.exception(f"Health check: failed to create default concept for {game.title_name}")

                        pgame = None
                        try:
                            pgame = ProfileGame.objects.get(profile=profile, game=game)
                            current_tracked_games.remove(pgame)
                        except ProfileGame.DoesNotExist:
                            pass
                        except ValueError:
                            # pgame already removed from current_tracked_games
                            pass

                        title_total = title.earned_trophies.bronze + title.earned_trophies.silver + title.earned_trophies.gold + title.earned_trophies.platinum
                        if tracked['total'] != title_total:
                            has_mismatch = True
                            trophy_titles_to_be_updated.append({'title': title, 'game': game})
                            logger.info(f"Mismatch for profile {profile_id} - {title.np_communication_id}: Tracked: {tracked['total']} | Title: {title_total}")
                        elif pgame and tracked['total'] != pgame.earned_trophies_count and pgame.id not in touched_profilegame_ids:
                            touched_profilegame_ids.append(pgame.id)
                            logger.warning(f"ProfileGame/tracked total mismatch, appending {pgame} to be updated. | Tracked: {tracked['total']} | PGame: {pgame.earned_trophies_count}")
                    is_full = len(titles) == page_size
                    limit += page_size
                    offset += page_size

                if len(current_tracked_games) > 0:
                    # Use bulk update instead of individual saves to reduce DB locks
                    hidden_game_ids = [pgame.game_id for pgame in current_tracked_games]
                    with transaction.atomic():
                        EarnedTrophy.objects.filter(
                            profile=profile,
                            trophy__game_id__in=hidden_game_ids
                        ).update(user_hidden=True)
                        ProfileGame.objects.filter(
                            profile=profile,
                            game_id__in=hidden_game_ids
                        ).update(user_hidden=True)

                if has_mismatch and len(trophy_titles_to_be_updated) > 0:
                    logger.info(
                        f"Health check for profile {profile_id}: {len(trophy_titles_to_be_updated)} games need re-sync"
                    )

                    # Re-enter syncing state with progress tracking
                    profile.reset_sync_progress()
                    profile.set_sync_status('syncing')

                    # Set up pending_sync_complete so a follow-up runs after these jobs finish.
                    # This ensures badges, milestones, challenges, etc. run AFTER the re-queued
                    # games have actually finished syncing (not on stale data).
                    pending_key = f"pending_sync_complete:{profile_id}"
                    pending_data = json.dumps({
                        'touched_profilegame_ids': touched_profilegame_ids,
                        'queue_name': 'orchestrator'
                    })
                    redis_client.set(pending_key, pending_data, ex=21600)

                    queued_count = 0
                    for title in trophy_titles_to_be_updated:
                        game = title['game']
                        platform = game.title_platform[0] if not game.title_platform[0] == 'PSPC' else game.title_platform[1]
                        if PSNManager.assign_sync_trophies(profile.id, game.np_communication_id, platform, priority_override='low_priority'):
                            queued_count += 1
                    profile.add_to_sync_target(queued_count * 2)  # sync_trophies increments by 2

                    # Early return: skip badge/milestone/challenge checks.
                    # The follow-up sync_complete (triggered by pending_sync_complete
                    # when all re-queued jobs finish) will handle all of that.
                    profile.last_profile_health_check = timezone.now()
                    profile.save(update_fields=['last_profile_health_check'])
                    return
                else:
                    profile.total_hiddens = summary_total - total_tracked if summary_total - total_tracked >= 0 else 0
                    profile.save(update_fields=['total_hiddens'])
                    logger.info(f"New total hiddens for profile {profile.id}: {summary_total - total_tracked}")

                # Check badges
                profile.last_profile_health_check = timezone.now()
                profile.save(update_fields=['last_profile_health_check'])

            elif total_tracked != profilegame_total:
                # EarnedTrophy data matches PSN, but ProfileGame stats are stale.
                # No PSN API calls needed: just queue all ProfileGames for recalculation.
                logger.info(
                    f"ProfileGame stats drift for profile {profile_id}: "
                    f"tracked={total_tracked}, profilegame={profilegame_total}. "
                    f"Recalculating stats (no resync needed)."
                )
                all_pg_ids = list(
                    ProfileGame.objects.filter(profile=profile)
                    .values_list('id', flat=True)
                )
                existing_set = set(touched_profilegame_ids)
                for pg_id in all_pg_ids:
                    if pg_id not in existing_set:
                        touched_profilegame_ids.append(pg_id)
                profile.last_profile_health_check = timezone.now()
                profile.save(update_fields=['last_profile_health_check'])

            logger.info(f"Updating plats for {profile_id}...")
            profile.update_plats()
            logger.info(f"Updating profilegame stats for {profile_id}...")
            PsnApiService.update_profilegame_stats(touched_profilegame_ids)
            logger.info(f"Checking profile badges for {profile_id}...")
            check_profile_badges(profile, touched_profilegame_ids)

            # Create consolidated badge notifications
            try:
                from notifications.services.deferred_notification_service import DeferredNotificationService
                DeferredNotificationService.create_badge_notifications(profile_id, profile=profile)
            except Exception as e:
                logger.error(f"Failed to create badge notifications for profile {profile_id}: {e}", exc_info=True)

            logger.info(f"ProfileGame Stats updated for {profile_id} successfully! | {len(touched_profilegame_ids)} profilegames updated")
            from trophies.milestone_constants import ALL_CALENDAR_TYPES, ALL_GENRE_TYPES
            # Challenge-specific types are excluded here because they're checked
            # separately by their respective check_*_challenge_progress() functions below
            check_all_milestones_for_user(profile, exclude_types=ALL_CALENDAR_TYPES | {'az_progress'} | ALL_GENRE_TYPES)
            logger.info(f"Milestones checked for {profile_id} successfully!")

            # Check A-Z challenge progress
            from trophies.services.challenge_service import check_az_challenge_progress, check_calendar_challenge_progress, check_genre_challenge_progress
            try:
                check_az_challenge_progress(profile)
            except Exception:
                logger.exception(f"Failed to check A-Z challenge progress for profile {profile_id}")

            # Check Calendar challenge progress
            try:
                check_calendar_challenge_progress(profile)
            except Exception:
                logger.exception(f"Failed to check calendar challenge progress for profile {profile_id}")

            # Check Genre challenge progress
            try:
                check_genre_challenge_progress(profile)
            except Exception:
                logger.exception(f"Failed to check genre challenge progress for profile {profile_id}")

            update_profile_trophy_counts(profile)
            profile.set_sync_status('synced')

            from trophies.services.timeline_service import invalidate_timeline_cache
            invalidate_timeline_cache(profile_id)

            logger.info(f"{profile.display_psn_username} account has finished syncing!")
        except Exception as e:
            logger.exception(f"Error during sync_complete for profile {profile_id}: {e}")
            try:
                profile.refresh_from_db()
                if not profile.set_sync_status('error'):
                    logger.warning(f"Profile {profile_id} was deleted during error recovery (no account_id).")
            except Profile.DoesNotExist:
                logger.warning(f"Profile {profile_id} no longer exists during error recovery.")
        finally:
            # Always clear the sync_complete in-progress flag, even on error
            redis_client.delete(sync_complete_key)
            redis_client.delete(f"sync_started_at:{profile_id}")

    def _job_handle_privacy_error(self, profile_id: int):
        try:
            profile = Profile.objects.get(id=profile_id)
        except Profile.DoesNotExist:
            logger.error(f"Profile {profile_id} does not exist.")
            return
        job_type = 'handle_privacy_error'

        time.sleep(3)

        if not profile.psn_history_public:
            profile.set_sync_status('error')
        logger.info('Privacy error handled.')

    def _job_sync_profile_data(self, profile_id: int) -> Profile:
        try:
            profile = Profile.objects.get(id=profile_id)
        except Profile.DoesNotExist:
            logger.error(f"Profile {profile_id} does not exist.")
            return
        job_type = 'sync_profile_data'
        original_profile_id = profile.id

        try:
            legacy = self._execute_api_call(self._get_instance_for_job(job_type), profile, 'get_profile_legacy')
        except Exception as e:
            profile.set_sync_status('error')
            raise
        is_public = not legacy['profile']['trophySummary']['level'] == 0

        # This may return a different profile if a merge occurred
        merged_profile = PsnApiService.update_profile_from_legacy(profile, legacy, is_public)

        # If profile ID changed, a merge occurred - delete the duplicate
        if merged_profile.id != original_profile_id:
            logger.info(f"Merge detected: profile {original_profile_id} merged into {merged_profile.id}. Attempting to delete duplicate.")

            # Use the safe deletion method
            deletion_success = PsnApiService.delete_duplicate_profile(original_profile_id, merged_profile.id)

            if not deletion_success:
                logger.warning(f"Could not auto-delete duplicate profile {original_profile_id}. It may have data that needs manual review.")

            # Continue sync with the merged profile
            profile = merged_profile

        try:
            region = self._execute_api_call(self._get_instance_for_job(job_type), profile, 'get_region')
        except Exception as e:
            profile.set_sync_status('error')
            raise
        PsnApiService.update_profile_region(profile, region)

    def _job_sync_trophy_titles(self, profile_id: int, force_title_stats:bool=False):
        # Clear the orchestrator pending flag now that this job is executing
        redis_client.delete(f"sync_orchestrator_pending:{profile_id}")
        try:
            profile = Profile.objects.get(id=profile_id)
        except Profile.DoesNotExist:
            logger.error(f"Profile {profile_id} does not exist.")
            return
        job_type = 'sync_trophy_titles'
        job_counter = 0

        trophy_titles = []
        page_size = 400
        limit = page_size
        offset = 0
        is_full = True

        while is_full:
            result = self._execute_api_call(self._get_instance_for_job(job_type), profile, 'trophy_titles', limit=limit, offset=offset, page_size=page_size)
            is_full = len(result) == page_size
            trophy_titles.extend(result)
            limit += page_size
            offset += page_size

        # FIRST PASS: Create/update games and calculate job count WITHOUT assigning jobs
        touched_profilegame_ids = []
        num_title_stats = 0
        games_needing_groups = []
        games_by_comm_id = {}  # Cache games to avoid re-fetching in second pass

        for title in trophy_titles:
            game, created, _ = PsnApiService.create_or_update_game(title)
            games_by_comm_id[title.np_communication_id] = game
            profile_game, _ = PsnApiService.create_or_update_profile_game(profile, game, title)
            touched_profilegame_ids.append(profile_game.id)
            for platform in game.title_platform:
                if platform in TITLE_STATS_SUPPORTED_PLATFORMS:
                    num_title_stats += 1
                    break
            title_defined_trophies_total = title.defined_trophies.bronze + title.defined_trophies.silver + title.defined_trophies.gold + title.defined_trophies.platinum

            # Check if this game needs trophy groups synced
            needs_groups = created or game.get_total_defined_trophies() != title_defined_trophies_total or not TrophyGroup.objects.filter(game=game).exists()
            if needs_groups:
                games_needing_groups.append(game)
                job_counter += 1  # sync_trophy_groups
            job_counter += 2  # sync_trophies (includes the +2 for include_progress)

        # Set target BEFORE assigning any jobs to prevent race condition
        profile.add_to_sync_target(job_counter)

        # Determine queue for sync_trophies: bulk_priority for whale accounts
        bulk_threshold = int(redis_client.get('sync:bulk_threshold') or 5000)
        trophy_queue = 'bulk_priority' if job_counter > bulk_threshold else None  # None = default (low_priority)
        if trophy_queue == 'bulk_priority':
            logger.info(f"Profile {profile_id}: {job_counter} jobs exceeds bulk threshold ({bulk_threshold}), using bulk_priority queue")

        # SECOND PASS: Now assign the jobs (using cached games from first pass)
        skipped = 0
        for title in trophy_titles:
            game = games_by_comm_id[title.np_communication_id]
            platform = game.title_platform[0] if not game.title_platform[0] == 'PSPC' else game.title_platform[1]

            if game in games_needing_groups:
                args = [game.np_communication_id, platform]
                PSNManager.assign_job('sync_trophy_groups', args, profile.id)
            queued = PSNManager.assign_sync_trophies(profile.id, game.np_communication_id, platform, priority_override=trophy_queue)
            if not queued:
                skipped += 1

        # Adjust target if any games were already queued from a prior pass
        if skipped > 0:
            logger.info(f"Profile {profile_id}: skipped {skipped} duplicate sync_trophies jobs")
            profile.add_to_sync_target(-(skipped * 2))

        update_profile_games(profile)

        # Assign jobs for title_stats
        if num_title_stats > 0:
            page_size = 20
            limit = page_size
            offset = 0
            for _ in range(num_title_stats // page_size):
                args=[limit, offset, page_size, False, force_title_stats]
                PSNManager.assign_job('sync_title_stats', args, profile_id)
                limit += page_size
                offset += page_size
            # Always assign the final page with is_last=True
            args=[limit, offset, page_size, True, force_title_stats]
            PSNManager.assign_job('sync_title_stats', args, profile_id)
        
        pending_key = f"pending_sync_complete:{profile_id}"
        pending_data = json.dumps({
            'touched_profilegame_ids': touched_profilegame_ids,
            'queue_name': 'orchestrator'
        })
        redis_client.set(pending_key, pending_data, ex=21600)

    def _job_sync_trophy_groups(self, profile_id: int, np_communication_id: str, platform: str):
        try:
            profile = Profile.objects.get(id=profile_id)
            game = Game.objects.get(np_communication_id=np_communication_id)
        except Profile.DoesNotExist:
            logger.error(f"Profile {profile_id} does not exist.")
            return
        except Game.DoesNotExist:
            logger.error(f"Game {np_communication_id} does not exist.")
            return
        job_type='sync_trophy_groups'
        
        trophy_groups_summary = self._execute_api_call(self._get_instance_for_job(job_type), profile, 'trophy_groups_summary', np_communication_id=np_communication_id, platform=PlatformType(platform))
        for group in trophy_groups_summary.trophy_groups:
            trophy_group, created = PsnApiService.create_or_update_trophy_groups_from_summary(game, group)

        # Sync concept-level trophy groups for the Community Hub
        if game.concept:
            from trophies.services.concept_trophy_group_service import ConceptTrophyGroupService
            ConceptTrophyGroupService.sync_for_concept(game.concept)
        else:
            logger.warning(f"Game {game.title_name} ({game.np_communication_id}) has no concept during trophy group sync.")

        profile.increment_sync_progress()
        logger.info(f"Trophy group summaries for {game.title_name} synced successfully!")

    def _job_sync_title_stats(self, profile_id: int, limit: int, offset: int, page_size: int, is_last: bool=False, force_all: bool=False):
        logger.info(f"Syncing title stats | Force All: {force_all}")
        try:
            profile = Profile.objects.get(id=profile_id)
        except Profile.DoesNotExist:
            logger.error(f"Profile {profile_id} does not exist.")
            return
        job_type = 'sync_title_stats'
        job_counter = 0

        title_stats = self._execute_api_call(self._get_instance_for_job(job_type), profile, 'title_stats', limit=limit, offset=offset, page_size=page_size)

        if is_last and len(title_stats) == page_size:
            args=[limit + page_size, offset + page_size, page_size, True, force_all]
            PSNManager.assign_job('sync_title_stats', args, profile_id)

        remaining_title_stats = []
        for stats in title_stats:
            found = PsnApiService.update_profile_game_with_title_stats(profile, stats)
            if force_all or (not found and stats.title_id not in TITLE_ID_BLACKLIST):
                remaining_title_stats.append(stats)
        
        if len(remaining_title_stats) > 0:
            trophy_titles_for_title = []
            page_size = min(5, len(remaining_title_stats))
            limit = page_size
            offset = 0
            while offset < len(remaining_title_stats):
                title_ids = []
                for title in remaining_title_stats[offset:limit]:
                    title_ids.append(title.title_id)
                
                logger.info(f"Calling trophy_titles_for_title... {title_ids}")
                result = self._execute_api_call(self._get_instance_for_job(job_type), profile, 'trophy_titles_for_title', title_ids=title_ids)
                trophy_titles_for_title.extend(result)
                limit += page_size
                offset += page_size
            
            for title in trophy_titles_for_title:
                try:
                    game = Game.objects.get(np_communication_id=title.np_communication_id)
                except Game.DoesNotExist:
                    logger.warning(f"Game with comm id {title.np_communication_id} does not exist.")
                    continue
                game.add_title_id(title.np_title_id)
                args = [title.np_title_id, title.np_communication_id]
                PSNManager.assign_job('sync_title_id', args=args, profile_id=profile.id, priority_override='medium_priority')
                job_counter += 1
            for stats in remaining_title_stats:
                PsnApiService.update_profile_game_with_title_stats(profile, stats)
            
            profile.add_to_sync_target(job_counter)

    def _job_sync_trophies(self, profile_id: int, np_communication_id: str, platform: str):
        try:
            profile = Profile.objects.get(id=profile_id)
            game = Game.objects.get(np_communication_id=np_communication_id, title_platform__contains=platform)
        except Profile.DoesNotExist:
            logger.error(f"Profile {profile_id} does not exist.")
            return
        except Game.DoesNotExist:
            logger.error(f"Game {np_communication_id} does not exist.")
            return

        # Prevent concurrent sync_trophies for the same game.
        # Health-check re-queuing can dispatch multiple high_priority jobs
        # for games sharing a concept, causing AB/BA deadlocks via
        # ShovelwareDetectionService's concept-sibling updates.
        lock_key = f"sync_trophies_lock:{np_communication_id}"
        acquired = redis_client.set(lock_key, f"{profile_id}", nx=True, ex=120)
        if not acquired:
            logger.info(
                f"sync_trophies for {np_communication_id} already in progress "
                f"(profile {profile_id}), skipping duplicate."
            )
            redis_client.srem(f"sync_queued_games:{profile_id}", np_communication_id)
            profile.increment_sync_progress(value=2)
            return

        try:
            self._do_sync_trophies(profile, game, np_communication_id, platform)
        finally:
            redis_client.delete(lock_key)
            redis_client.srem(f"sync_queued_games:{profile_id}", np_communication_id)

    def _do_sync_trophies(self, profile, game, np_communication_id: str, platform: str):
        """Execute the actual trophy sync work. Called under a per-game Redis lock."""
        job_type = 'sync_trophies'

        logger.info(f"Fetching trophies for profile {profile.id}, game {np_communication_id} on platform {platform}")
        trophies = self._execute_api_call(self._get_instance_for_job(job_type), profile, 'trophies', np_communication_id=np_communication_id, platform=PlatformType(platform), include_progress=True, trophy_group_id='all', page_size=500)
        # Process in batches to avoid long-running transactions that block other DB operations.
        # Suppress the EarnedTrophy pre_save signal during sync: the signal fires a SELECT per
        # save to track earned state, but during sync notifications are handled by
        # DeferredNotificationService and earned-flip detection is in create_or_update_earned_trophy_from_trophy_data.
        from trophies.sync_utils import sync_signal_suppressor
        batch_size = 50
        with sync_signal_suppressor():
            for i in range(0, len(trophies), batch_size):
                batch = trophies[i:i + batch_size]
                with transaction.atomic():
                    for trophy_data in batch:
                        trophy, _ = PsnApiService.create_or_update_trophy_from_trophy_data(game, trophy_data)
                        PsnApiService.create_or_update_earned_trophy_from_trophy_data(profile, trophy, trophy_data)
        profile.increment_sync_progress(value=2)

        # Create any pending platinum notifications for this game
        try:
            from notifications.services.deferred_notification_service import DeferredNotificationService
            DeferredNotificationService.create_platinum_notification_for_game(
                profile_id=profile.id,
                game_id=game.id
            )
        except Exception as e:
            logger.error(f"Failed to create platinum notification for game {game.id}: {e}", exc_info=True)
    
    def _job_sync_title_id(self, profile_id: str, title_id_str: str, np_communication_id: str):
        try:
            profile = Profile.objects.get(id=profile_id)
        except Profile.DoesNotExist:
            logger.error(f"Profile {profile_id} does not exist.")
            return

        try:
            game = Game.objects.get(np_communication_id=np_communication_id)
        except Game.DoesNotExist:
            logger.warning(f"Game {title_id_str} | {np_communication_id} not in database.")
            profile.increment_sync_progress()
            return

        title_id, created = TitleID.objects.get_or_create(
            title_id=title_id_str,
            defaults={
                'platform': game.title_platform[0] if game.title_platform else 'PS4',
                'region': 'IP',
            }
        )
        if created:
            logger.info(f"Auto-created TitleID {title_id_str} with platform={title_id.platform}, region=IP")

        job_type='sync_title_id'

        logger.info(f"Beginning sync for {title_id.title_id} | {np_communication_id}")
        try:

            # Resolve platform mismatch: trust the Game's platform over TitleID
            api_platform = title_id.platform
            if game.title_platform and title_id.platform not in game.title_platform:
                api_platform = game.title_platform[0]
                logger.warning(f"Platform mismatch for {title_id.title_id}: TitleID={title_id.platform}, Game={game.title_platform}. Using {api_platform}.")

            game_title = self._execute_api_call(self._get_instance_for_job(job_type), profile, 'game_title', title_id=title_id.title_id, platform=PlatformType(api_platform), account_id=profile.account_id, np_communication_id=game.np_communication_id)
            if game_title:
                # Fix mismatch at source if API succeeded with corrected platform
                if api_platform != title_id.platform:
                    old_platform = title_id.platform
                    title_id.platform = api_platform
                    title_id.save(update_fields=['platform'])
                    logger.info(f"Corrected TitleID {title_id.title_id} platform: {old_platform} -> {api_platform}")

                details = game_title.get_details()[0]
                error_code = details.get('errorCode', None)
                if error_code is None:
                    concept, _ = PsnApiService.create_concept_from_details(details)

                    release_date = details.get('defaultProduct', {}).get('releaseDate', None)
                    if release_date is None:
                        release_date = details.get('releaseDate', {}).get('date', '')
                    concept.update_release_date(release_date)
                    media_data = self._extract_media(details)
                    concept.update_media(media_data['all_media'], media_data['icon_url'], media_data['bg_url'])
                    game.add_concept(concept)
                    detected_region = detect_region_from_details(details)
                    if detected_region:
                        if title_id.region == 'IP':
                            title_id.region = detected_region
                            title_id.save(update_fields=['region'])
                        game.add_region(detected_region)
                    else:
                        game.add_region(title_id.region)
                    concept.add_title_id(title_id.title_id)
                    concept.check_and_mark_regional()
                    profile.increment_sync_progress()
                    logger.info(f"Title ID {title_id.title_id} - {concept.unified_title} sync'd successfully!")
                else:
                    profile.increment_sync_progress()
                    if game.concept is None:
                        region_code = detect_asian_language(game.title_name)
                        if not region_code == 'Unknown':
                            game.add_region(region_code)
                            game.is_regional = True
                            game.save(update_fields=['is_regional'])
                            logger.info(f"Game {game.title_name} detected as Asian regional.")
                        else:
                            logger.warning(f"Concept for {title_id.title_id} returned an error code.")
                        default_concept = Concept.create_default_concept(game)
                        game.add_concept(default_concept)
                        logger.info(f"Created default concept for {game.title_name}")
                    logger.info(f"Title ID {title_id.title_id} sync'd successfully!")
            else:
                profile.increment_sync_progress()
                logger.warning(f"Couldn't get game_title for Title ID {title_id.title_id}")
                if game.concept is None:
                    try:
                        region_code = detect_asian_language(game.title_name)
                        if region_code != 'Unknown':
                            game.add_region(region_code)
                            game.is_regional = True
                            game.save(update_fields=['is_regional'])
                            logger.info(f"Game {game.title_name} detected as Asian regional.")
                        default_concept = Concept.create_default_concept(game)
                        game.add_concept(default_concept)
                        logger.info(f"Created default concept for {game.title_name} (game_title was None)")
                    except Exception:
                        logger.exception(f"Failed to create default concept for {game.title_name} (Title ID {title_id.title_id})")
        except Exception as e:
            profile.increment_sync_progress()
            logger.exception(f"Error while syncing Title ID {title_id.title_id}: {e}")
            # Last-resort: ensure the game has a concept even if sync failed
            try:
                game.refresh_from_db()
                if game.concept is None:
                    default_concept = Concept.create_default_concept(game)
                    game.add_concept(default_concept)
                    logger.info(f"Exception recovery: created default concept for {game.title_name} (Title ID {title_id.title_id})")
            except Exception as recovery_err:
                logger.exception(f"Exception recovery also failed for {game.title_name} (Title ID {title_id.title_id}): {recovery_err}")

    def _extract_media(self, details: dict) -> list[dict]:
        """Extract and combine unique media (images/videos) from JSON, deduped by URL per type."""
        all_media = []
        seen = {}

        def add_item(item):
            item_type = item.get('type')
            url = item.get('url')
            if item_type and url:
                if item_type not in seen:
                    seen[item_type] = set()
                if url not in seen[item_type]:
                    seen[item_type].add(url)
                    all_media.append(item)

        default_media = details.get('defaultProduct', {}).get('media', {})
        for img in default_media.get('images', []):
            add_item(img)
        for vid in default_media.get('videos', []):
            add_item(vid)

        root_media = details.get('media', {})
        for img in root_media.get('images', []):
            add_item(img)
        for vid in root_media.get('videos', []):
            add_item(vid)

        all_media.sort(key=lambda x: (x.get('type', ''), x.get('url', '')))

        icon_url = ''
        for img in all_media:
            if img.get('type') == 'MASTER':
                icon_url = img.get('url')
        bg_url = ''
        for img in all_media:
            if img.get('type') == 'GAMEHUB_COVER_ART':
                bg_url = img.get('url')
            elif bg_url == '' and img.get('type') == 'BACKGROUND_LAYER_ART':
                bg_url = img.get('url')

        media_data = {
            'all_media': all_media,
            'icon_url': icon_url,
            'bg_url': bg_url
        }

        return media_data
    
    def _job_profile_refresh(self, profile_id: int):
        # Clear the orchestrator pending flag now that this job is executing
        redis_client.delete(f"sync_orchestrator_pending:{profile_id}")
        try:
            profile = Profile.objects.get(id=profile_id)
        except Profile.DoesNotExist:
            logger.error(f"Profile {profile_id} does not exist.")
            return
        job_type = 'profile_refresh'
        job_counter = 0

        last_sync = profile.last_synced
        PSNManager.assign_job('sync_profile_data', args=[], profile_id=profile.id)

        trophy_titles_to_be_updated = []
        page_size = 400
        limit = page_size
        offset = 0
        is_full = True
        end_found = False
        while not end_found and is_full:
            titles = self._execute_api_call(self._get_instance_for_job(job_type), profile, 'trophy_titles', limit=limit, offset=offset, page_size=page_size)
            is_full = len(titles) == page_size
            for title in titles:
                if title.last_updated_datetime > last_sync:
                    trophy_titles_to_be_updated.append(title)
                else:
                    end_found = True
                    break
            limit += page_size
            offset += page_size

        # FIRST PASS: Create/update games and calculate job count WITHOUT assigning jobs
        touched_profilegame_ids = []
        games_needing_groups = []

        for title in trophy_titles_to_be_updated:
            game, created, _ = PsnApiService.create_or_update_game(title)
            profile_game, _ = PsnApiService.create_or_update_profile_game(profile, game, title)
            touched_profilegame_ids.append(profile_game.id)
            title_defined_trophies_total = title.defined_trophies.bronze + title.defined_trophies.silver + title.defined_trophies.gold + title.defined_trophies.platinum

            # Check if this game needs trophy groups synced
            needs_groups = created or game.get_total_defined_trophies() != title_defined_trophies_total or not TrophyGroup.objects.filter(game=game).exists()
            if needs_groups:
                games_needing_groups.append(game)
                job_counter += 1  # sync_trophy_groups
            job_counter += 2  # sync_trophies (includes the +2 for include_progress)

        # Set target BEFORE assigning any jobs to prevent race condition
        profile.add_to_sync_target(job_counter)

        # Determine queue for sync_trophies: bulk_priority for whale accounts
        bulk_threshold = int(redis_client.get('sync:bulk_threshold') or 5000)
        trophy_queue = 'bulk_priority' if job_counter > bulk_threshold else 'medium_priority'
        if trophy_queue == 'bulk_priority':
            logger.info(f"Profile {profile_id}: {job_counter} refresh jobs exceeds bulk threshold ({bulk_threshold}), using bulk_priority queue")

        # SECOND PASS: Now assign the jobs
        skipped = 0
        for title in trophy_titles_to_be_updated:
            game = Game.objects.get(np_communication_id=title.np_communication_id)
            platform = game.title_platform[0] if not game.title_platform[0] == 'PSPC' else game.title_platform[1]

            if game in games_needing_groups:
                args = [game.np_communication_id, platform]
                PSNManager.assign_job('sync_trophy_groups', args, profile.id)
            queued = PSNManager.assign_sync_trophies(profile.id, game.np_communication_id, platform, priority_override=trophy_queue)
            if not queued:
                skipped += 1

        # Adjust target if any games were already queued
        if skipped > 0:
            logger.info(f"Profile {profile_id}: skipped {skipped} duplicate sync_trophies jobs in refresh")
            profile.add_to_sync_target(-(skipped * 2))

        update_profile_games(profile)
        job_counter = 0
        
        title_stats_to_be_updated = []
        page_size = 20
        limit = page_size
        offset = 0
        is_full = True
        end_found = False
        while not end_found and is_full:
            title_stats = self._execute_api_call(self._get_instance_for_job(job_type), profile, 'title_stats', limit=limit, offset=offset, page_size=page_size)
            is_full = len(title_stats) == page_size
            for stats in title_stats:
                if stats.last_played_date_time > last_sync:
                    title_stats_to_be_updated.append(stats)
                else:
                    end_found = True
                    break
            limit += page_size
            offset += page_size
        
        remaining_title_stats = []
        for stats in title_stats_to_be_updated:
            found = PsnApiService.update_profile_game_with_title_stats(profile, stats)
            if not found and stats.title_id not in TITLE_ID_BLACKLIST:
                remaining_title_stats.append(stats)
        
        if len(remaining_title_stats) > 0:
            trophy_titles_for_title = []
            page_size = min(5, len(remaining_title_stats))
            limit = page_size
            offset = 0
            while offset < len(remaining_title_stats):
                title_ids = []
                for title in remaining_title_stats[offset:limit]:
                    title_ids.append(title.title_id)
                
                logger.info(f"Calling trophy_titles_for_title... {title_ids}")
                result = self._execute_api_call(self._get_instance_for_job(job_type), profile, 'trophy_titles_for_title', title_ids=title_ids)
                trophy_titles_for_title.extend(result)
                limit += page_size
                offset += page_size
        
            for title in trophy_titles_for_title:
                try:
                    game = Game.objects.get(np_communication_id=title.np_communication_id)
                except Game.DoesNotExist:
                    logger.warning(f"Game with comm id {title.np_communication_id} does not exist.")
                    continue
                game.add_title_id(title.np_title_id)
                args = [title.np_title_id, title.np_communication_id]
                PSNManager.assign_job('sync_title_id', args=args, profile_id=profile.id)
                job_counter += 1
            for stats in remaining_title_stats:
                PsnApiService.update_profile_game_with_title_stats(profile, stats)
            
            profile.add_to_sync_target(job_counter)

        pending_key = f"pending_sync_complete:{profile_id}"
        pending_data = json.dumps({
            'touched_profilegame_ids': touched_profilegame_ids,
            'queue_name': 'orchestrator'
        })
        redis_client.set(pending_key, pending_data, ex=7200)

        # If no low/medium priority jobs were assigned, trigger sync_complete immediately
        # Otherwise it will never fire since _complete_job only checks pending for low/medium queues
        current_jobs = self._get_current_jobs_for_profile(profile_id)
        if current_jobs <= 0:
            logger.info(f"No pending jobs for profile {profile_id}, triggering sync_complete immediately")
            args = [touched_profilegame_ids, 'orchestrator']
            PSNManager.assign_job('sync_complete', args, profile_id, priority_override='orchestrator')
            redis_client.delete(pending_key)

    @property
    def stats(self) -> Dict:
        stats = {}
        for group_id, group in self.group_instances.items():
            for inst_id, inst in group['instances'].items():
                key = f"{group_id}-{inst_id}"
                if inst.client is None:
                    stats[key] = {
                        "group_id": group_id,
                        "busy": inst.is_busy,
                        "healthy": False,
                        "calls_in_window": 0,
                        "access_token_expiry_in": -1,
                        "refresh_token_expiry_in": -1,
                        "token_scopes": "none",
                        "npsso_cookie": "missing",
                        "uptime_seconds": 0,
                        "last_error": inst.last_error,
                        "pending_refresh": False,
                        "outbound_ip": inst.outbound_ip,
                    }
                    continue
                auth = inst.client.authenticator
                stats[key] = {
                    "group_id": group_id,
                    "busy": inst.is_busy,
                    "healthy": time.time() - inst.last_health < self.health_interval,
                    "calls_in_window": self._get_calls_in_window(inst.token),
                    "access_token_expiry_in": inst.get_access_expiry_in_seconds(),
                    "refresh_token_expiry_in": inst.get_refresh_expiry_in_seconds(),
                    "token_scopes": auth.token_response.get("scope", "unknown") if auth.token_response else "none",
                    "npsso_cookie": "present" if auth.npsso_cookie else "missing",
                    "uptime_seconds": time.time() - inst.last_health if inst.last_health > 0 else 0,
                    "last_error": inst.last_error,
                    "pending_refresh": bool(redis_client.get(f"token_keeper:pending_refresh:{self.machine_id}:{group_id}:{inst_id}")),
                    "outbound_ip": inst.outbound_ip,
                }
        return stats

token_keeper = TokenKeeper()