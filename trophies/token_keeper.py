import json
from random import choice
import time
import threading
import logging
import os
import atexit
from pyrate_limiter import Duration, Rate
import requests
from typing import Optional, Dict
from dataclasses import dataclass
from datetime import datetime, timedelta
from django.utils import timezone
from psnawp_api import PSNAWP as BasePSNAWP
from psnawp_api.core.request_builder import RequestBuilder as BaseRequestBuilder
from psnawp_api.core.authenticator import Authenticator as BaseAuthenticator
from psnawp_api.core.psnawp_exceptions import PSNAWPForbiddenError
from psnawp_api.models.trophies.trophy_constants import PlatformType
from requests import HTTPError
from requests.exceptions import ConnectionError, Timeout
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from .models import Profile, Game, TitleID, TrophyGroup
from .services.psn_api_service import PsnApiService
from .psn_manager import PSNManager
from .utils import redis_client, log_api_call, TITLE_ID_BLACKLIST, TITLE_STATS_SUPPORTED_PLATFORMS, check_profile_badges, update_profile_games, update_profile_trophy_counts

logger = logging.getLogger("psn_api")

class ProxiedRequestBuilder(BaseRequestBuilder):
    def __init__(self, common_headers, rate_limit, proxy_url=None):
        super().__init__(common_headers, rate_limit)
        if proxy_url:
            self.session.proxies = {'http': proxy_url, 'https': proxy_url}

class ProxiedAuthenticator(BaseAuthenticator):
    def __init__(self, npsso_cookie, common_headers, rate_limit, proxy_url=None):
        super().__init__(npsso_cookie, common_headers, rate_limit)
        self.request_builder = ProxiedRequestBuilder(common_headers, rate_limit, proxy_url=proxy_url)

class ProxiedPSNAWP(BasePSNAWP):
    def __init__(self, npsso_cookie, headers=None, rate_limit=None, proxy_url=None):
        random_ua = [
            "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/55.0.6598.1817 Mobile Safari/537.36",
            "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Mobile Safari/537.36",
            "Mozilla/5.0 (Linux; Android 5.0; SM-G900P Build/LRX21T) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/39.0.5707.1741 Mobile Safari/537.36",
            "Mozilla/5.0 (Android 14; Mobile; rv:137.0) Gecko/137.0 Firefox/137.0",
            "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/55.0.9318.1385 Mobile Safari/537.36"
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

    def __post_init__(self):
        if self.user_cache is None:
            self.user_cache = {}
        if self.access_expiry is None or self.refresh_expiry is None:
            self.update_expiry_times()
    
    def update_expiry_times(self):
        auth = self.client.authenticator
        self.access_expiry = datetime.fromtimestamp(auth.access_token_expiration_time)
        self.refresh_expiry = datetime.fromtimestamp(auth.refresh_token_expiration_time)

    def get_access_expiry_in_seconds(self):
        if self.access_expiry:
            return (self.access_expiry - datetime.now()).total_seconds()
        return -1
    
    def get_refresh_expiry_in_seconds(self):
        if self.refresh_expiry:
            return (self.refresh_expiry - datetime.now()).total_seconds()
        return -1
        
    def cleanup_cache(self, ttl_hours=24):
        """Remove cache entries older than ttl_hours."""
        now = datetime.now()
        expired = [
            key for key, entry in self.user_cache.items()
            if now - entry['timestamp'] > timedelta(hours=ttl_hours)
        ]
        for key in expired:
            del self.user_cache[key]
        logger.debug(f"Cleaned {len(expired)} expired users from instance {self.instance_id}")

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
        running_key = f"token_keeper:running:{self.machine_id}"
        if redis_client.get(running_key):
            logger.info(f"TokenKeeper already running for machine {self.machine_id}")
            return None
        self.initialize_groups()
        self._start_health_monitor()
        self._start_stats_publisher()
        self._start_job_workers()
        redis_client.set(running_key, "1", ex=3600)
        atexit.register(self._cleanup)
    
    def _publish_stats_loop(self):
        while True:
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

    
    def _start_health_monitor(self):
        """Start background thread for proactive health checks."""
        self._health_thread = threading.Thread(target=self._health_loop, daemon=True)
        self._health_thread.start()
        logger.info("TokenKeeper health monitor started")

    def _start_stats_publisher(self):
        self._stats_thread = threading.Thread(target=self._publish_stats_loop, daemon=True)
        self._stats_thread.start()
        logger.info("TokenKeeper stats publisher started")

    def _start_job_workers(self):
        num_workers_per_group = 3
        total_workers = num_workers_per_group * len(self.token_groups)
        for _ in range(total_workers):
            t = threading.Thread(target=self._job_worker_loop, daemon=True)
            t.start()
            self._job_workers.append(t)
        logger.info(f"Started {total_workers} job worker threads")
    
    def _health_loop(self):
        """Infinite loop: Check health every interval."""
        while True:
            time.sleep(self.health_interval)
            redis_client.set(f"token_keeper:running:{self.machine_id}", "1", ex=3600)
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
                client = ProxiedPSNAWP(token, proxy_url=proxy)
                self._record_call(token)
                client.user(online_id='PlatPursuit') # Generates refresh tokens, etc.
                inst = TokenInstance(
                    instance_id=i,
                    token=token,
                    client=client,
                    user_cache={},
                    proxy_url=proxy
                )
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
                self._record_call(token)
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
        group_id = next((gid for gid, g in self.group_instances.items() if inst in g['instances'].values()), None)
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
                log_api_call("keeper_refresh", inst.token, None, 200, time.time() - start)
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
        except Exception as e:
            logger.error(f"Health check failed for {inst.instance_id}: {e}")
            inst.last_error = f"{datetime.now().isoformat()} Refresh error: {str(e)}"
            inst.last_health = 0
        else:
            inst.last_health = time.time()

    # Job Assignment & Handling

    def _job_worker_loop(self):
        while True:
            profile_id = None
            queue_name = None
            try:
                queue_b, job_json = redis_client.brpop(['high_priority_jobs', 'medium_priority_jobs', 'low_priority_jobs'])
                queue_name = queue_b.decode()[:-5] # remove '_jobs'
                job_data = json.loads(job_json)
                job_type = job_data['job_type']
                args = job_data['args']
                profile_id = job_data['profile_id']
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
                elif job_type == 'sync_profilegame_stats':
                    self._sync_profilegame_stats(profile_id, args[0])
                elif job_type == 'check_profile_health':
                    self._job_check_profile_health(profile_id)
                elif job_type == 'sync_games_only':
                    self._job_sync_games_only(profile_id)
                elif job_type == 'sync_title_id':
                    self._job_sync_title_id(profile_id, args[0], args[1])
                elif job_type == 'sync_complete':
                    self._job_sync_complete(profile_id)
                elif job_type == 'handle_privacy_error':
                    self._job_handle_privacy_error(profile_id)
                elif job_type == 'check_profile_badges':
                    self._job_check_profile_badges(profile_id)
                else:
                    logger.error(f"Unknown job type: {job_type}")
                    raise
                
                logger.info(f"Job: {job_type} - Profile: {profile_id} completed successfully!")
                self._complete_job(profile_id, queue_name)
            except Exception as e:
                logger.error(f"Error in job worker: {e}")
            finally:
                if profile_id and queue_name != 'high_priority':
                    self._complete_job(profile_id, queue_name)

    def _complete_job(self, profile_id, queue_name):
        """Handle finished job, check for deferred."""
        if queue_name == 'low_priority':
            redis_client.decr(f"profile_jobs:{profile_id}:{queue_name}")
            current_jobs = int(redis_client.get(f"profile_jobs:{profile_id}:{queue_name}") or 0)
            if current_jobs <= 0:
                redis_client.delete(f"profile_jobs:{profile_id}:{queue_name}")
                redis_client.srem("active_profiles", profile_id)
            job_json = redis_client.lpop(f"deferred_jobs:{profile_id}")
            if job_json:
                job_data = json.loads(job_json)
                PSNManager.assign_job(job_data['type'], job_data['args'], profile_id, job_data.get('priority_override'))

    def _get_instance_for_job(self, job_type: str) -> Optional[TokenInstance]:
        """Selects best instance for job, respecting workload and priority."""
        start = time.time()
        while time.time() - start < self.token_wait_interval:
            instance_scores = {}
            for group_id, group in self.group_instances.items():
                for inst_id, inst in group['instances'].items():
                    if not inst.is_busy and self._is_healthy(inst):
                        key = (group_id, inst_id)
                        instance_scores[key] = self._get_calls_in_window(inst.token)
            
            if instance_scores:
                best_key = min(instance_scores, key=instance_scores.get)
                group_id, inst_id = best_key
                inst = self.group_instances[group_id]['instances'][inst_id]
                inst.is_busy = True
                return inst
            logger.info("Waiting for token...")
            time.sleep(0.1)
        logger.error(f"No token available for use.")
        return None
    
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
            if redis_client.get(f"token_keeper:pending_refresh:{self.machine_id}:{instance.instance_id}"):
                self._check_and_refresh(instance)
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
            if redis_client.get(f"token_keeper:pending_refresh:{self.machine_id}:{instance.instance_id}"):
                self._check_and_refresh(instance)
            raise
        except HTTPError as e:
            log_api_call(endpoint, instance.token, profile.id if profile else None, e.response.status_code, time.time() - start_time, str(e))
            self._rollback_call(instance.token)
            if redis_client.get(f"token_keeper:pending_refresh:{self.machine_id}:{instance.instance_id}"):
                self._check_and_refresh(instance)
            raise
        except Exception as e:
            log_api_call(endpoint, instance.token, profile.id if profile else None, 500, time.time() - start_time, str(e))
            instance.last_error = f"{datetime.now().isoformat()} Error: {str(e)}"
            if redis_client.get(f"token_keeper:pending_refresh:{self.machine_id}:{instance.instance_id}"):
                self._check_and_refresh(instance)
            raise
        finally:
            instance.is_busy = False


    def _get_calls_in_window(self, token : str) -> int:
        """Count API calls in rolling window."""
        now = time.time()
        redis_client.zremrangebyscore(f"token:{token}:{self.machine_id}:timestamps", 0, now - self.window_seconds)
        return redis_client.zcard(f"token:{token}:{self.machine_id}:timestamps")
    
    def _record_call(self, token : str):
        """Record API call timestamp."""
        now = time.time()
        redis_client.zadd(f"token:{token}:{self.machine_id}:timestamps", {str(now): now})

    def _rollback_call(self, token : str):
        """Rollback API call counter."""
        now = time.time()
        redis_client.zremrangebyscore(f"token:{token}:{self.machine_id}:timestamps", now - 1, now)
    
    def _handle_rate_limit(self, instance : TokenInstance):
        """Handle token rate limiting (429 error)."""
        logger.warning(f"Rate limit hit for instance {instance.instance_id}. Parking for 60s.")
        instance.last_health = 0
        time.sleep(60)
        instance.last_health = time.time()
        
    # Job Requests

    def _job_sync_complete(self, profile_id: int):
        try:
            profile = Profile.objects.get(id=profile_id)
        except Profile.DoesNotExist:
            logger.error(f"Profile {profile_id} does not exist.")
        job_type = 'sync_complete'

        time.sleep(10)
        update_profile_trophy_counts(profile)
        profile.set_sync_status('synced')
        logger.info(f"{profile.display_psn_username} account has finished syncing!")
    
    def _job_check_profile_badges(self, profile_id: int):
        try:
            profile = Profile.objects.get(id=profile_id)
        except Profile.DoesNotExist:
            logger.error(f"Profile {profile_id} does not exist.")
        job_type = 'check_profile_badges'

        check_profile_badges(profile)
        logger.info(f"Badges checked for {profile.display_psn_username} successfully!")
    
    def _job_handle_privacy_error(self, profile_id: int):
        try:
            profile = Profile.objects.get(id=profile_id)
        except Profile.DoesNotExist:
            logger.error(f"Profile {profile_id} does not exist.")
        job_type = 'handle_privacy_error'

        time.sleep(1)

        if not profile.psn_history_public:
            profile.set_sync_status('error')
        logger.info('Privacy error handled.')

    def _job_sync_profile_data(self, profile_id: int) -> Profile:
        try:
            profile = Profile.objects.get(id=profile_id)
        except Profile.DoesNotExist:
            logger.error(f"Profile {profile_id} does not exist.")
        job_type = 'sync_profile_data'

        try:
            legacy = self._execute_api_call(self._get_instance_for_job(job_type), profile, 'get_profile_legacy')
        except Exception as e:
            profile.set_sync_status('error')
            raise
        is_public = not legacy['profile']['trophySummary']['level'] == 0
        PsnApiService.update_profile_from_legacy(profile, legacy, is_public)
        try:
            region = self._execute_api_call(self._get_instance_for_job(job_type), profile, 'get_region')
        except Exception as e:
            profile.set_sync_status('error')
            raise    
        PsnApiService.update_profile_region(profile, region)

    def _job_sync_trophy_titles(self, profile_id: int, force_title_stats:bool=False):
        try:
            profile = Profile.objects.get(id=profile_id)
        except Profile.DoesNotExist:
            logger.error(f"Profile {profile_id} does not exist.")
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
        
        touched_profilegame_ids = []
        num_title_stats = 0
        for title in trophy_titles:
            game, created, _ = PsnApiService.create_or_update_game(title)
            profile_game, _ = PsnApiService.create_or_update_profile_game(profile, game, title)
            touched_profilegame_ids.append(profile_game.id)
            for platform in game.title_platform:
                if platform in TITLE_STATS_SUPPORTED_PLATFORMS:
                    num_title_stats += 1
                    break
            title_defined_trophies_total = title.defined_trophies.bronze + title.defined_trophies.silver + title.defined_trophies.gold + title.defined_trophies.platinum
            args = [game.np_communication_id, game.title_platform[0] if not game.title_platform[0] == 'PSPC' else game.title_platform[1]]
            if created or game.get_total_defined_trophies() != title_defined_trophies_total or not TrophyGroup.objects.filter(game=game).exists():
                PSNManager.assign_job('sync_trophy_groups', args, profile.id)
                job_counter += 1
            PSNManager.assign_job('sync_trophies', args, profile.id)
            job_counter +=1
        
        profile.add_to_sync_target(job_counter)
        update_profile_games(profile)

        # Check profile health after processing all titles
        PSNManager.assign_job('sync_profilegame_stats', args=[touched_profilegame_ids], profile_id=profile.id, priority_override='low_priority')
        PSNManager.assign_job('check_profile_health', args=[], profile_id=profile.id, priority_override='low_priority')

        # Assign jobs for title_stats
        page_size = 200
        limit = page_size
        offset = 0
        for i in range(num_title_stats // page_size):
            args=[limit, offset, page_size, False, force_title_stats]
            PSNManager.assign_job('sync_title_stats', args, profile_id)
            limit += page_size
            offset += page_size
        else:
            args=[limit, offset, page_size, True, force_title_stats]
            PSNManager.assign_job('sync_title_stats', args, profile_id)
    
    def _job_sync_trophy_groups(self, profile_id: int, np_communication_id: str, platform: str):
        try:
            profile = Profile.objects.get(id=profile_id)
            game = Game.objects.get(np_communication_id=np_communication_id)
        except Profile.DoesNotExist:
            logger.error(f"Profile {profile_id} does not exist.")
        except Game.DoesNotExist:
            logger.error(f"Game {np_communication_id} does not exist.")
        job_type='sync_trophy_groups'
        
        trophy_groups_summary = self._execute_api_call(self._get_instance_for_job(job_type), profile, 'trophy_groups_summary', np_communication_id=np_communication_id, platform=PlatformType(platform))
        for group in trophy_groups_summary.trophy_groups:
            trophy_group, created = PsnApiService.create_or_update_trophy_groups_from_summary(game, group)
        profile.increment_sync_progress()
        logger.info(f"Trophy group summaries for {game.title_name} synced successfully!")

    def _job_sync_title_stats(self, profile_id: int, limit: int, offset: int, page_size: int, is_last: bool=False, force_all: bool=False):
        logger.info(f"Syncing title stats | Force All: {force_all}")
        try:
            profile = Profile.objects.get(id=profile_id)
        except Profile.DoesNotExist:
            logger.error(f"Profile {profile_id} does not exist.")
        job_type = 'sync_title_stats'
        job_counter = 1 # Default 1 job for badge checking at the end

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
                game.add_title_id(title.np_title_id)
                args = [title.np_title_id, title.np_communication_id]
                PSNManager.assign_job('sync_title_id', args=args, profile_id=profile.id, priority_override='medium_priority')
                job_counter += 1
            for stats in remaining_title_stats:
                PsnApiService.update_profile_game_with_title_stats(profile, stats)
            
            profile.add_to_sync_target(job_counter)
            PSNManager.check_profile_badges(profile, 'low_priority')
            PSNManager.sync_complete(profile, 'low_priority')

    def _job_sync_trophies(self, profile_id: int, np_communication_id: str, platform: str):
        try:
            profile = Profile.objects.get(id=profile_id)
            game = Game.objects.get(np_communication_id=np_communication_id, title_platform__contains=platform)
        except Profile.DoesNotExist:
            logger.error(f"Profile {profile_id} does not exist.")
        except Game.DoesNotExist:
            logger.error(f"Game {np_communication_id} does not exist.")
        job_type = 'sync_trophies'

        logger.info(f"Fetching trophies for profile {profile_id}, game {np_communication_id} on platform {platform}")
        trophies = self._execute_api_call(self._get_instance_for_job(job_type), profile, 'trophies', np_communication_id=np_communication_id, platform=PlatformType(platform), include_progress=True, trophy_group_id='all', page_size=500)
        for trophy_data in trophies:
            trophy, _ = PsnApiService.create_or_update_trophy_from_trophy_data(game, trophy_data)
            PsnApiService.create_or_update_earned_trophy_from_trophy_data(profile, trophy, trophy_data)
        profile.increment_sync_progress()
    
    def _job_sync_title_id(self, profile_id: str, title_id_str: str, np_communication_id: str):
        try:
            profile = Profile.objects.get(id=profile_id)
            title_id = TitleID.objects.get(title_id=title_id_str)
        except TitleID.DoesNotExist:
            logger.warning(f"Title ID {title_id_str} not in title_id table.")
        job_type='sync_title_id'
        
        logger.info(f"Beginning sync for {title_id.title_id} | {np_communication_id}")
        try:
            try:
                game = Game.objects.get(np_communication_id=np_communication_id)
            except Game.DoesNotExist:
                logger.warning(f"Game {title_id.title_id} | {np_communication_id} not in database.")
            
            game_title = self._execute_api_call(self._get_instance_for_job(job_type), profile, 'game_title', title_id=title_id.title_id, platform=PlatformType(title_id.platform), account_id=profile.account_id, np_communication_id=game.np_communication_id)
            if game_title:
                details = game_title.get_details()[0]
                error_code = details.get('errorCode', None)
                if error_code is None:
                    concept, _ = PsnApiService.create_concept_from_details(details)
                    
                    release_date = details.get('defaultProduct', {}).get('releaseDate', None)
                    if release_date is None:
                        release_date = details.get('releaseDate', {}).get('date', '')
                    media_data = self._extract_media(details)
                    concept.update_release_date(release_date)
                    concept.update_media(media_data['all_media'], media_data['icon_url'], media_data['bg_url'])
                    game.add_concept(concept)
                    game.add_region(title_id.region)
                    concept.add_title_id(title_id.title_id)
                    concept.check_and_mark_regional()
                    profile.increment_sync_progress()
                    logger.info(f"Title ID {title_id.title_id} - {concept.unified_title} sync'd successfully!")
                else:
                    profile.increment_sync_progress()
                    logger.warning(f"Concept for {title_id.title_id} returned an error code.")
                    logger.info(f"Title ID {title_id.title_id} sync'd successfully!")
            else:
                profile.increment_sync_progress()
                logger.warning(f"Couldn't get game_title for Title ID {title_id.title_id}")
        except Exception as e:
            profile.increment_sync_progress()
            logger.error(f"Error while syncing Title ID {title_id.title_id}: {str(e)}")
    
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
        try:
            profile = Profile.objects.get(id=profile_id)
        except Profile.DoesNotExist:
            logger.error(f"Profile {profile_id} does not exist.")
        job_type = 'profile_refresh'
        job_counter = 1 # Default 1 job for badge checking at the end

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
        
        touched_profilegame_ids = []
        for title in trophy_titles_to_be_updated:
            game, created, _ = PsnApiService.create_or_update_game(title)
            profile_game, _ = PsnApiService.create_or_update_profile_game(profile, game, title)
            touched_profilegame_ids.append(profile_game.id)
            title_defined_trophies_total = title.defined_trophies.bronze + title.defined_trophies.silver + title.defined_trophies.gold + title.defined_trophies.platinum
            args = [game.np_communication_id, game.title_platform[0] if not game.title_platform[0] == 'PSPC' else game.title_platform[1]]
            if created or game.get_total_defined_trophies() != title_defined_trophies_total or not TrophyGroup.objects.filter(game=game).exists():
                PSNManager.assign_job('sync_trophy_groups', args, profile.id)
                job_counter += 1
            PSNManager.assign_job('sync_trophies', args, profile.id, priority_override='medium_priority')
            job_counter += 1
        
        profile.add_to_sync_target(job_counter)
        update_profile_games(profile)
        job_counter = 0
        
        title_stats_to_be_updated = []
        page_size = 200
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
                game.add_title_id(title.np_title_id)
                args = [title.np_title_id, title.np_communication_id]
                PSNManager.assign_job('sync_title_id', args=args, profile_id=profile.id)
                job_counter += 1
            for stats in remaining_title_stats:
                PsnApiService.update_profile_game_with_title_stats(profile, stats)
            
            profile.add_to_sync_target(job_counter)
        if timezone.now() - timedelta(days=1) > profile.last_profile_health_check:
            PSNManager.assign_job('check_profile_health', args=[], profile_id=profile.id, priority_override='medium_priority')
        PSNManager.assign_job('sync_profilegame_stats', args=[touched_profilegame_ids], profile_id=profile.id, priority_override='medium_priority')
        PSNManager.check_profile_badges(profile, 'medium_priority')
        PSNManager.sync_complete(profile, 'medium_priority')

    def _sync_profilegame_stats(self, profile_id: int, touched_profilegame_ids: list[int]):
        try:
            profile = Profile.objects.get(id=profile_id)
        except Profile.DoesNotExist:
            logger.error(f"Profile {profile_id} does not exist.")
        job_type = 'sync_profilegame_stats'

        profile.update_plats()
        PsnApiService.update_profilegame_stats(touched_profilegame_ids)
        logger.info(f"ProfileGame Stats updated for {profile_id} successfully! | {len(touched_profilegame_ids)} profilegames updated")

    def _job_check_profile_health(self, profile_id: int):
        try:
            profile = Profile.objects.get(id=profile_id)
        except Profile.DoesNotExist:
            logger.error(f"Profile {profile_id} does not exist.")
        job_type = 'check_profile_health'

        time.sleep(10)

        summary = self._execute_api_call(self._get_instance_for_job(job_type), profile, 'trophy_summary')
        tracked_trophies = PsnApiService.get_profile_trophy_summary(profile)

        earned = summary.earned_trophies
        summary_total = earned.bronze + earned.silver + earned.gold + earned.platinum

        logger.info(f"Profile {profile_id} health: Summary: {summary_total} | Tracked: {tracked_trophies['total']} | {summary_total == tracked_trophies['total']}")

        if summary_total > tracked_trophies['total']:
            trophy_titles_to_be_updated = []
            page_size = 400
            limit = page_size
            offset = 0
            is_full = True
            while is_full:
                titles = self._execute_api_call(self._get_instance_for_job(job_type), profile, 'trophy_titles', limit=limit, offset=offset, page_size=page_size)
                for title in titles:
                    game, tracked = PsnApiService.get_tracked_trophies_for_game(profile, title.np_communication_id)
                    title_total = title.earned_trophies.bronze + title.earned_trophies.silver + title.earned_trophies.gold + title.earned_trophies.platinum
                    logger.info(f"{game.np_communication_id} - Tracked: {tracked['total']} | Title: {title_total} | {tracked['total'] == title_total}")
                    if tracked['total'] != title_total:
                        trophy_titles_to_be_updated.append({'title': title, 'game': game})
                        logger.info(f"Mismatch for profile {profile_id} - {title.np_communication_id}: Tracked: {tracked['total']} | Title: {title_total}")
                is_full = len(titles) == page_size
                limit += page_size
                offset += page_size
        
            if len(trophy_titles_to_be_updated) > 0:
                for title in trophy_titles_to_be_updated:
                    game = title['game']
                    args = [game.np_communication_id, game.title_platform[0] if not game.title_platform[0] == 'PSPC' else game.title_platform[1]]
                    PSNManager.assign_job('sync_trophies', args=args, profile_id=profile.id, priority_override='high_priority')

    def _job_sync_games_only(self, profile_id: int):
        try:
            profile = Profile.objects.get(id=profile_id)
        except Profile.DoesNotExist:
            logger.error(f"Profile {profile_id} does not exist.")
        job_type = 'sync_games_only'

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
        
        num_title_stats = 0
        for title in trophy_titles:
            game, created, _ = PsnApiService.create_or_update_game(title)
            profile_game, _ = PsnApiService.create_or_update_profile_game(profile, game, title)
            for platform in game.title_platform:
                if platform in TITLE_STATS_SUPPORTED_PLATFORMS:
                    num_title_stats += 1
                    break
            title_defined_trophies_total = title.defined_trophies.bronze + title.defined_trophies.silver + title.defined_trophies.gold + title.defined_trophies.platinum
            args = [game.np_communication_id, game.title_platform[0] if not game.title_platform[0] == 'PSPC' else game.title_platform[1]]
            if game.get_total_defined_trophies() != title_defined_trophies_total:
                PSNManager.assign_job('sync_trophy_groups', args, profile.id)

        # Assign jobs for title_stats
        page_size = 200
        limit = page_size
        offset = 0
        for i in range(num_title_stats // page_size):
            args=[limit, offset, page_size, False, True]
            PSNManager.assign_job('sync_title_stats', args, profile_id)
            limit += page_size
            offset += page_size
        else:
            args=[limit, offset, page_size, True, True]
            PSNManager.assign_job('sync_title_stats', args, profile_id)

    @property
    def stats(self) -> Dict:
        stats = {}
        for group_id, group in self.group_instances.items():
            for inst_id, inst in group['instances'].items():
                auth = inst.client.authenticator
                key = f"{group_id}-{inst_id}"
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