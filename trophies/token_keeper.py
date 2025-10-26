import difflib
import json
import time
import threading
import logging
import os
import atexit
import concurrent.futures
from typing import Optional, Dict
from dataclasses import dataclass
from datetime import datetime, timedelta
from psnawp_api import PSNAWP
from psnawp_api.models.trophies.trophy_constants import PlatformType
from requests import HTTPError
from .models import Profile, Game
from .services import PsnApiService
from .utils import redis_client, log_api_call

logger = logging.getLogger("psn_api")

@dataclass
class TokenInstance:
    instance_id: int
    token: str
    client: PSNAWP
    user_cache: dict = None
    last_health: float = time.time()
    last_refresh: float = 0
    is_busy: bool = False

    def __post_init__(self):
        if self.user_cache is None:
            self.user_cache = {}
    
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
        self.health_interval = 300
        self.refresh_threshold = 300
        self.window_seconds = int(os.getenv("WINDOW_SECONDS", 900))
        self.max_calls_per_window = int(os.getenv("MAX_CALLS_PER_WINDOW", 300))
        self.reserved_high_prio_calls = int(self.max_calls_per_window * 0.1)

        if len(self.tokens) != 3:
            raise ValueError("Exactly 3 PSN_TOKENS required")
        
        self.instances: Dict[int, TokenInstance] = {}
        self._health_thread = None
        self._pubsub_thread = None
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=3)
        self._start_health_monitor()
        self._start_pubsub_listener()
        redis_client.set("token_keeper:running", "1", ex=3600)
        atexit.register(self._cleanup)

    def _cleanup(self):
        """Clean up Redis stat on process exit."""
        logger.info("Cleaning up TokenKeeper Redis state")
        redis_client.delete("token_keeper:running")
        for i in range(3):
            redis_client.delete(f"token_keeper:instance:{i}:token")
            redis_client.delete(f"instance_lock:{i}")
        self._executor.shutdown(wait=True)
        logger.info("TokenKeeper Redis state cleaned")

    
    def _start_health_monitor(self):
        """Start background thread for proactive health checks."""
        self._health_thread = threading.Thread(target=self._health_loop, daemon=True)
        self._health_thread.start()
        logger.info("TokenKeeper health monitor started")
    
    def _start_pubsub_listener(self):
        self._pubsub_thread = threading.Thread(target=self._pubsub_loop, daemon=True)
        self._pubsub_thread.start()
        logger.info("TokenKeeper pub/sub listener started")

    def _health_loop(self):
        """Infinite loop: Check health every interval."""
        while True:
            time.sleep(self.health_interval)
            redis_client.set("token_keeper:running", "1", ex=3600)
            for instance_id, inst in self.instances.items():
                self._check_and_refresh(inst)
    
    def _check_and_refresh(self, inst : TokenInstance):
        """Refresh token if < time remaining and clean cache."""
        try:
            auth = inst.client.authenticator
            if auth.access_token_expiration_in < self.refresh_threshold:
                start = time.time()
                auth.fetch_access_token_from_refresh()
                inst.last_refresh = time.time()
                self._record_call(inst.token)
                log_api_call("keeper_refresh", inst.token, None, 200, time.time() - start)
                logger.debug(f"Instance {inst.instance_id} refreshed proactively")
            inst.cleanup_cache()
        except Exception as e:
            logger.error(f"Health check failed for {inst.instance_id}: {e}")
            inst.last_health = 0

    def _pubsub_loop(self):
        """Inits and listens to pub/sub structure."""
        pubsub = redis_client.pubsub()
        pubsub.subscribe("psn_api_requests")
        for message in pubsub.listen():
            if message['type'] == 'message':
                data = json.loads(message['data'])
                self._executor.submit(self._handle_request, data)
    
    def _handle_request(self, data: Dict):
        """Handles all PSN API requests, publishing via Redis."""
        task_id = data.get('task_id')
        job_type = data.get('job_type')
        request = data.get('request')
        profile_id = data.get('profile_id')
        args = data.get('args', {})

        profile = Profile.objects.get(id=profile_id) if profile_id else None

        logger.info(f"Handling request: {request} for profile {profile_id if profile_id else 'no profile'}")
        try:
            if request == "sync_profile_data":
                profile = self._sync_profile_data(profile, job_type)
                result = {'profile_id': profile.id}
            elif request == "sync_profile_games_data":
                game_ids = self._sync_profile_games_data(profile, job_type)
                result = {'game_ids': game_ids}
            elif request == "refresh_profile_game_data":
                game_ids = self._refresh_profile_games_data(profile, job_type, args[0])
                result = {'game_ids': game_ids}
            elif request == "sync_profile_trophies":
                profile = Profile.objects.get(id=args[0])
                game = Game.objects.get(np_communication_id=args[1])
                game_id = self._sync_profile_trophy_data(job_type, profile, game)
                result = {'game_id': game_id}
            else:
                raise ValueError(f"Unknown request: {request}")
            
            redis_client.publish(
                f"psn_api_responses:{task_id}",
                json.dumps({"status": "success", "result": result})
            )
            logger.info(f"Request {request} for profile {profile_id if profile_id else 'no profile'} handled successfully!")
        except Exception as e:
            logger.error(f"Request {request} for profile {profile_id if profile_id else 'no profile'} failed for task {task_id}: {e}")
            redis_client.publish(
                f"psn_api_responses:{task_id}",
                json.dumps({'status': 'error', 'error': str(e)})
            )

    def _get_instance_for_job(self, job_type: str) -> Optional[TokenInstance]:
        """Selects best instance for job, respecting workload and priority."""
        is_high_prio = job_type in ["initial_sync", "profile_refresh"]
        instance_scores = {}
        for inst in self.instances.values():
            if not inst.is_busy and self._is_healthy(inst):
                calls = self._get_calls_in_window(inst.token)
                if not is_high_prio and calls >= (self.max_calls_per_window - self.reserved_high_prio_calls):
                    continue
                instance_scores[inst.instance_id] = calls
        
        if instance_scores:
            best_id = min(instance_scores, key=instance_scores.get)
            inst = self.instances[best_id]
            inst.is_busy = True
            return inst
        return None
    
    def _execute_api_call(self, instance : TokenInstance, profile : Profile, endpoint : str, **kwargs):
        logger.info(f"API called: {endpoint} | instance: {instance.instance_id} | args: {kwargs}")
        lookup_key = profile.account_id if profile.account_id else profile.psn_username
        if lookup_key not in instance.user_cache:
            instance.user_cache[lookup_key] = {
                "user": (instance.client.user(account_id=profile.account_id) if profile.account_id else instance.client.user(online_id=profile.psn_username)),
                "timestamp": datetime.now()
            }
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
            else:
                raise ValueError(f"Unknown endpoint: {endpoint}")
            
            log_api_call(endpoint, instance.token, profile.id if profile else None, 200, time.time() - start_time)
            instance.is_busy = False
            return data
        except HTTPError as e:
            log_api_call(endpoint, instance.token, profile.id if profile else None, e.response.status_code, time.time() - start_time, str(e))
            instance.is_busy = False
            self._rollback_call(instance.token)
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

    def initialize_instances(self):
        """Create 3 live PSNAWP clients."""
        for i, token in enumerate(self.tokens):
            start_time = time.time()
            client = PSNAWP(token)
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
        
    # Job Requests
    def _sync_profile_data(self, profile: Profile, job_type: str) -> Profile:
        try:
            legacy = self._execute_api_call(self._get_instance_for_job(job_type), profile, 'get_profile_legacy')
            profile = PsnApiService.update_profile_from_legacy(profile, legacy)
            return profile
        except HTTPError as e:
            logger.error(f"Failed profile sync for {profile.id}: {e}")
            raise
    
    def _sync_profile_games_data(self, profile: Profile, job_type: str) -> list:
        page_size = 200
        offset = 0
        instance = self._get_instance_for_job(job_type)

        trophy_titles = []
        profile_game_comm_ids = []
        is_full = True
        while is_full:
            titles = self._execute_api_call(instance, profile, 'trophy_titles', limit=None, offset=offset, page_size=page_size)
            trophy_titles.extend(titles)
            is_full = len(titles) >= page_size
            offset += page_size

            for title in titles:
                profile_game_comm_ids.append(title.np_communication_id)

        offset = 0
        title_stats = []
        is_full = True
        while is_full:
            titles = self._execute_api_call(instance, profile, 'title_stats', limit=None, offset=offset, page_size=page_size)
            title_stats.extend(titles)
            is_full = len(titles) >= page_size
            offset += page_size

        self._sync_profile_games(profile, trophy_titles, title_stats)
        logger.info(f"Synced {len(profile_game_comm_ids)} games for profile {profile.id}")
        return profile_game_comm_ids
    
    def _refresh_profile_games_data(self, profile: Profile, job_type: str, latest_sync: datetime) -> list:
        page_size = 200
        offset = 0
        instance = self._get_instance_for_job(job_type)

        trophy_titles_to_be_updated = []
        end_found = False
        while True:
            trophy_titles = self._execute_api_call(instance, profile, 'trophy_titles', limit=None, offset=offset, page_size=page_size)
            for title in trophy_titles:
                if title.last_updated_datetime >= latest_sync:
                    trophy_titles_to_be_updated.append(title)
                else:
                    end_found = True
                    break
            if end_found:
                break
            offset += page_size

        offset = 0
        title_stats_to_be_updated = []
        end_found = False
        while True:
            title_stats = self._execute_api_call(instance, profile, 'title_stats', limit=None, offset=0, page_size=page_size)
            for title in title_stats:
                if title.last_played_date_time >= latest_sync:
                    title_stats_to_be_updated.append(title)
                else:
                    end_found = True
                    break
            if end_found:
                break
            offset += page_size
        
        profile_game_comm_ids = []
        for title in trophy_titles_to_be_updated:
            profile_game_comm_ids.append(title.np_communication_id)
        
        remaining_title_stats, _ = self._sync_profile_games(profile, trophy_titles_to_be_updated, title_stats_to_be_updated)
        self._sync_profile_games_title_stats(profile, remaining_title_stats)
        logger.info(f"Refreshed {len(profile_game_comm_ids)} games for profile {profile.id}")
        return profile_game_comm_ids


    def _sync_profile_trophy_data(self, job_type: str, profile: Profile, game: Game):
        """Sync Trophy & EarnedTrophy models for specified Game & Profile."""
        try:
            trophies_data = self._execute_api_call(self._get_instance_for_job(job_type), profile, 'trophies', np_communication_id=game.np_communication_id, platform=PlatformType(game.title_platform[0]), include_progress=True, trophy_group_id='all', page_size=500)
            for trophy_data in trophies_data:
                trophy, _ = PsnApiService.create_or_update_trophy_from_trophy_data(game, trophy_data)
                PsnApiService.create_or_update_earned_trophy_from_trophy_data(profile, trophy, trophy_data)
            logger.info(f"Synced trophies for {game.np_communication_id} for profile {profile.id}")
            return game.np_communication_id
        except HTTPError as e:
            logger.error(f"Failed trophy sync for game {game.np_communication_id} for profile {profile.id}: {e}")

    # Job Helpers
    def _sync_profile_games(self, profile: Profile, trophy_titles: list, title_stats: list):
        games_needing_trophy_updates = []
        for trophy_title in trophy_titles:
            matched = False
            for i, title_stat in enumerate(title_stats):
                if (
                    self._match_game_names(trophy_title.title_name, title_stat.name)
                    and str(title_stat.category.name) in [platform.value for platform in trophy_title.title_platform]
                ):
                    game, created, needs_trophy_update = PsnApiService.create_or_update_game_from_title(trophy_title, title_stat)
                    if needs_trophy_update and not created:
                        games_needing_trophy_updates.append(game)
                    PsnApiService.create_or_update_profile_game_from_title(profile, game, trophy_title, title_stat)
                    title_stats.pop(i)
                    matched = True
                    break
            if not matched:
                game, created, needs_trophy_update = PsnApiService.create_or_update_game_from_title(trophy_title)
            if needs_trophy_update and not created:
                games_needing_trophy_updates.append(game)
            PsnApiService.create_or_update_profile_game_from_title(profile, game, trophy_title)
        return title_stats, games_needing_trophy_updates
    
    def _match_game_names(self, name1, name2, threshold=0.9):
        """Fuzzy match game names with normalization."""
        name1 = name1.lower().replace('™', '').replace('®', '').strip()
        name2 = name2.lower().replace('™', '').replace('®', '').strip()
        ratio = difflib.SequenceMatcher(None, name1, name2).ratio()
        return ratio >= threshold

    def _sync_profile_games_title_stats(self, profile: Profile, title_stats: list):
        """Update ProfileGame with only title_stats - no trophy data."""
        for stat in title_stats:
            try:
                game = Game.objects.get(title_id=stat.title_id)
            except Game.DoesNotExist as e:
                logger.warning(f"Game with title id {stat.title_id} could not be found.")
                continue
            PsnApiService.update_game_from_title_stats(profile, game, stat)

    @property
    def stats(self) -> Dict:
        return {
            inst.instance_id: {
                "busy": inst.is_busy,
                "healthy": time.time() - inst.last_health < self.health_interval,
                "calls_in_window": self._get_calls_in_window(inst.token)
            }
            for inst in self.instances.values()
        }

token_keeper = TokenKeeper()