from collections import namedtuple
import json
import time
import threading
import logging
import os
import atexit
import concurrent.futures
from typing import Optional, Dict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from psnawp_api import PSNAWP
from psnawp_api.models.trophies.trophy_constants import PlatformType
from requests import HTTPError
from .models import Profile, Game
from .services import PsnApiService
from .utils import redis_client, log_api_call, TITLE_ID_BLACKLIST

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
        self.token_wait_interval = 10
        self.window_seconds = int(os.getenv("WINDOW_SECONDS", 900))
        self.max_calls_per_window = int(os.getenv("MAX_CALLS_PER_WINDOW", 300))
        self.stats_interval = 5
        self.machine_id = os.getenv("MACHINE_ID", "default")

        if len(self.tokens) != 3:
            raise ValueError("Exactly 3 PSN_TOKENS required")
        
        self.instances: Dict[int, TokenInstance] = {}
        self._health_thread = None
        self._pubsub_thread = None
        self._stats_thread = None
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=3)
        self._start_health_monitor()
        self._start_pubsub_listener()
        self._start_stats_publisher()
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

    def _start_stats_publisher(self):
        self._stats_thread = threading.Thread(target=self._publish_stats_loop, daemon=True)
        self._stats_thread.start()
        logger.info("TokenKeeper stats publisher started")

    def _health_loop(self):
        """Infinite loop: Check health every interval."""
        while True:
            time.sleep(self.health_interval)
            logger.info(f"Health loop tick.")
            redis_client.set("token_keeper:running", "1", ex=3600)
            for instance_id, inst in self.instances.items():
                self._check_and_refresh(inst)
    
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
                game_ids, title_stats = self._sync_profile_games_data(profile, job_type)
                result = {'game_ids': game_ids, 'title_stats': title_stats}
            elif request == "sync_trophy_titles_for_title":
                result = self._sync_trophy_titles_for_title(profile, job_type, args[0])
            elif request == "refresh_profile_game_data":
                game_ids = self._refresh_profile_games_data(profile, job_type, args[0])
                result = {'game_ids': game_ids}
            elif request == "sync_profile_trophies":
                game = Game.objects.get(np_communication_id=args[0])
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
        start = time.time()
        while time.time() - start < self.token_wait_interval:
            is_high_prio = job_type in ["initial_sync", "profile_refresh"]
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
        
    # Job Requests
    def _sync_profile_data(self, profile: Profile, job_type: str) -> Profile:
        try:
            legacy = self._execute_api_call(self._get_instance_for_job(job_type), profile, 'get_profile_legacy')
            profile = PsnApiService.update_profile_from_legacy(profile, legacy)
            return profile
        except HTTPError as e:
            logger.error(f"Failed profile sync for {profile.id}: {e}")
            raise
    
    def _sync_profile_games_data(self, profile: Profile, job_type: str) -> tuple[list, list]:
        page_size = 400
        offset = 0
        limit = page_size

        trophy_titles = []
        profile_game_comm_ids = []
        is_full = True
        while is_full:
            titles = self._execute_api_call(self._get_instance_for_job(job_type), profile, 'trophy_titles', limit=limit, offset=offset, page_size=page_size)
            trophy_titles.extend(titles)
            is_full = len(titles) >= page_size
            offset += page_size
            limit += page_size

            for title in titles:
                profile_game_comm_ids.append(title.np_communication_id)

        page_size = 200
        offset = 0
        limit = page_size
        title_stats = []
        is_full = True
        while is_full:
            titles = self._execute_api_call(self._get_instance_for_job(job_type), profile, 'title_stats', limit=limit, offset=offset, page_size=page_size)
            title_stats.extend(titles)
            is_full = len(titles) >= page_size
            offset += page_size
            limit += page_size
        
        for stats in title_stats[:]:
            if stats.title_id in TITLE_ID_BLACKLIST:
                title_stats.remove(stats)

        remaining_title_stats, _ = self._sync_profile_games(profile, trophy_titles, title_stats)
        remaining_title_stats_list = []
        for stats in remaining_title_stats:
            remaining_title_stats_list.append({
                'title_id': stats.title_id,
                'name': stats.name,
                'image_url': stats.image_url,
                'category': stats.category.name,
                'play_count': stats.play_count if stats.play_count else 0,
                'first_played_date_time': stats.first_played_date_time.timestamp() if stats.first_played_date_time else None,
                'last_played_date_time': stats.last_played_date_time.timestamp() if stats.last_played_date_time else None,
                'play_duration': stats.play_duration.total_seconds() if stats.play_duration else None,
            })

        logger.info(f"Synced {len(profile_game_comm_ids)} games for profile {profile.id}")
        return profile_game_comm_ids, remaining_title_stats_list
    
    def _sync_trophy_titles_for_title(self, profile: Profile, job_type: str, title_stats: list):
        title_ids = []
        for stats in title_stats:
            title_ids.append(stats['title_id'])
        titles = self._execute_api_call(self._get_instance_for_job(job_type), profile, 'trophy_titles_for_title', title_ids=title_ids)

        for title in titles:
            PsnApiService.assign_title_id(title.np_communication_id, title.np_title_id)

        for stats in title_stats:
            stats['category'] = PlatformType(stats['category'])
            stats['first_played_date_time'] = datetime.fromtimestamp(stats['first_played_date_time'], tz=timezone.utc)
            stats['last_played_date_time'] = datetime.fromtimestamp(stats['last_played_date_time'], tz=timezone.utc)
            stats['play_duration'] = timedelta(seconds=stats['play_duration']) if not stats['play_duration'] == None else None
            TitleStats = namedtuple('TitleStats', ['title_id', 'name', 'image_url', 'category', 'play_count', 'first_played_date_time', 'last_played_date_time', 'play_duration'])
            named_stats = TitleStats(**stats)
            PsnApiService.update_profile_game_with_title_stats(profile, named_stats)

    def _refresh_profile_games_data(self, profile: Profile, job_type: str, latest_sync_int: int) -> list:
        page_size = 400
        limit = page_size
        offset = 0
        latest_sync = datetime.fromtimestamp(latest_sync_int, tz=timezone.utc)

        trophy_titles_to_be_updated = []
        end_found = False
        while True:
            trophy_titles = self._execute_api_call(self._get_instance_for_job(job_type), profile, 'trophy_titles', limit=limit, offset=offset, page_size=page_size)
            for title in trophy_titles:
                if title.last_updated_datetime >= latest_sync:
                    trophy_titles_to_be_updated.append(title)
                else:
                    end_found = True
                    break
            if end_found:
                break
            offset += page_size
            limit += page_size

        page_size = 200
        limit = page_size
        offset = 0
        title_stats_to_be_updated = []
        end_found = False
        while True:
            title_stats = self._execute_api_call(self._get_instance_for_job(job_type), profile, 'title_stats', limit=limit, offset=offset, page_size=page_size)
            for title in title_stats:
                if title.last_played_date_time >= latest_sync:
                    title_stats_to_be_updated.append(title)
                else:
                    end_found = True
                    break
            if end_found:
                break
            offset += page_size
            limit += page_size

        profile_game_comm_ids = []
        for title in trophy_titles_to_be_updated:
            profile_game_comm_ids.append(title.np_communication_id)
        
        remaining_title_stats, _ = self._sync_profile_games(profile, trophy_titles_to_be_updated, title_stats_to_be_updated)
        title_ids = []
        for stats in remaining_title_stats:
            title_ids.append(stats.title_id)
        
        page_size = 5
        limit = page_size
        offset = 0
        while offset < len(title_ids):
            title_ids_package = title_ids[offset:limit]
            titles = self._execute_api_call(self._get_instance_for_job(job_type), profile, 'trophy_titles_for_title', title_ids=title_ids_package)
            for title in titles:
                PsnApiService.assign_title_id(title.np_communication_id, title.np_title_id)
            offset += page_size
            limit += page_size

        for stats in remaining_title_stats:
            PsnApiService.update_profile_game_with_title_stats(profile, stats)
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
        for title in trophy_titles:
            game, _, needs_trophy_update = PsnApiService.create_or_update_game(title)
            if needs_trophy_update:
                games_needing_trophy_updates.append(game)
            profile_game, _ = PsnApiService.create_or_update_profile_game(profile, game, title)
        
        for stats in title_stats[:]:
            updated = PsnApiService.update_profile_game_with_title_stats(profile, stats)
            if updated:
                title_stats.remove(stats)
                    
        return title_stats, games_needing_trophy_updates

    def _sync_profile_games_title_stats(self, profile: Profile, title_stats: list):
        """Update Games & ProfileGame with only title_stats - no trophy data."""
        for stat in title_stats:
            games = Game.objects.filter(title_id=stat.title_id)
            if games:
                for game in games:
                    PsnApiService.update_profile_game_with_title_stats(profile, game, stat)

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