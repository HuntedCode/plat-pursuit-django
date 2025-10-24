import time
import threading
import logging
import os
from typing import Optional, Dict
from dataclasses import dataclass
from datetime import datetime, timedelta
from psnawp_api import PSNAWP
from .utils import redis_client, log_api_call

logger = logging.getLogger("psn_api")

@dataclass
class TokenInstance:
    instance_id: int
    token: str
    client: PSNAWP
    user_cache: Dict[str, object] = None
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
    """Singleton: Maintains 3 live PSNAWP instances with proactive health management."""

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
        self._start_health_monitor()
        redis_client.set("token_keeper:running", "1", ex=3600)
    
    def _start_health_monitor(self):
        """Start background thread for proactive health checks."""
        self._health_thread = threading.Thread(target=self._health_loop, daemon=True)
        self._health_thread.start()
        logger.info("TokenKeeper health monitor started")

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
                self.record_call(inst.token)
                log_api_call("keeper_refresh", inst.token, None, 200, time.time() - start)
                logger.debug(f"Instance {inst.instance_id} refreshed proactively")
            inst.cleanup_cache()
        except Exception as e:
            logger.error(f"Health check failed for {inst.instance_id}: {e}")
            inst.last_health = 0

    def _get_calls_in_window(self, token):
        """Count API calls in rolling window."""
        now = time.time()
        redis_client.zremrangebyscore(f"token:{token}:timestamps", 0, now - self.window_seconds)
        return redis_client.zcard(f"token:{token}:timestamps")
    
    def record_call(self, token):
        """Record API call timestamp."""
        now = time.time()
        redis_client.zadd(f"token:{token}:timestamps", {str(now): now})

    def initialize_instances(self):
        """Create 3 live PSNAWP clients."""
        for i, token in enumerate(self.tokens):
            try:
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
                self.record_call(token)
                logger.info(f"Instance {i} initialized with live client")
                log_api_call("client_init", token, None, 200, time.time() - start_time)
            except Exception as e:
                logger.error(f"Error initializing instance {i} ({token}): {e}")
                log_api_call("client_init", token, None, 500, time.time() - start_time, str(e))

    def checkout(self, job_type, timeout : int = 5) -> Optional[TokenInstance]:
        """Get least-loaded healthy instance (blocks up to timeout.)"""
        start = time.time()

        while time.time() - start < timeout:
            instance_scores = {}
            for i in range(3):
                if redis_client.get(f"token_keeper:instance:{i}:token"):
                    if not redis_client.get(f"instance_lock:{i}"):
                        instance_scores[i] = self._get_calls_in_window(redis_client.get(f"token_keeper:instance:{i}:token").decode())

            if instance_scores:
                best_id = min(instance_scores, key=instance_scores.get)
                inst = self.instances[best_id]
                if redis_client.set(f"instance_lock:{inst.instance_id}", 'locked', nx=True, ex=3600):
                    inst.is_busy = True
                    inst.last_health = time.time()
                    logger.debug(f"Checked out instance {inst.instance_id}")
                    return inst
            time.sleep(0.1)
        
        logger.warning("No healthy instances available after timeout")
        return None
    
    def checkin(self, instance_id : int):
        """Release instance, clear lock."""
        if instance_id in self.instances:
            inst = self.instances[instance_id]
            inst.is_busy = False
            redis_client.delete(f"instance_lock:{instance_id}")
            logger.debug(f"Check in instance {instance_id}")

    def _is_healthy(self, inst : TokenInstance) -> bool:
        """Quick health check."""
        try:
            return inst.client is not None
        except:
            return False
        
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