from collections import namedtuple
import json
import time
import threading
import logging
import os
import atexit
from typing import Optional, Dict
from dataclasses import dataclass
from datetime import datetime, timedelta
from psnawp_api import PSNAWP
from psnawp_api.models.trophies.trophy_constants import PlatformType
from requests import HTTPError
from .models import Profile, Game
from .services import PsnApiService
from .psn_manager import PSNManager
from .utils import redis_client, log_api_call, TITLE_ID_BLACKLIST, TITLE_STATS_SUPPORTED_PLATFORMS

logger = logging.getLogger("psn_api")

@dataclass
class TokenInstance:
    instance_id: int
    token: str
    client: PSNAWP
    user_cache: dict = None
    access_expiry: datetime = None
    refresh_expiry: datetime = None
    last_health: float = time.time()
    last_refresh: float = 0
    is_busy: bool = False

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
                    if redis_client.get("token_keeper:running"):
                        logger.info("TokenKeeper already running in another process")
                        cls._instance = None
                    else:
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

        if len(self.tokens) != 3:
            raise ValueError("Exactly 3 PSN_TOKENS required")
        
        self.instances: Dict[int, TokenInstance] = {}
        self._health_thread = None
        self._stats_thread = None
        self._job_workers = []
        self.initialize_instances()
        self._start_health_monitor()
        self._start_stats_publisher()
        self._start_job_workers()
        redis_client.set("token_keeper:running", "1", ex=3600)
        atexit.register(self._cleanup)
    
    def _publish_stats_loop(self):
        while True:
            time.sleep(self.stats_interval)
            try:
                stats = self.stats
                stats_with_id = {"machine_id": self.machine_id, "instances": stats}
                redis_client.publish("token_keeper_stats", json.dumps(stats_with_id))
                redis_client.set("token_keeper_latest_stats", json.dumps(stats_with_id), ex=60)
            except Exception as e:
                logger.error(f"Error publishing stats: {e}")

    def _cleanup(self):
        """Clean up Redis stat on process exit."""
        logger.info("Cleaning up TokenKeeper Redis state")
        redis_client.delete("token_keeper:running")
        for i in range(len(self.tokens)):
            redis_client.delete(f"token_keeper:instance:{i}:token")
            redis_client.delete(f"instance_lock:{i}")
            redis_client.delete(f"token_keeper:pending_refresh:{i}")
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
        num_workers = 3
        for _ in range(num_workers):
            t = threading.Thread(target=self._job_worker_loop, daemon=True)
            t.start()
            self._job_workers.append(t)
        logger.info(f"Started {num_workers} job worker threads")
    
    def _health_loop(self):
        """Infinite loop: Check health every interval."""
        while True:
            time.sleep(self.health_interval)
            redis_client.set("token_keeper:running", "1", ex=3600)
            for instance_id, inst in self.instances.items():
                self._check_and_refresh(inst)

    def initialize_instances(self):
        """Create 3 live PSNAWP clients."""
        for i, token in enumerate(self.tokens):
            start_time = time.time()
            client = PSNAWP(token)
            self._record_call(token)
            client.user(online_id='PlatPursuit') # Generates refresh tokens, etc.
            inst = TokenInstance(
                instance_id=i,
                token=token,
                client=client,
                user_cache={}
            )
            self.instances[i] = inst
            redis_client.set(f"token_keeper:instance:{i}:token", token)
            self._record_call(token)
            logger.info(f"Instance {i} initialized with live client")
            log_api_call("client_init", token, None, 200, time.time() - start_time)

    def _is_healthy(self, inst : TokenInstance) -> bool:
        """Quick health check."""
        try:
            return inst.client is not None
        except:
            return False
    
    def _check_and_refresh(self, inst : TokenInstance):
        """Refresh token if time remaining less than refresh threshold and clean cache."""
        try:
            if inst.get_access_expiry_in_seconds() < self.refresh_threshold:
                if inst.is_busy:
                    redis_client.set(f"token_keeper:pending_refresh:{inst.instance_id}", "1", ex=3600)
                    logger.debug(f"Instance {inst.instance_id} needs refresh but is busy.")
                    return
                start = time.time()
                inst.client = PSNAWP(inst.token)
                inst.client.user(online_id='PlatPursuit') # Generates refresh tokens, etc.
                inst.user_cache = {}
                inst.update_expiry_times()
                inst.last_refresh = time.time()
                self._record_call(inst.token)
                log_api_call("keeper_refresh", inst.token, None, 200, time.time() - start)
                logger.info(f"Instance {inst.instance_id} refreshed proactively")
            inst.cleanup_cache()
        except Exception as e:
            logger.error(f"Health check failed for {inst.instance_id}: {e}")
            inst.last_health = 0

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
                elif job_type == 'sync_title_stats':
                    self._job_sync_title_stats(profile_id, args[0], args[1], args[2], args[3])
                elif job_type == 'sync_trophies':
                    self._job_sync_trophies(profile_id, args[0], args[1])
                elif job_type == 'profile_refresh':
                    self._job_profile_refresh(profile_id)
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
        if queue_name != 'high_priority':
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
            for inst in self.instances.values():
                if not inst.is_busy and self._is_healthy(inst):
                    instance_scores[inst.instance_id] = self._get_calls_in_window(inst.token)
            
            if instance_scores:
                best_id = min(instance_scores, key=instance_scores.get)
                inst = self.instances[best_id]
                inst.is_busy = True
                return inst
            logger.info("Waiting for token...")
            time.sleep(0.1)
        logger.error(f"No token available for use.")
        return None
    
    def _execute_api_call(self, instance : TokenInstance, profile : Profile, endpoint : str, **kwargs):
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
        start_time = time.time()

        try:
            self._record_call(instance.token)
            if endpoint == "get_profile_legacy":
                data = user.get_profile_legacy()
            elif endpoint == "trophy_titles":
                data = list(user.trophy_titles(**kwargs))
            elif endpoint == "title_stats":
                data = list(user.title_stats(**kwargs))
            elif endpoint == "trophies":
                if "include_progress" in kwargs:
                    self._record_call(instance.token)
                data = list(user.trophies(**kwargs))
            elif endpoint == "trophy_titles_for_title":
                data = list(user.trophy_titles_for_title(**kwargs))
            else:
                raise ValueError(f"Unknown endpoint: {endpoint}")
            
            log_api_call(endpoint, instance.token, profile.id if profile else None, 200, time.time() - start_time)
            instance.is_busy = False
            if redis_client.get(f"token_keeper:pending_refresh:{instance.instance_id}"):
                self._check_and_refresh(instance)
            return data
        except HTTPError as e:
            log_api_call(endpoint, instance.token, profile.id if profile else None, e.response.status_code, time.time() - start_time, str(e))
            instance.is_busy = False
            self._rollback_call(instance.token)
            if redis_client.get(f"token_keeper:pending_refresh:{instance.instance_id}"):
                self._check_and_refresh(instance)
            raise

    def _get_calls_in_window(self, token : str) -> int:
        """Count API calls in rolling window."""
        now = time.time()
        redis_client.zremrangebyscore(f"token:{token}:timestamps", 0, now - self.window_seconds)
        return redis_client.zcard(f"token:{token}:timestamps")
    
    def _record_call(self, token : str):
        """Record API call timestamp."""
        now = time.time()
        redis_client.zadd(f"token:{token}:timestamps", {str(now): now})

    def _rollback_call(self, token : str):
        """Rollback API call counter."""
        now = time.time()
        redis_client.zremrangebyscore(f"token:{token}:timestamps", now - 1, now)
    
    def _handle_rate_limit(self, instance : TokenInstance):
        """Handle token rate limiting (429 error)."""
        logger.warning(f"Rate limit hit for instance {instance.instance_id}. Parking for 60s.")
        instance.last_health = 0
        time.sleep(60)
        instance.last_health = time.time()
        
    # Job Requests

    def _job_sync_profile_data(self, profile_id: int) -> Profile:
        try:
            profile = Profile.objects.get(id=profile_id)
        except Profile.DoesNotExist:
            logger.error(f"Profile {profile_id} does not exist.")
        job_type = 'sync_profile_data'

        legacy = self._execute_api_call(self._get_instance_for_job(job_type), profile, 'get_profile_legacy')
        PsnApiService.update_profile_from_legacy(profile, legacy)

    def _job_sync_trophy_titles(self, profile_id: int):
        try:
            profile = Profile.objects.get(id=profile_id)
        except Profile.DoesNotExist:
            logger.error(f"Profile {profile_id} does not exist.")
        job_type = 'sync_trophy_titles'

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
            game, _, _ = PsnApiService.create_or_update_game(title)
            profile_game, _ = PsnApiService.create_or_update_profile_game(profile, game, title)
            for platform in game.title_platform:
                if platform in TITLE_STATS_SUPPORTED_PLATFORMS:
                    num_title_stats += 1
                    break
            args = [game.np_communication_id, game.title_platform[0] if not game.title_platform[0] == 'PSPC' else game.title_platform[1]]
            PSNManager.assign_job('sync_trophies', args, profile.id)

        # Assign jobs for title_stats
        page_size = 200
        limit = page_size
        offset = 0
        for i in range(num_title_stats // page_size):
            args=[limit, offset, page_size, False]
            PSNManager.assign_job('sync_title_stats', args, profile_id)
            limit += page_size
            offset += page_size
        else:
            args=[limit, offset, page_size, True]
            PSNManager.assign_job('sync_title_stats', args, profile_id)

    def _job_sync_title_stats(self, profile_id: int, limit: int, offset: int, page_size: int, is_last: bool=False):
        try:
            profile = Profile.objects.get(id=profile_id)
        except Profile.DoesNotExist:
            logger.error(f"Profile {profile_id} does not exist.")
        job_type = 'sync_title_stats'

        title_stats = self._execute_api_call(self._get_instance_for_job(job_type), profile, 'title_stats', limit=limit, offset=offset, page_size=page_size)

        if is_last and len(title_stats) == page_size:
            args=[limit + page_size, offset + page_size, page_size, True]
            PSNManager.assign_job('sync_title_stats', args, profile_id)

        remaining_title_stats = []
        for stats in title_stats:
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
                PsnApiService.assign_title_id(title.np_communication_id, title.np_title_id)
            for stats in remaining_title_stats:
                PsnApiService.update_profile_game_with_title_stats(profile, stats)

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
    
    def _job_profile_refresh(self, profile_id: int):
        try:
            profile = Profile.objects.get(id=profile_id)
        except Profile.DoesNotExist:
            logger.error(f"Profile {profile_id} does not exist.")
        job_type = 'profile_refresh'

        last_sync = profile.last_synced
        PSNManager.assign_job('sync_profile_data', args=[], profile_id=profile.id)

        trophy_titles_to_be_updated = []
        page_size = 400
        limit = page_size
        offset = 0
        end_found = False
        while not end_found:
            titles = self._execute_api_call(self._get_instance_for_job(job_type), profile, 'trophy_titles', limit=limit, offset=offset, page_size=page_size)
            for title in titles:
                if title.last_updated_datetime > last_sync:
                    trophy_titles_to_be_updated.append(title)
                else:
                    end_found = True
                    break
            limit += page_size
            offset += page_size
        
        for title in trophy_titles_to_be_updated:
            game, _, _ = PsnApiService.create_or_update_game(title)
            profile_game, _ = PsnApiService.create_or_update_profile_game(profile, game, title)
            args = [game.np_communication_id, game.title_platform[0] if not game.title_platform[0] == 'PSPC' else game.title_platform[1]]
            PSNManager.assign_job('sync_trophies', args, profile.id)

        
        title_stats_to_be_updated = []
        page_size = 200
        limit = page_size
        offset = 0
        end_found = False
        while not end_found:
            title_stats = self._execute_api_call(self._get_instance_for_job(job_type), profile, 'title_stats', limit=limit, offset=offset, page_size=page_size)
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
                PsnApiService.assign_title_id(title.np_communication_id, title.np_title_id)
            for stats in remaining_title_stats:
                PsnApiService.update_profile_game_with_title_stats(profile, stats)
        

    @property
    def stats(self) -> Dict:
        stats = {}
        for inst in self.instances.values():
            auth = inst.client.authenticator
            stats[inst.instance_id] = {
                "busy": inst.is_busy,
                "healthy": time.time() - inst.last_health < self.health_interval,
                "calls_in_window": self._get_calls_in_window(inst.token),
                "access_token_expiry_in": inst.get_access_expiry_in_seconds(),
                "refresh_token_expiry_in": inst.get_refresh_expiry_in_seconds(),
                "token_scopes": auth.token_response.get("scope", "unknown") if auth.token_response else "none",
                "npsso_cookie": "present" if auth.npsso_cookie else "missing"
            }
        return stats

token_keeper = TokenKeeper()