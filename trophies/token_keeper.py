import json
import re
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
from django.db.models import F
from psnawp_api import PSNAWP as BasePSNAWP
from psnawp_api.core.request_builder import RequestBuilder as BaseRequestBuilder
from psnawp_api.core.authenticator import Authenticator as BaseAuthenticator
from psnawp_api.core.psnawp_exceptions import PSNAWPForbiddenError, PSNAWPServerError
from psnawp_api.models.trophies.trophy_constants import PlatformType
from requests import HTTPError
from requests.exceptions import ConnectionError, Timeout
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type, wait_fixed
from .models import Profile, Game, Concept, TitleID, TrophyGroup, ProfileGame, EarnedTrophy, ScoutAccount
from .services.psn_api_service import PsnApiService
from .psn_manager import PSNManager
from trophies.util_modules.cache import redis_client, log_api_call
from trophies.util_modules.constants import TITLE_ID_BLACKLIST, TITLE_STATS_SUPPORTED_PLATFORMS
from trophies.util_modules.language import detect_asian_language
from trophies.util_modules.region import detect_region_from_details
from trophies.services.profile_stats_service import update_profile_games, update_profile_trophy_counts
from trophies.services.badge_service import check_profile_badges
from trophies.services.milestone_service import check_all_milestones_for_user
from trophies.services.concept_anchor_service import try_anchor_new_game

logger = logging.getLogger("psn_api")


class PSNOutageError(Exception):
    """Raised when PSN API returns a 5xx gateway error or the circuit breaker is open."""
    pass


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
        # Periodic cache sweep is mostly noise when nothing was expired. Only
        # surface it at INFO when we actually evicted entries.
        if expired:
            logger.info(f"Cleaned {len(expired)} expired users from instance {self.group_id}-{self.instance_id}")
        else:
            logger.debug(f"No expired users to clean from instance {self.group_id}-{self.instance_id}")

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
        self.health_interval = 60
        self.refresh_threshold = 300
        self.token_wait_interval = 120
        self.window_seconds = int(os.getenv("WINDOW_SECONDS", 900))
        self.max_calls_per_window = int(os.getenv("MAX_CALLS_PER_WINDOW", 300))
        self.max_jobs_per_profile = int(os.getenv("MAX_JOBS_PER_PROFILE", 3))
        self.stats_interval = 5
        self.machine_id = os.getenv("MACHINE_ID", "default")
        token_groups_str = os.getenv('TOKEN_GROUPS', '')
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

        # PSN outage circuit breaker
        self._psn_outage_active = False
        self._psn_outage_lock = threading.Lock()
        # Half-open recovery state: count of consecutive successful probes
        # while the breaker is open. Only touched by the health thread, so
        # no lock needed. Banner stays up until this hits
        # PSN_OUTAGE_RECOVERY_THRESHOLD, preventing flapping during partial
        # PSN outages where a single lucky probe used to clear the flag.
        self._psn_outage_probe_successes = 0

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
                stats_with_id = {
                    "machine_id": self.machine_id,
                    "instances": stats,
                }
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
        current_thread = threading.current_thread()
        for worker in self._job_workers:
            if worker is current_thread:
                continue
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

    def _wait_for_db(self, max_wait=120):
        """Block until the database is accepting connections, up to max_wait seconds.

        Used after transient DB connection errors to prevent workers from
        burning through the entire job queue while the database is down.
        If the DB does not recover within max_wait, records an error to
        feed into the restart threshold system.
        Returns True if DB recovered, False if max_wait exceeded.
        """
        start = time.time()
        wait = 2
        while not self._shutdown_requested and (time.time() - start) < max_wait:
            try:
                connection.close()
                connection.ensure_connection()
                logger.info("Database connection restored.")
                return True
            except OperationalError:
                logger.warning(f"DB still unavailable, retrying in {wait}s...")
                time.sleep(wait)
                wait = min(wait * 2, 30)
        if not self._shutdown_requested:
            logger.error(f"DB did not recover within {max_wait}s")
            self._record_db_lock_error()
        return False

    def _record_psn_5xx(self, status_code: int):
        """Record a PSN 5xx gateway error and trip circuit breaker if threshold met."""
        REDIS_KEY = 'psn:5xx_timestamps'
        WINDOW = 60
        THRESHOLD = 5

        try:
            now = time.time()
            pipe = redis_client.pipeline()
            pipe.zadd(REDIS_KEY, {f"{now}:{status_code}": now})
            pipe.zremrangebyscore(REDIS_KEY, 0, now - WINDOW)
            pipe.zcard(REDIS_KEY)
            pipe.expire(REDIS_KEY, WINDOW * 2)
            results = pipe.execute()
            count = results[2]

            if count >= THRESHOLD and not self._psn_outage_active:
                self._trip_psn_outage()
        except Exception as e:
            logger.error(f"Error recording PSN 5xx: {e}")

    def _trip_psn_outage(self):
        """Activate the PSN outage circuit breaker."""
        with self._psn_outage_lock:
            if self._psn_outage_active:
                return
            self._psn_outage_active = True

        data = json.dumps({
            'activated_at': time.time(),
            'machine_id': self.machine_id,
        })
        redis_client.set('site:psn_outage', data, ex=600)
        logger.critical(
            "PSN OUTAGE DETECTED: Circuit breaker tripped. "
            "Profiles will not be marked as error during outage."
        )

    def _handle_outage_recovery(self, profile):
        """Reset a profile that failed due to PSN outage.

        Flips sync_status back to 'synced' and clears progress fields so
        the profile isn't stuck in 'syncing'. Crucially does NOT touch
        last_synced: the previous successful sync's timestamp stays
        accurate for user-facing display, and the cron will pick the
        profile up on its normal tier cadence (premium: 6h, basic: 12h,
        etc.) once it crosses the threshold. Users who want an immediate
        retry can trigger a manual sync from the UI.
        """
        try:
            profile.refresh_from_db()
            if profile.sync_status == 'syncing':
                profile.sync_status = 'synced'
                profile.sync_progress_value = 0
                profile.sync_progress_target = 0
                profile.save(update_fields=[
                    'sync_status',
                    'sync_progress_value', 'sync_progress_target',
                ])
                # Clean up sync-related Redis keys
                redis_client.delete(f"sync_started_at:{profile.id}")
                redis_client.delete(f"sync_orchestrator_pending:{profile.id}")
                redis_client.srem('active_profiles', str(profile.id))
                logger.info(
                    f"PSN outage recovery: profile {profile.id} reset to synced"
                )
        except Profile.DoesNotExist:
            logger.warning(
                f"Profile {profile.id} no longer exists during PSN outage recovery"
            )
        except Exception as e:
            logger.error(f"Error during PSN outage recovery for profile {profile.id}: {e}")

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
            self._check_psn_outage()
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
            err_msg = str(db_err).lower()
            logger.warning(f"Database error in health check for instance {inst.instance_id}: {db_err}")
            if "database is locked" in err_msg or "lock timeout" in err_msg:
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
            args = None
            requeued = False
            try:
                queue_b, job_json = redis_client.brpop(['orchestrator_jobs', 'high_priority_jobs', 'medium_priority_jobs', 'low_priority_jobs', 'bulk_priority_jobs'])
                queue_name = queue_b.decode()[:-5] # remove '_jobs'
                job_data = json.loads(job_json)
                job_type = job_data['job_type']
                args = job_data['args']
                profile_id = job_data['profile_id']
                job_start = time.time()
                logger.info(f"[profile {profile_id}] {job_type} START queue={queue_name}")

                if job_type == 'sync_profile_data':
                    self._job_sync_profile_data(profile_id)
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
                    logger.warning(f"[profile {profile_id}] {job_type} SLOW dur={job_duration:.1f}s")

                logger.info(f"[profile {profile_id}] {job_type} DONE dur={job_duration:.1f}s")
            except OperationalError as db_err:
                err_msg = str(db_err).lower()
                is_lock_error = "deadlock detected" in err_msg or "lock timeout" in err_msg
                is_connection_error = any(phrase in err_msg for phrase in [
                    "connection failed", "the connection is closed",
                    "not yet accepting connections", "could not connect",
                    "server closed the connection", "connection refused",
                    "connection timed out", "database is locked",
                ])

                if is_lock_error or is_connection_error:
                    if is_lock_error:
                        error_type = "Deadlock" if "deadlock" in err_msg else "Lock timeout"
                        delay = 2
                    else:
                        error_type = "DB connection error"
                        delay = 5

                    logger.warning(
                        f"{error_type} in {job_type} for profile {profile_id}, "
                        f"re-queuing after {delay}s delay: {db_err}"
                    )
                    self._record_db_lock_error()
                    time.sleep(delay)
                    if job_type and args is not None and profile_id and queue_name:
                        PSNManager.assign_job(
                            job_type, args=args, profile_id=profile_id,
                            priority_override=queue_name, skip_counter=True
                        )
                        requeued = True

                    # Block until DB is back before popping more jobs
                    if is_connection_error:
                        self._wait_for_db()
                else:
                    logger.error(f"Database error in job worker: {db_err}")
                self._check_stuck_instances()
            except PSNOutageError:
                logger.warning(
                    f"PSN outage during {job_type} for profile {profile_id}, "
                    f"applying outage recovery"
                )
                if profile_id:
                    try:
                        profile = Profile.objects.get(id=profile_id)
                        self._handle_outage_recovery(profile)
                    except Profile.DoesNotExist:
                        pass
            except Exception as e:
                logger.error(f"Error in job worker: {e}")
                # Reset any instances stuck in busy state for too long
                self._check_stuck_instances()
            finally:
                if profile_id and queue_name not in ('high_priority', 'orchestrator') and not requeued:
                    self._complete_job(profile_id, queue_name)
                # Django's CONN_MAX_AGE (600s) handles stale connection recycling
                # automatically. Avoid closing here: each close() forces a new
                # TCP+TLS handshake on the next query, which is expensive at
                # scale (24 workers = dozens of TLS handshakes/sec on the DB).
                pass

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
                        logger.info(f"[profile {profile_id}] sync_complete already in progress; pending data preserved")
                    else:
                        raw_pending = redis_client.get(pending_key)
                        try:
                            pending_data = json.loads(raw_pending)
                            if not isinstance(pending_data, dict):
                                raise ValueError("Pending data is not a dictionary")
                            args = [pending_data['touched_profilegame_ids'], pending_data['queue_name']]
                            PSNManager.assign_job('sync_complete', args, profile_id, priority_override=pending_data['queue_name'])
                            redis_client.delete(pending_key)
                            logger.debug(f"[profile {profile_id}] pending sync_complete triggered")
                        except (json.JSONDecodeError, ValueError, KeyError) as parse_err:
                            logger.error(f"[profile {profile_id}] pending_sync_complete parse failed: {parse_err}")
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
        # Only one TK instance should run this check per cycle
        if not redis_client.set("stuck_sync_check_lock", self.machine_id, nx=True, ex=90):
            return
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

                # Skip profiles with pending orchestrator jobs (profile_refresh
                # hasn't run yet to create the real sync jobs).
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

    def _check_psn_outage(self):
        """Check and manage PSN outage state.

        Uses a half-open recovery pattern. While the breaker is open we
        probe PSN once per health tick (60s) and require
        ``RECOVERY_THRESHOLD`` consecutive successes before clearing the
        banner. A single failed probe resets the counter, snapping us back
        to fully open. This prevents the flapping observed during partial
        PSN outages where lucky single-probe successes used to clear the
        flag while user-facing syncs kept failing.

        When the outage is not active: sync in-memory flag and counter
        from Redis (handles cross-machine detection).
        """
        REDIS_KEY = 'site:psn_outage'
        TTL = 600
        RECOVERY_THRESHOLD = 3

        try:
            existing = redis_client.get(REDIS_KEY)

            if existing:
                recovered = self._probe_psn_api()
                redis_client.expire(REDIS_KEY, TTL)

                if recovered:
                    self._psn_outage_probe_successes += 1
                    if self._psn_outage_probe_successes >= RECOVERY_THRESHOLD:
                        redis_client.delete(REDIS_KEY)
                        redis_client.delete('psn:5xx_timestamps')
                        with self._psn_outage_lock:
                            self._psn_outage_active = False
                        self._psn_outage_probe_successes = 0
                        logger.info(
                            "PSN OUTAGE CLEARED: %d consecutive probe successes. "
                            "Circuit breaker reset.",
                            RECOVERY_THRESHOLD,
                        )
                    else:
                        with self._psn_outage_lock:
                            self._psn_outage_active = True
                        logger.info(
                            "PSN outage probe succeeded (%d/%d). Banner stays up.",
                            self._psn_outage_probe_successes,
                            RECOVERY_THRESHOLD,
                        )
                else:
                    self._psn_outage_probe_successes = 0
                    with self._psn_outage_lock:
                        self._psn_outage_active = True
                    logger.warning("PSN outage still active. Probe failed.")
            else:
                with self._psn_outage_lock:
                    self._psn_outage_active = False
                self._psn_outage_probe_successes = 0
        except Exception as e:
            logger.error(f"Error checking PSN outage state: {e}")

    def _probe_psn_api(self) -> bool:
        """Probe PSN with two endpoints to check if the service has recovered.

        Tests ``trophy_summary`` (lightweight) AND ``trophy_titles`` (heavier
        title-list call) so partial outages where one service is healthy
        but the other is degraded still register as a failed probe. The
        title-list call uses ``limit=1`` to keep the probe cheap. Returns
        True only if BOTH calls succeed.
        """
        try:
            test_profile = Profile.objects.filter(
                account_id__isnull=False,
                sync_status='synced',
            ).first()

            if not test_profile:
                logger.warning("No suitable profile for PSN probe.")
                return False

            instance = None
            for group_id, group in self.group_instances.items():
                for inst_id, inst in group['instances'].items():
                    if (not inst.is_busy
                            and self._is_healthy(inst)
                            and inst.last_health != 0):
                        instance = inst
                        break
                if instance:
                    break

            if not instance:
                logger.warning("No idle instance for PSN probe.")
                return False

            # Direct API call bypassing _execute_api_call (which would
            # short-circuit due to the outage flag).
            # Note: we don't formally acquire the instance (no is_busy/lock),
            # so no release needed. This is a quick, non-blocking probe.
            lookup_key = test_profile.account_id
            if lookup_key not in instance.user_cache:
                instance.user_cache[lookup_key] = {
                    "user": instance.client.user(
                        account_id=test_profile.account_id
                    ),
                    "timestamp": datetime.now(),
                }
            user = instance.user_cache[lookup_key]['user']
            user.trophy_summary()
            # Second endpoint: catches partial outages where trophy_summary
            # is healthy but the heavier title-list service is degraded.
            list(user.trophy_titles(limit=1))

            logger.info(f"PSN probe succeeded for profile {test_profile.id}")
            return True
        except Exception as e:
            logger.debug(f"PSN probe failed: {e}")
            return False

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
        # Short-circuit if PSN is known to be down
        if self._psn_outage_active:
            self._release_instance(instance)
            raise PSNOutageError("PSN API is currently unavailable (circuit breaker open)")

        start_time = time.time()
        try:
            # game_title / game_details operate on instance.client directly and
            # don't need a User object, so skip the init_user warm-up for those.
            if endpoint not in ("game_title", "game_details"):
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
            elif endpoint == "trophy_titles_with_count":
                # Fingerprint helper for the unified profile_refresh orchestrator.
                # Fetches the first page of trophy_titles and returns
                # (titles_list, total_item_count). totalItemCount is metadata in
                # PSN's response (the global count of visible titles for the
                # account, identical on every page).
                # See docs/architecture/sync-architecture.md.
                iterator = user.trophy_titles(**kwargs)
                titles = list(iterator)
                data = (titles, iterator._total_item_count)
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
            elif endpoint == "game_details":
                # Build a GameTitle bound to THIS instance's authenticator so the
                # get_details HTTP call rides this instance's pyrate_limiter bucket,
                # not some other instance's that happened to originate the object.
                game_title_obj = instance.client.game_title(
                    title_id=kwargs["title_id"],
                    platform=kwargs["platform"],
                    account_id=kwargs["account_id"],
                    np_communication_id=kwargs["np_communication_id"],
                )
                result = game_title_obj.get_details(country=kwargs["country"], language=kwargs["language"])
                data = result[0] if result else {}
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
            status_code = e.response.status_code if hasattr(e, 'response') and e.response is not None else 0
            log_api_call(endpoint, instance.token, profile.id if profile else None, status_code, time.time() - start_time, str(e))
            self._rollback_call(instance.token)
            if status_code in (502, 503, 504):
                self._record_psn_5xx(status_code)
                raise PSNOutageError(
                    f"PSN service unavailable ({status_code})"
                ) from e
            raise
        except PSNAWPServerError as e:
            # psnawp raises PSNAWPServerError (not HTTPError) for 5xx responses.
            # Extract status code from message if possible (e.g., "Error 503 - ...")
            match = re.search(r'(\d{3})', str(e))
            status_code = int(match.group(1)) if match else 503
            log_api_call(endpoint, instance.token, profile.id if profile else None, status_code, time.time() - start_time, str(e))
            self._rollback_call(instance.token)
            self._record_psn_5xx(status_code)
            raise PSNOutageError(
                f"PSN service unavailable ({status_code})"
            ) from e
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

    # PSN returns sparse metadata for Asia-only titles when queried with the default
    # US/en-US storefront (the title doesn't exist in that storefront). Retrying the
    # concept-details lookup against Asian storefronts usually recovers the data.
    ASIAN_REGION_FALLBACKS = (
        ('JP', 'ja-JP'),
        ('HK', 'en-HK'),
        ('KR', 'ko-KR'),
        ('TW', 'zh-Hant'),
    )

    @staticmethod
    def _details_is_populated(details):
        """Return True if a PSN details dict carries usable concept metadata."""
        if not details:
            return False
        if details.get('errorCode') is not None:
            return False
        if not (details.get('name') or details.get('nameEn')):
            return False
        return True

    def _get_details_with_region_fallback(self, title_id, platform, account_id, np_communication_id, profile, job_type):
        """Fetch concept details, trying US/en-US first then Asian-region fallbacks.

        Each attempt acquires a freshly-picked instance via _execute_api_call with
        the 'game_details' endpoint, so every request rides its own rate-limit
        bucket (distributing load across instances) and rate-limit accounting is
        charged to the instance that actually issued the HTTP call.

        Returns (details: dict, used_fallback_region: bool). The flag tells the caller
        whether the returned payload came from a non-default storefront, so mutations
        to existing concepts can be gated off per the Asian-title rule.
        """
        call_kwargs = {
            "title_id": title_id,
            "platform": PlatformType(platform),
            "account_id": account_id,
            "np_communication_id": np_communication_id,
        }

        def _attempt(country, language):
            # PSNOutageError intentionally propagates to the worker loop for
            # circuit-breaker handling; only swallow per-attempt non-outage errors.
            try:
                return self._execute_api_call(
                    self._get_instance_for_job(job_type),
                    profile,
                    'game_details',
                    country=country,
                    language=language,
                    **call_kwargs,
                )
            except PSNOutageError:
                raise
            except Exception:
                logger.exception(f"get_details {country}/{language} failed")
                return {}

        details = _attempt('US', 'en-US')
        if self._details_is_populated(details):
            return details, False

        logger.info("Primary US/en-US response sparse; walking Asian-region fallbacks")

        for country, language in self.ASIAN_REGION_FALLBACKS:
            candidate = _attempt(country, language)
            if self._details_is_populated(candidate):
                logger.info(f"Region fallback {country}/{language}: populated concept details")
                return candidate, True
            logger.info(f"Region fallback {country}/{language}: sparse, continuing")

        logger.warning("All Asian-region fallbacks returned sparse responses; using primary result")
        return details, False

    # Job Requests

    def _job_sync_complete(self, profile_id: int, touched_profilegame_ids: list[int], queue_name: str):
        sync_complete_key = f"sync_complete_in_progress:{profile_id}"
        # B1: Surface sub-phase progress to the UI. Each phase boundary writes
        # a machine-readable phase string to this key; ProfileSyncStatusView
        # exposes it as `finalize_phase` so the hotbar/syncing card can show
        # "Verifying...", "Updating stats...", etc. instead of just "Finalizing..."
        # for the entire post-sync window.
        finalize_phase_key = f"finalize_phase:{profile_id}"

        def _set_phase(phase: str):
            redis_client.set(finalize_phase_key, phase, ex=1800)

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
            redis_client.delete(finalize_phase_key)
            return
        job_type = 'sync_complete'

        try:
            # Brief buffer to ensure the last job's DB writes have fully committed.
            # Jobs use transaction.atomic() so writes commit before job completion,
            # but 1s covers any edge cases with connection pooling or replication lag.
            time.sleep(1)

            # (job-worker bookend already logged START; narration line dropped)

            # Orphan-concept reconciliation: any game that slipped past the
            # slow-path's legacy-inline and sync_title_id fallbacks gets a
            # stub here. This catches modern games PSN's title_stats endpoint
            # omits (never-played or hidden), which sync_title_id never queues,
            # so the fingerprint-level "concept-less recovery" path otherwise
            # loops forever without progress. Pure DB work plus a deferred
            # IGDB enrich, no PSN calls, safe to run unconditionally. Runs
            # before the drain below so newly-deferred enrichments are picked
            # up in the same pass.
            _set_phase('igdb_enrich')
            orphan_games = list(
                Game.objects.filter(
                    played_by__profile=profile, concept__isnull=True,
                ).distinct()
            )
            reconciled = 0
            for game in orphan_games:
                try:
                    # Try canonical anchor first; if no clean IGDB match,
                    # fall through to the historical PP_* stub path.
                    if try_anchor_new_game(game) is None:
                        stub = Concept.create_default_concept(game)
                        game.add_concept(stub)
                        self._defer_igdb_enrich(profile_id, stub)
                    reconciled += 1
                except Exception:
                    logger.exception(
                        f"sync_complete orphan reconcile failed "
                        f"game={game.np_communication_id}"
                    )
            if reconciled:
                logger.warning(
                    f"[profile {profile_id}] health check reconciled "
                    f"{reconciled} concept-less game(s)"
                )

            # Drain any IGDB enrichments deferred by _job_sync_title_id or by
            # the orphan reconciliation above. By this point every Game for
            # every new concept has been attached, so the matching pipeline
            # sees the full game set — critical for distinguishing single-game
            # concepts from multi-game compilations.
            self._drain_deferred_igdb_enrich(profile_id)

            # Recompute total_hiddens from authoritative DB state. Visibility
            # reconciliation and trophy-count drift detection happen upstream
            # in _job_profile_refresh, so by the time we get here the DB is
            # the source of truth. The orphan-concept reconcile above and the
            # trophy/group completeness check below are the health-style work
            # remaining in sync_complete.
            _set_phase('health_check')
            logger.info(f"[profile {profile_id}] sync_complete hiddens recomputed")
            PsnApiService.recompute_total_hiddens(profile)
            redis_client.delete(f"health_mismatch_retries:{profile_id}")

            # Trophy/TrophyGroup completeness check: detect games where sync jobs
            # failed, leaving the Game with defined_trophies > 0 but zero Trophy
            # records or zero TrophyGroup records in the DB.  Separate cooldowns
            # per check type so one failure doesn't block the other from retrying.
            trophy_cooldown_key = f"trophy_completeness_check:{profile_id}"
            group_cooldown_key = f"group_completeness_check:{profile_id}"
            orphan_cooldown_key = f"orphan_group_check:{profile_id}"
            check_trophies = not redis_client.exists(trophy_cooldown_key)
            check_groups = not redis_client.exists(group_cooldown_key)
            check_orphans = not redis_client.exists(orphan_cooldown_key)

            if check_trophies or check_groups or check_orphans:
                from django.db.models import Count as _Count

                incomplete_trophy_games = []
                incomplete_group_games = []
                orphan_group_games = []

                if check_trophies:
                    # Games with 0 Trophy records (sync_trophies failed)
                    incomplete_trophy_games = list(
                        Game.objects.filter(
                            played_by__profile=profile,
                            defined_trophies__has_key='bronze',
                        ).annotate(
                            trophy_record_count=_Count('trophies'),
                        ).filter(
                            trophy_record_count=0,
                        )
                    )

                if check_groups:
                    # Games with 0 TrophyGroup records (sync_trophy_groups failed)
                    incomplete_group_games = list(
                        Game.objects.filter(
                            played_by__profile=profile,
                            defined_trophies__has_key='bronze',
                        ).annotate(
                            group_count=_Count('trophy_groups'),
                        ).filter(
                            group_count=0,
                        )
                    )

                if check_orphans:
                    # Games whose Trophy rows reference a trophy_group_id that has
                    # no matching TrophyGroup row (corrupted/missing DLC groups
                    # while the trophies themselves survive). The zero-group check
                    # above can't see these (group_count > 0); the slow-path drift
                    # check can't either (game-level defined_trophies total still
                    # matches PSN). DB-side Exists + distinct keeps this bounded
                    # for whales instead of materializing every Trophy row.
                    from django.db.models import OuterRef as _OuterRef, Exists as _Exists
                    from trophies.models import Trophy
                    _group_exists = TrophyGroup.objects.filter(
                        game_id=_OuterRef('game_id'),
                        trophy_group_id=_OuterRef('trophy_group_id'),
                    )
                    orphan_game_ids = list(
                        Trophy.objects.filter(game__played_by__profile=profile)
                        .annotate(_has_group=_Exists(_group_exists))
                        .filter(_has_group=False)
                        .values_list('game_id', flat=True)
                        .distinct()
                    )
                    if orphan_game_ids:
                        orphan_group_games = list(
                            Game.objects.filter(id__in=orphan_game_ids)
                        )

                if incomplete_trophy_games or incomplete_group_games or orphan_group_games:
                    if incomplete_trophy_games:
                        logger.warning(
                            f"Trophy record completeness: profile {profile_id} has "
                            f"{len(incomplete_trophy_games)} game(s) with 0 Trophy records."
                        )
                        redis_client.set(trophy_cooldown_key, "1", ex=21600)
                    if incomplete_group_games:
                        logger.warning(
                            f"TrophyGroup completeness: profile {profile_id} has "
                            f"{len(incomplete_group_games)} game(s) with 0 TrophyGroup records."
                        )
                        redis_client.set(group_cooldown_key, "1", ex=21600)
                    if orphan_group_games:
                        logger.warning(
                            f"TrophyGroup orphans: profile {profile_id} has "
                            f"{len(orphan_group_games)} game(s) whose trophies reference "
                            f"missing TrophyGroup records."
                        )
                        redis_client.set(orphan_cooldown_key, "1", ex=21600)

                    profile.reset_sync_progress()
                    profile.set_sync_status('syncing')
                    pending_key = f"pending_sync_complete:{profile_id}"
                    pending_data = json.dumps({
                        'touched_profilegame_ids': touched_profilegame_ids,
                        'queue_name': 'orchestrator'
                    })
                    redis_client.set(pending_key, pending_data, ex=21600)

                    queued_count = 0
                    # Re-queue sync_trophy_groups for games missing all groups OR
                    # with orphaned groups. Dedup by id so a game flagged by both
                    # checks is only queued once.
                    group_resync_games = {g.id: g for g in incomplete_group_games}
                    group_resync_games.update({g.id: g for g in orphan_group_games})

                    # Concept-sibling expansion: if one stacked version is missing
                    # a group, the other versions in the same concept likely gained
                    # the same DLC too (PSN adds it per-np_communication_id, and an
                    # unpopular sibling may have no active syncer to catch it). So
                    # proactively refresh every game sharing a detected game's
                    # concept. Bounded by the per-concept game count (a handful for
                    # regional/cross-gen stacks).
                    detected_concept_ids = {
                        g.concept_id for g in group_resync_games.values() if g.concept_id
                    }
                    if detected_concept_ids:
                        for sibling in Game.objects.filter(concept_id__in=detected_concept_ids):
                            group_resync_games.setdefault(sibling.id, sibling)

                    for game in group_resync_games.values():
                        # Guard malformed title_platform (empty / 'PSPC'-only):
                        # the sibling set can include legacy games we don't own.
                        platforms = game.title_platform or []
                        if not platforms:
                            continue
                        platform = platforms[0] if platforms[0] != 'PSPC' else (
                            platforms[1] if len(platforms) > 1 else None
                        )
                        if platform is None:
                            continue
                        args = [game.np_communication_id, platform]
                        PSNManager.assign_job('sync_trophy_groups', args, profile.id)
                        queued_count += 1  # group sync = 1 progress tick

                    # Re-queue sync_trophies for games missing trophy records
                    for game in incomplete_trophy_games:
                        platform = game.title_platform[0] if not game.title_platform[0] == 'PSPC' else game.title_platform[1]
                        if PSNManager.assign_sync_trophies(
                            profile.id, game.np_communication_id, platform,
                            priority_override='low_priority'
                        ):
                            queued_count += 2  # sync_trophies = 2 progress ticks

                    profile.add_to_sync_target(queued_count)
                    return

            _set_phase('stats_badges')
            profile.update_plats()
            PsnApiService.update_profilegame_stats(touched_profilegame_ids)
            check_profile_badges(profile, touched_profilegame_ids)

            # Create consolidated badge notifications
            try:
                from notifications.services.deferred_notification_service import DeferredNotificationService
                DeferredNotificationService.create_badge_notifications(profile_id, profile=profile)
            except Exception as e:
                logger.error(f"[profile {profile_id}] sync_complete badge notification failed: {e}", exc_info=True)
            _set_phase('milestones')
            from trophies.milestone_constants import ALL_CALENDAR_TYPES, ALL_GENRE_TYPES
            # Challenge-specific types are excluded here because they're checked
            # separately by their respective check_*_challenge_progress() functions below
            check_all_milestones_for_user(profile, exclude_types=ALL_CALENDAR_TYPES | {'az_progress'} | ALL_GENRE_TYPES)

            _set_phase('challenges')
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

            _set_phase('finishing')
            # Refresh denormalized stats from authoritative post-sync state.
            # Both updaters honor profile.hide_hiddens, so totals stay
            # consistent even when the user toggles that setting between syncs.
            update_profile_games(profile)
            update_profile_trophy_counts(profile)
            profile.set_sync_status('synced')

            from trophies.services.timeline_service import invalidate_timeline_cache
            invalidate_timeline_cache(profile_id)

            from trophies.services.stats_service import invalidate_stats_cache
            invalidate_stats_cache(profile_id)

            # Bulletproof dashboard invalidation: badge_service has its own hook
            # but it can early-return on no-op syncs. Invalidating here guarantees
            # every full sync refreshes all dashboard modules regardless of which
            # sub-services ran.
            from trophies.services.dashboard_service import invalidate_dashboard_cache
            invalidate_dashboard_cache(profile_id)

            # Re-render forum signature if enabled (SVG only: fast, no Playwright)
            try:
                from trophies.models import ProfileCardSettings
                if ProfileCardSettings.objects.filter(profile_id=profile_id, public_sig_enabled=True).exists():
                    from core.services.profile_card_renderer import render_sig_svg
                    render_sig_svg(profile)
                    logger.debug(f"[profile {profile_id}] forum sig re-rendered")
            except Exception:
                logger.exception(f"[profile {profile_id}] forum sig render failed")

            # (job-worker bookend logs DONE; trailing narration line dropped)
        except PSNOutageError:
            logger.warning(
                f"PSN outage during sync_complete for profile {profile_id}, deferring"
            )
            self._handle_outage_recovery(profile)
            return
        except Exception as e:
            # Deadlock/lock-timeout errors are transient (our fault, not the user's).
            # Instead of marking 'error', reset to 'synced' with a stale last_synced
            # so the cron picks the profile up for a normal profile_refresh.
            if isinstance(e, OperationalError):
                err_msg = str(e).lower()
                if "deadlock detected" in err_msg or "lock timeout" in err_msg:
                    logger.warning(
                        f"Deadlock/lock-timeout in sync_complete for profile {profile_id}, "
                        f"resetting for cron retry: {e}"
                    )
                    try:
                        profile.refresh_from_db()
                        profile.last_synced = profile.last_synced - timedelta(days=10)
                        profile.sync_status = 'synced'
                        profile.save(update_fields=['last_synced', 'sync_status'])
                    except Profile.DoesNotExist:
                        logger.warning(f"Profile {profile_id} no longer exists during deadlock recovery.")
                    return

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
            redis_client.delete(finalize_phase_key)
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
        logger.info(f"[profile {profile_id}] privacy error handled")

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
        except PSNOutageError:
            self._handle_outage_recovery(profile)
            return
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
        except PSNOutageError:
            self._handle_outage_recovery(profile)
            return
        except Exception as e:
            profile.set_sync_status('error')
            raise
        PsnApiService.update_profile_region(profile, region)

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

        # Sync concept-level trophy groups for the Review Hub
        if game.concept:
            from trophies.services.concept_trophy_group_service import ConceptTrophyGroupService
            ConceptTrophyGroupService.sync_for_concept(game.concept)
        else:
            logger.warning(f"Game {game.title_name} ({game.np_communication_id}) has no concept during trophy group sync.")

        profile.increment_sync_progress()
        logger.debug(f"[profile {profile.id}] sync_trophy_groups done game={game.np_communication_id}")

    def _job_sync_title_stats(self, profile_id: int, limit: int, offset: int, page_size: int, is_last: bool=False, force_all: bool=False):
        logger.debug(f"[profile {profile_id}] sync_title_stats force_all={force_all} offset={offset}")
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
                
                logger.debug(f"trophy_titles_for_title call ids={title_ids}")
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
                f"[profile {profile_id}] sync_trophies skip duplicate game={np_communication_id}"
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

        logger.debug(f"[profile {profile.id}] sync_trophies fetch game={np_communication_id} platform={platform}")
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
        # Diagnostic: compare what the trophies API returned vs what the
        # health check will see in the DB. Helps identify persistent mismatches
        # where trophy_titles reports a different count than the trophies endpoint.
        api_earned = sum(1 for t in trophies if t.earned)
        db_earned = EarnedTrophy.objects.filter(
            profile=profile, trophy__game=game, earned=True
        ).count()
        if api_earned != db_earned:
            logger.warning(
                f"Mismatch diagnostic for {np_communication_id} (profile {profile.id}): "
                f"API returned {api_earned} earned, DB has {db_earned} earned, "
                f"API total trophies: {len(trophies)}"
            )

        # Refresh Trophy.earn_rate for this game's trophies. Trophy.earned_count
        # and Game.played_count are maintained incrementally by the EarnedTrophy
        # and ProfileGame post_save signals, but earn_rate is a derived value
        # (earned_count / played_count) and was historically only refreshed by
        # the daily recalc_earn_rates cron. That left brand-new games stuck at
        # 0% earn rate until the next cron run. Doing one targeted UPDATE here
        # per game costs almost nothing and keeps PP-specific rarity live.
        # The daily cron remains the source of truth for cross-game drift.
        from trophies.models import Trophy
        played_count = (
            Game.objects.filter(pk=game.id)
            .values_list('played_count', flat=True)
            .first()
        ) or 0
        if played_count > 0:
            Trophy.objects.filter(game_id=game.id).update(
                earn_rate=F('earned_count') * 1.0 / played_count
            )

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

        logger.debug(f"[profile {profile_id}] sync_title_id begin title_id={title_id.title_id} game={np_communication_id}")
        try:

            # Resolve platform mismatch: trust the Game's platform over TitleID
            api_platform = title_id.platform
            if game.title_platform and title_id.platform not in game.title_platform:
                api_platform = game.title_platform[0]
                logger.warning(f"Platform mismatch for {title_id.title_id}: TitleID={title_id.platform}, Game={game.title_platform}. Using {api_platform}.")

            details, used_fallback_region = self._get_details_with_region_fallback(
                title_id=title_id.title_id,
                platform=api_platform,
                account_id=profile.account_id,
                np_communication_id=game.np_communication_id,
                profile=profile,
                job_type=job_type,
            )
            if self._details_is_populated(details):
                # Fix mismatch at source if API succeeded with corrected platform
                if api_platform != title_id.platform:
                    old_platform = title_id.platform
                    title_id.platform = api_platform
                    title_id.save(update_fields=['platform'])
                    logger.info(f"Corrected TitleID {title_id.title_id} platform: {old_platform} -> {api_platform}")

                # Try canonical IGDB anchor first when the Game has no Concept
                # yet. A clean match skips PSN-storefront Concept creation
                # entirely (we land at a canonical-anchored Concept whose
                # IGDBMatch is refreshed inline). Region detection + title_id
                # capture below still run against whichever Concept the Game
                # ends up on.
                #
                # When the Game already lives on an IGDB-anchored Concept
                # (anchor_migration_completed_at is set), the PSN-storefront
                # is no longer the source of truth — we MUST NOT re-create
                # the old PSN concept and reassign the game back to it on
                # every sync. That bug used to silently undo the anchoring
                # work because `game.add_concept(psn_concept)` happily
                # overwrites the FK whenever `concept_lock=False`.
                anchored = try_anchor_new_game(game) if game.concept is None else None

                if anchored is not None:
                    concept = anchored
                    # No PSN-derived enrichment to defer — process_match already
                    # ran inside try_anchor_new_game.
                    #
                    # ...but process_match only captured IGDB media. PSN GAMEHUB
                    # art is the preferred banner background and we already have
                    # the PSN `details` in hand, so apply PSN bg_url over the
                    # IGDB-artwork fallback when PSN supplies it. (Without this,
                    # IGDB-anchored concepts never get PSN landscape art — the
                    # profile-banner gap.)
                    try:
                        psn_bg = self._extract_media(details).get('bg_url')
                        if psn_bg and concept.bg_url != psn_bg:
                            concept.bg_url = psn_bg
                            concept.save(update_fields=['bg_url'])
                    except Exception:
                        logger.exception(
                            f"Failed to apply PSN bg_url to anchored concept "
                            f"{concept.concept_id}"
                        )
                elif (
                    game.concept_id
                    and game.concept.anchor_migration_completed_at is not None
                ):
                    # Existing game on an IGDB-anchored Concept: preserve it.
                    # Region detection + title_id capture below still run.
                    concept = game.concept
                else:
                    concept, concept_created = PsnApiService.create_concept_from_details(details)

                    # Gate: data from a non-default (Asian) storefront may only be used
                    # to initialize a NEW concept. It must never overwrite fields on an
                    # existing concept; a later US/en-US sync is allowed to refresh as
                    # usual via the concept_created=False, used_fallback_region=False path.
                    if concept_created or not used_fallback_region:
                        # English-path refresh: if we're seeing an existing concept via a
                        # US/en-US response, upgrade its English-facing fields so concepts
                        # originally seeded from an Asian fallback get their canonical
                        # English title once we finally see it.
                        if not concept_created and not used_fallback_region:
                            PsnApiService.update_concept_english_fields(concept, details)
                        release_date = details.get('defaultProduct', {}).get('releaseDate', None)
                        if release_date is None:
                            release_date = details.get('releaseDate', {}).get('date', '')
                        concept.update_release_date(release_date)
                        media_data = self._extract_media(details)
                        concept.update_media(media_data['all_media'], media_data['icon_url'], media_data['bg_url'])
                    else:
                        logger.info(
                            f"Concept {concept.concept_id} matched existing via Asian-region "
                            f"fallback; skipping release_date/media overwrite"
                        )
                    game.add_concept(concept)

                    # IGDB enrichment for newly created PSN concepts (best-effort).
                    # Deferred to sync_complete so every Game for the concept is
                    # attached before matching runs — _pick_search_title needs the
                    # full game set to distinguish single-game concepts from
                    # multi-game compilations.
                    if concept_created:
                        self._defer_igdb_enrich(profile_id, concept)

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
                logger.info(f"sync_title_id resolved {title_id.title_id} -> \"{concept.unified_title}\"")
            else:
                # PSN returned no usable metadata (empty, sparse, or errorCode). Fall
                # back to a default concept so the Game still has something to group.
                profile.increment_sync_progress()
                error_code = details.get('errorCode') if details else None
                if error_code is not None:
                    logger.warning(f"Concept for {title_id.title_id} returned errorCode: {error_code}")
                else:
                    logger.warning(f"Couldn't get usable details for Title ID {title_id.title_id}")
                if game.concept is None:
                    try:
                        region_code = detect_asian_language(game.title_name)
                        if region_code != 'Unknown':
                            game.add_region(region_code)
                            game.is_regional = True
                            game.save(update_fields=['is_regional'])
                            logger.info(f"Game {game.title_name} detected as Asian regional.")
                        # PSN returned no usable details; try canonical IGDB
                        # anchor before falling back to a PP_* stub. IGDB often
                        # covers games PSN's storefront has gaps for.
                        if try_anchor_new_game(game) is None:
                            default_concept = Concept.create_default_concept(game)
                            game.add_concept(default_concept)
                            self._defer_igdb_enrich(profile_id, default_concept)
                            logger.info(f"Created default concept for {game.title_name}")
                        else:
                            logger.info(f"Anchored {game.title_name} at canonical IGDB Concept")
                    except Exception:
                        logger.exception(f"Failed to place concept-less Game {game.title_name} (Title ID {title_id.title_id})")
        except PSNOutageError:
            raise  # Let the worker loop handle outage recovery
        except Exception as e:
            profile.increment_sync_progress()
            logger.exception(f"Error while syncing Title ID {title_id.title_id}: {e}")
            # Last-resort: ensure the game has a concept even if sync failed
            try:
                game.refresh_from_db()
                if game.concept is None:
                    default_concept = Concept.create_default_concept(game)
                    game.add_concept(default_concept)
                    self._defer_igdb_enrich(profile_id, default_concept)
                    logger.info(f"Exception recovery: created default concept for {game.title_name} (Title ID {title_id.title_id})")
            except Exception as recovery_err:
                logger.exception(f"Exception recovery also failed for {game.title_name} (Title ID {title_id.title_id}): {recovery_err}")

    def _try_igdb_enrich(self, concept):
        """Per-game IGDB anchoring for a newly created PSN concept.

        `sync_title_id` calls `try_anchor_new_game` upfront, but trophy data
        isn't loaded yet at that point (sync_trophies is a separate later job),
        so the per-game matcher has no `_extract_trophy_group_title` signal
        and returns None. Every new game falls through to a PSN-storefront
        concept. This method runs at sync_complete (drained from a Redis set,
        AFTER trophy_groups are populated) and is the actual anchoring step:
        per-game `match_game` (the canonical signal per the title-name-
        poisoning rule) -> `anchor_game_to_canonical` routes each Game to its
        per-version `str(raw_igdb_id)` slot, exactly like `anchor_concepts`
        does. The now-empty PSN concept absorbs into the survivor(s) via
        add_concept's cascade.

        Skips the older concept-level `enrich_concept` call. It used
        `match_concept` (PSN's `unified_title`, which the title-name-poisoning
        memory flags as unreliable), and `process_match` would have written an
        IGDBMatch onto the PSN concept right before per-game anchoring
        emptied and absorbed it — pure waste, plus a redundant IGDB call.
        The weekly `enrich_from_igdb --missing-or-no-match` cron still picks
        up concepts whose games never matched.

        When no game in the concept matches, record a no_match marker on the
        PSN concept so the weekly retry cron picks it up later.
        """
        from trophies.services.igdb_service import IGDBService
        from trophies.services.concept_anchor_service import anchor_game_to_canonical

        # Already-anchored concept: no per-game routing needed (the migration
        # / a prior auto-anchor pass already placed games at their per-version
        # slots). Don't disturb.
        if concept.anchor_migration_completed_at is not None:
            return

        # Per-game anchoring: re-route each Game in the PSN concept to its
        # per-version IGDB-anchored concept. anchor_game_to_canonical handles
        # identity cross-check + trophy fingerprint comparison and writes a
        # ConceptJoinReview on mismatch (game stays put), so divergent games
        # surface in the staff queue rather than getting silently mis-grouped.
        # The IGDB data is preloaded from match_game and threaded into
        # anchor_game_to_canonical so we never re-fetch the same id.
        matched_any = False
        for game in list(concept.games.all()):
            try:
                game_match = IGDBService.match_game(game)
            except Exception:
                logger.exception(
                    f"sync_complete auto-anchor: match_game failed for game pk={game.pk}"
                )
                continue
            if not game_match:
                continue
            matched_any = True
            try:
                anchor_game_to_canonical(
                    game,
                    game_match['raw_igdb_id'],
                    user=None,
                    raw_data=game_match['igdb_data'],
                )
            except Exception:
                logger.exception(
                    f"sync_complete auto-anchor: anchor_game_to_canonical failed "
                    f"for game pk={game.pk}"
                )

        if not matched_any:
            # Per-game matching uses each Game's trophy_group_title — the
            # canonical signal but it can miss true compilations where IGDB
            # has a single entry covering several PSN trophy lists (e.g.
            # "Resident Evil Origins Collection"). Before marking no_match,
            # try the concept-level `enrich_concept` (match_concept against
            # `unified_title`) as a safety net so compilations still get an
            # IGDBMatch without waiting for the weekly retry cron.
            try:
                fallback = IGDBService.enrich_concept(concept)
            except Exception:
                logger.exception(
                    f"sync_complete auto-anchor: enrich_concept fallback "
                    f"failed for concept {concept.concept_id}"
                )
                fallback = None
            if fallback is None:
                try:
                    IGDBService.record_no_match(concept)
                except Exception:
                    logger.exception(
                        f"Failed to record no_match marker for concept {concept.concept_id}"
                    )

    @staticmethod
    def _pending_igdb_enrich_key(profile_id) -> str:
        return f"profile:{profile_id}:pending_igdb_enrich"

    def _defer_igdb_enrich(self, profile_id, concept):
        """Queue a newly created concept for IGDB enrichment at sync_complete.

        Adds concept.id to a per-profile Redis set. Drained by
        `_drain_deferred_igdb_enrich` so that matching runs after every
        sibling Game has been attached across title_id jobs. Falls back to
        inline enrichment if Redis is unavailable (keeps the old behaviour
        as a safety net).
        """
        try:
            key = self._pending_igdb_enrich_key(profile_id)
            redis_client.sadd(key, concept.id)
            redis_client.expire(key, 21600)  # 6h cap in case sync_complete never fires
        except Exception:
            logger.exception(
                f"Failed to defer IGDB enrichment for concept {concept.concept_id}; "
                f"falling back to inline enrichment"
            )
            self._try_igdb_enrich(concept)

    def _drain_deferred_igdb_enrich(self, profile_id):
        """Run IGDB enrichment for every concept queued during this profile's sync.

        Called at the top of _job_sync_complete, before health-check logic
        that may itself create default concepts and inline-enrich them.
        """
        key = self._pending_igdb_enrich_key(profile_id)
        try:
            raw_ids = redis_client.smembers(key)
        except Exception:
            logger.exception(f"Failed to read deferred IGDB enrichment set for profile {profile_id}")
            return

        if not raw_ids:
            return

        concept_ids = []
        for item in raw_ids:
            if isinstance(item, bytes):
                item = item.decode('utf-8', errors='ignore')
            try:
                concept_ids.append(int(item))
            except (TypeError, ValueError):
                continue

        if not concept_ids:
            redis_client.delete(key)
            return

        concepts = list(Concept.objects.filter(id__in=concept_ids))
        logger.info(
            f"[profile {profile_id}] IGDB drain concepts={len(concepts)}"
        )
        for concept in concepts:
            self._try_igdb_enrich(concept)

        redis_client.delete(key)

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
    
    # ─── Profile Refresh: Unified Fingerprint-Based Orchestrator ────────────
    # See docs/architecture/sync-architecture.md for the architecture
    # rationale. This is the single sync orchestrator entry point: both fresh
    # accounts and follow-up syncs flow through here. Whether the DB starts
    # empty or partially populated is no longer a code-branch decision; it
    # just shows up as a larger fingerprint mismatch.

    def _job_profile_refresh(self, profile_id: int):
        """Unified sync orchestrator.

        Computes a fingerprint from PSN (`trophy_summary` totals + visible
        game count from `trophy_titles` page 1 metadata) and compares to the
        DB. If matched, fast-path: schedule sync_complete with no per-game
        work. If mismatched, slow-path: walk all visible titles, reconcile
        visibility by set diff, queue per-game work.
        """
        redis_client.delete(f"sync_orchestrator_pending:{profile_id}")
        try:
            profile = Profile.objects.get(id=profile_id)
        except Profile.DoesNotExist:
            logger.error(f"Profile {profile_id} does not exist.")
            return

        # Capture state BEFORE we queue sync_profile_data and start the walk:
        # - last_sync: sync_profile_data will bump last_synced when it runs;
        #   the title_stats walk below uses the previous value to early-exit
        #   on entries older than the previous sync.
        # - had_existing_games: distinguishes initial sync (no DB games yet)
        #   from follow-up sync. The title_stats walk uses full-walk semantics
        #   on initial sync so newly-created ProfileGames get their first
        #   playtime data, and early-exit semantics on follow-ups.
        last_sync = profile.last_synced
        had_existing_games = ProfileGame.objects.filter(profile=profile).exists()

        # Queue sync_profile_data so PSN profile fields (level, avatar, region)
        # refresh AND last_synced gets bumped via update_profile_from_legacy.
        # This is the canonical place for that queue: PSNManager entry points
        # (initial_sync / profile_refresh) only queue this orchestrator.
        PSNManager.assign_job('sync_profile_data', args=[], profile_id=profile_id)

        job_type = 'profile_refresh'

        # 1. PSN signals: trophy_summary + first page of trophy_titles
        summary = self._execute_api_call(
            self._get_instance_for_job(job_type), profile, 'trophy_summary'
        )
        earned = summary.earned_trophies
        page_size = 400
        first_page, total_item_count = self._execute_api_call(
            self._get_instance_for_job(job_type), profile, 'trophy_titles_with_count',
            limit=page_size, offset=0, page_size=page_size,
        )

        psn_fingerprint = (
            earned.bronze, earned.silver, earned.gold, earned.platinum,
            total_item_count,
        )
        db_fingerprint = PsnApiService.get_db_fingerprint(profile)
        fingerprints_match = (psn_fingerprint == db_fingerprint)

        # Safety net: even if the fingerprint matches, force slow path when
        # any of the profile's games is missing a concept. The fingerprint
        # only watches trophy counts and visible-game count; it can't see
        # concept resolution gaps caused by matching pipeline failures, IGDB
        # outages, or manual concept cleanup. Forcing the walk lets the
        # classifier re-route concept-less modern games into sync_title_stats
        # and assign default concepts to legacy-only games.
        force_slow = False
        if fingerprints_match:
            if ProfileGame.objects.filter(
                profile=profile, game__concept__isnull=True,
            ).exists():
                force_slow = True

        path_label = 'slow' if (not fingerprints_match or force_slow) else 'fast'
        log_suffix = ' (concept-less recovery)' if (fingerprints_match and force_slow) else ''
        logger.info(
            f"[profile {profile_id}] fingerprint "
            f"psn={psn_fingerprint} db={db_fingerprint} "
            f"path={path_label}{log_suffix}"
        )

        if fingerprints_match and not force_slow:
            self._profile_refresh_fast_path(profile_id)
            return

        self._profile_refresh_slow_path(
            profile_id, profile, first_page, total_item_count, page_size,
            last_sync, had_existing_games,
        )

    def _profile_refresh_fast_path(self, profile_id: int):
        """Fingerprint matched: schedule sync_complete with no per-game work."""
        pending_key = f"pending_sync_complete:{profile_id}"
        pending_data = json.dumps({
            'touched_profilegame_ids': [],
            'queue_name': 'orchestrator',
        })
        redis_client.set(pending_key, pending_data, ex=7200)

        # No counted jobs were queued, so sync_complete won't auto-fire via
        # _complete_job. Trigger it immediately.
        current_jobs = self._get_current_jobs_for_profile(profile_id)
        if current_jobs <= 0:
            logger.info(f"[profile {profile_id}] fast path: triggering sync_complete")
            args = [[], 'orchestrator']
            PSNManager.assign_job('sync_complete', args, profile_id, priority_override='orchestrator')
            redis_client.delete(pending_key)

    def _profile_refresh_slow_path(self, profile_id, profile, first_page, total_item_count, page_size, last_sync, had_existing_games):
        """Fingerprint mismatched: walk PSN trophy_titles fully, reconcile state."""
        logger.info(
            f"[profile {profile_id}] slow path: walking {total_item_count} titles"
        )
        job_type = 'profile_refresh'

        # Fetch remaining pages (we already have page 1). For accounts with
        # multiple remaining pages we fan them out in parallel: page-1 metadata
        # told us total_item_count, so we know exactly how many pages and what
        # offsets to fetch. Cap concurrency low — the token instance pool is
        # shared with other workers, and we don't want to starve them during
        # the brief pagination phase.
        trophy_titles = list(first_page)
        remaining_offsets = (
            list(range(page_size, total_item_count, page_size))
            if total_item_count > len(first_page)
            else []
        )
        if len(remaining_offsets) == 1:
            # Only one extra page; thread-pool overhead isn't worth it.
            offset = remaining_offsets[0]
            result = self._execute_api_call(
                self._get_instance_for_job(job_type), profile, 'trophy_titles',
                limit=offset + page_size, offset=offset, page_size=page_size,
            )
            trophy_titles.extend(result)
        elif len(remaining_offsets) > 1:
            from concurrent.futures import ThreadPoolExecutor
            max_workers = min(3, len(remaining_offsets))
            logger.info(
                f"[profile {profile_id}] paginating {len(remaining_offsets)} pages "
                f"in parallel (workers={max_workers})"
            )

            def _fetch_page(offset):
                return offset, self._execute_api_call(
                    self._get_instance_for_job(job_type), profile, 'trophy_titles',
                    limit=offset + page_size, offset=offset, page_size=page_size,
                )

            results_by_offset = {}
            with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix='trophy-titles-page') as executor:
                # `executor.map` raises the first exception encountered; that
                # propagates up through _job_profile_refresh and into the worker
                # loop's existing error handling (PSNOutageError, deadlock, etc).
                for offset, page in executor.map(_fetch_page, remaining_offsets):
                    results_by_offset[offset] = page

            # Reassemble in offset order so PSN's natural newest-first ordering
            # is preserved across the parallel fetch.
            for offset in remaining_offsets:
                trophy_titles.extend(results_by_offset[offset])

        # Pre-fetch DB earned-counts per game so we can detect trophy drift in
        # one pass without per-game queries.
        from django.db.models import Count as _Count
        db_earned_by_game = dict(
            EarnedTrophy.objects.filter(profile=profile, earned=True)
            .values('trophy__game_id')
            .annotate(total=_Count('id'))
            .values_list('trophy__game_id', 'total')
        )

        # FIRST PASS: per-title processing, build PSN visible set, classify work
        touched_profilegame_ids = []
        psn_visible_np_ids = set()
        games_needing_groups = []
        games_needing_concepts = []
        games_to_resync = []
        new_game_count = 0
        job_counter = 0

        for title in trophy_titles:
            psn_visible_np_ids.add(title.np_communication_id)
            game, game_created, _ = PsnApiService.create_or_update_game(title)
            if game_created:
                new_game_count += 1

            profile_game, pgame_created = PsnApiService.create_or_update_profile_game(profile, game, title)

            # Concept-less game classification:
            # - Modern platform games (PS4/PS5) go through sync_title_stats →
            #   sync_title_id for proper PSN concept resolution. We do NOT
            #   short-circuit them with default concepts even if title_ids is
            #   empty here, because title_ids gets populated downstream by the
            #   sync_title_stats pipeline.
            # - Legacy-only platform games (PS3, PSVITA, PSVR, PSVR2) never
            #   enter that pipeline, so they need a default concept assigned
            #   inline to avoid remaining concept-less indefinitely.
            # The health check uses an additional has_title_ids carve-out that
            # belongs only there, NOT here: it prevents infinite retry of
            # games PSN can't resolve. At walk time, every fresh modern game
            # has empty title_ids and that check would mis-stub them all.
            if game.concept is None:
                has_modern_platform = any(
                    p in TITLE_STATS_SUPPORTED_PLATFORMS for p in game.title_platform
                )
                if has_modern_platform:
                    games_needing_concepts.append(game)
                else:
                    try:
                        # Try canonical anchor first; legacy/PS3/Vita games
                        # often have an IGDB match even when they have no PSN
                        # storefront concept. If the match is clean, land
                        # the Game at the canonical Concept directly and
                        # skip the PP_* stub.
                        if try_anchor_new_game(game) is None:
                            default_concept = Concept.create_default_concept(game)
                            game.add_concept(default_concept)
                            self._try_igdb_enrich(default_concept)
                        logger.debug(
                            f"placed legacy game={game.np_communication_id}"
                        )
                    except Exception:
                        logger.exception(
                            f"legacy placement failed game={game.np_communication_id}"
                        )

            # Trophy-count drift detection: compare PSN's earned total for this
            # title against our DB's. Mismatch = needs sync_trophies re-run.
            title_earned_total = (
                title.earned_trophies.bronze + title.earned_trophies.silver
                + title.earned_trophies.gold + title.earned_trophies.platinum
            )
            db_earned_total = db_earned_by_game.get(game.id, 0)
            has_drift = (title_earned_total != db_earned_total)

            # TrophyGroup completeness: new games or games whose defined_trophies
            # total disagrees with DB, or which simply have no TrophyGroup rows yet.
            title_defined_total = (
                title.defined_trophies.bronze + title.defined_trophies.silver
                + title.defined_trophies.gold + title.defined_trophies.platinum
            )
            needs_groups = (
                game_created
                or game.get_total_defined_trophies() != title_defined_total
                or not TrophyGroup.objects.filter(game=game).exists()
            )
            if needs_groups:
                games_needing_groups.append(game)
                job_counter += 1

            if has_drift or pgame_created:
                games_to_resync.append(game)
                job_counter += 2  # sync_trophies = +2 (one without progress, one with)
                # Only games whose EarnedTrophy aggregates need recomputation are
                # added to touched_profilegame_ids. update_profilegame_stats and
                # check_profile_badges scope their work to this list, so adding
                # untouched games is wasted DB / badge-eval cycles.
                touched_profilegame_ids.append(profile_game.id)

        # Scout discovery counter (no-op for non-scouts).
        if new_game_count > 0:
            ScoutAccount.objects.filter(
                profile_id=profile_id, status='active',
            ).update(games_discovered=F('games_discovered') + new_game_count)

        # VISIBILITY RECONCILIATION: set diff between PSN-visible and DB-visible,
        # then bulk-flip user_hidden on ProfileGame and EarnedTrophy.
        db_visible_np_ids = set(
            ProfileGame.objects.filter(profile=profile, user_hidden=False)
            .values_list('game__np_communication_id', flat=True)
        )
        newly_hidden_np_ids = db_visible_np_ids - psn_visible_np_ids
        newly_unhidden_np_ids = psn_visible_np_ids - db_visible_np_ids

        if newly_hidden_np_ids:
            game_ids = list(
                Game.objects.filter(np_communication_id__in=newly_hidden_np_ids)
                .values_list('id', flat=True)
            )
            if game_ids:
                with transaction.atomic():
                    ProfileGame.objects.filter(
                        profile=profile, game_id__in=game_ids,
                    ).update(user_hidden=True)
                    EarnedTrophy.objects.filter(
                        profile=profile, trophy__game_id__in=game_ids,
                    ).update(user_hidden=True)

        if newly_unhidden_np_ids:
            game_ids = list(
                Game.objects.filter(np_communication_id__in=newly_unhidden_np_ids)
                .values_list('id', flat=True)
            )
            if game_ids:
                with transaction.atomic():
                    ProfileGame.objects.filter(
                        profile=profile, game_id__in=game_ids,
                    ).update(user_hidden=False)
                    EarnedTrophy.objects.filter(
                        profile=profile, trophy__game_id__in=game_ids,
                    ).update(user_hidden=False)

        if newly_hidden_np_ids or newly_unhidden_np_ids:
            logger.info(
                f"[profile {profile_id}] visibility "
                f"hid={len(newly_hidden_np_ids)} unhid={len(newly_unhidden_np_ids)}"
            )

        # title_stats walk: populate playtime / play count / first / last_played
        # on each ProfileGame and queue sync_title_id for any title_ids the
        # endpoint returns that don't map to a known concept yet.
        # - Initial sync (had_existing_games=False): walk every page so newly
        #   created ProfileGames get their first playtime data.
        # - Follow-up sync: early-exit on entries whose last_played <= last_sync
        #   (PSN sorts title_stats by last_played DESC, so we hit stale entries
        #   in order and can stop pagination).
        title_id_jobs_queued = self._walk_title_stats(
            profile, profile_id, last_sync, full_walk=not had_existing_games,
        )
        job_counter += title_id_jobs_queued

        # Set sync_progress_target BEFORE queueing jobs to avoid the race where
        # _complete_job sees zero pending and fires sync_complete prematurely.
        profile.add_to_sync_target(job_counter)

        # Whale routing: if total per-game work exceeds the threshold, send
        # sync_trophies to the bulk_priority queue.
        bulk_threshold = int(redis_client.get('sync:bulk_threshold') or 5000)
        trophy_queue = 'bulk_priority' if job_counter > bulk_threshold else None
        if trophy_queue == 'bulk_priority':
            logger.info(
                f"[profile {profile_id}] whale routing "
                f"jobs={job_counter} threshold={bulk_threshold} -> bulk_priority"
            )

        # SECOND PASS: queue per-game jobs.
        for game in games_needing_groups:
            platform = game.title_platform[0] if game.title_platform[0] != 'PSPC' else game.title_platform[1]
            args = [game.np_communication_id, platform]
            PSNManager.assign_job('sync_trophy_groups', args, profile.id)

        skipped = 0
        for game in games_to_resync:
            platform = game.title_platform[0] if game.title_platform[0] != 'PSPC' else game.title_platform[1]
            queued = PSNManager.assign_sync_trophies(
                profile.id, game.np_communication_id, platform,
                priority_override=trophy_queue,
            )
            if not queued:
                skipped += 1
        if skipped > 0:
            logger.info(f"[profile {profile_id}] skipped duplicate sync_trophies count={skipped}")
            profile.add_to_sync_target(-(skipped * 2))

        # One-line summary of the slow-path walk's per-game classification.
        # `concepts=` reports inline-detected concept-less modern games (those
        # got a sync_title_id job queued by _walk_title_stats above; the count
        # here is informational and may differ slightly from title_id_jobs_queued
        # if the title_stats walk discovered additional unmatched title_ids).
        logger.info(
            f"[profile {profile_id}] walk done "
            f"titles={len(trophy_titles)} drift={len(games_to_resync)} "
            f"groups={len(games_needing_groups)} concepts={len(games_needing_concepts)} "
            f"title_id_jobs={title_id_jobs_queued}"
        )

        # Set pending_sync_complete so the orchestrator fires sync_complete
        # once all per-game jobs finish.
        pending_key = f"pending_sync_complete:{profile_id}"
        pending_data = json.dumps({
            'touched_profilegame_ids': touched_profilegame_ids,
            'queue_name': 'orchestrator',
        })
        redis_client.set(pending_key, pending_data, ex=7200)

        # If no per-game work was queued (e.g. drift-free sync that still walked
        # to reconcile visibility), fire sync_complete immediately.
        current_jobs = self._get_current_jobs_for_profile(profile_id)
        if current_jobs <= 0:
            logger.info(f"[profile {profile_id}] no pending jobs: triggering sync_complete")
            args = [touched_profilegame_ids, 'orchestrator']
            PSNManager.assign_job('sync_complete', args, profile_id, priority_override='orchestrator')
            redis_client.delete(pending_key)

    def _walk_title_stats(self, profile, profile_id, last_sync, full_walk):
        """Walk PSN title_stats inline, updating ProfileGame playtime data.

        Two responsibilities, both inherited from the legacy `_job_profile_refresh`:

        1. Call `update_profile_game_with_title_stats` for each entry so each
           ProfileGame's play_count / play_duration / first_played / last_played
           fields reflect the latest PSN data.
        2. Detect title_ids that don't map to a known concept yet, fetch their
           np_communication_id mappings via `trophy_titles_for_title`, and queue
           a `sync_title_id` job for each so the concept-resolution pipeline runs.

        Pagination:
        - `full_walk=True` (initial sync): walk every page. Newly-created
          ProfileGames have no playtime data yet, so the early-exit check
          would only bail uselessly.
        - `full_walk=False` (follow-up sync): early-exit when the page
          contains an entry with `last_played_date_time <= last_sync`. PSN
          sorts title_stats by last_played DESC, so once we hit an old entry
          everything beyond it is also old.

        Returns the number of `sync_title_id` jobs queued, so the caller can
        add them to `sync_progress_target` before any per-game jobs queue.
        """
        job_type = 'profile_refresh'
        page_size = 20
        offset = 0
        title_stats_to_be_updated = []
        end_found = False
        is_full = True

        while not end_found and is_full:
            page = self._execute_api_call(
                self._get_instance_for_job(job_type), profile, 'title_stats',
                limit=offset + page_size, offset=offset, page_size=page_size,
            )
            is_full = len(page) == page_size
            for stats in page:
                if not full_walk and stats.last_played_date_time <= last_sync:
                    end_found = True
                    break
                title_stats_to_be_updated.append(stats)
            offset += page_size

        # Apply the playtime data and collect title_ids that didn't match a
        # known game/concept for the resolution pipeline.
        remaining_title_stats = []
        for stats in title_stats_to_be_updated:
            found = PsnApiService.update_profile_game_with_title_stats(profile, stats)
            if not found and stats.title_id not in TITLE_ID_BLACKLIST:
                remaining_title_stats.append(stats)

        if not remaining_title_stats:
            logger.info(
                f"[profile {profile_id}] title_stats walk done "
                f"entries={len(title_stats_to_be_updated)} unmatched=0 full_walk={full_walk}"
            )
            return 0

        # Map unmatched title_ids to np_communication_id via PSN
        # trophy_titles_for_title (chunked at 5 ids per call), then for each
        # resolved id: add it to the Game's `title_ids` field, retry the
        # playtime update with the original title_stats entry (it now matches
        # because `title_ids` is populated), and queue sync_title_id so the
        # concept resolver runs downstream.
        stats_by_title_id = {stats.title_id: stats for stats in remaining_title_stats}
        chunk_size = 5
        trophy_titles_for_title = []
        for chunk_start in range(0, len(remaining_title_stats), chunk_size):
            chunk = remaining_title_stats[chunk_start:chunk_start + chunk_size]
            title_ids = [t.title_id for t in chunk]
            logger.debug(f"trophy_titles_for_title call ids={title_ids}")
            result = self._execute_api_call(
                self._get_instance_for_job(job_type), profile,
                'trophy_titles_for_title', title_ids=title_ids,
            )
            trophy_titles_for_title.extend(result)

        jobs_queued = 0
        playtime_repopulated = 0
        for title in trophy_titles_for_title:
            try:
                game = Game.objects.get(np_communication_id=title.np_communication_id)
            except Game.DoesNotExist:
                logger.warning(f"Game with comm id {title.np_communication_id} does not exist.")
                continue
            # Order matters: add_title_id MUST run before the retry update,
            # since update_profile_game_with_title_stats matches via
            # `Game.title_ids__contains`. Without the prior add_title_id,
            # the retry would still find no Games — that's the bug we hit
            # on initial sync, where Games created from trophy_titles have
            # no title_ids until trophy_titles_for_title resolves them here.
            game.add_title_id(title.np_title_id)
            original_stats = stats_by_title_id.get(title.np_title_id)
            if original_stats is not None:
                if PsnApiService.update_profile_game_with_title_stats(profile, original_stats):
                    playtime_repopulated += 1
            args = [title.np_title_id, title.np_communication_id]
            PSNManager.assign_job('sync_title_id', args=args, profile_id=profile.id)
            jobs_queued += 1

        logger.info(
            f"[profile {profile_id}] title_stats walk done "
            f"entries={len(title_stats_to_be_updated)} "
            f"unmatched={len(remaining_title_stats)} "
            f"playtime_repopulated={playtime_repopulated} "
            f"title_id_jobs={jobs_queued} full_walk={full_walk}"
        )
        return jobs_queued

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