"""
Cache utilities - Redis client and caching helpers.

Provides Redis client configuration and utility functions for caching.
"""
import os
import logging
import time
import redis
import hashlib
import requests
from django.db import IntegrityError
from dotenv import load_dotenv

load_dotenv()


def get_redis_client():
    """
    Get configured Redis client instance.

    Returns a Redis client configured with the URL from environment variables.
    Connection is configured with keepalive and no timeout for long-running operations.

    Returns:
        redis.Redis: Configured Redis client instance
    """
    return redis.Redis.from_url(
        os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        socket_timeout=None,
        socket_keepalive=True
    )


# Global Redis client instance
redis_client = get_redis_client()


#: How long a resolved egress IP is trusted. It identifies the machine PSN sees, not the call, so
#: re-resolving per call bought nothing.
_EGRESS_IP_TTL = 3600
_EGRESS_IP_KEY = 'tokenkeeper:egress_ip'
#: A failed lookup is cached briefly too, so an outage costs one request a minute rather
#: than one per API call.
_EGRESS_IP_FAIL_TTL = 60
#: Survives a Redis outage, which a Redis-only cache by definition does not.
_LOCAL_IP = {'value': None, 'expires': 0.0}


def _machine_id():
    """The same value the TokenKeeper namespaces its Redis keys with (token_keeper.py sets it from
    this env var too). Read per call rather than cached at import so a restart picks up a change."""
    return os.getenv("MACHINE_ID", "default")


def _max_calls():
    """The rate-limit cap, read from the same env var `token_keeper.py` reads (`MAX_CALLS_PER_WINDOW`).
    Hardcoding 300 here meant changing it there made this column lie again."""
    try:
        return int(os.getenv("MAX_CALLS_PER_WINDOW", 300))
    except (TypeError, ValueError):
        return 300


def _window_seconds():
    try:
        return int(os.getenv("WINDOW_SECONDS", 900))
    except (TypeError, ValueError):
        return 900


def _calls_in_window(token):
    """Calls this machine has made on `token` inside the rolling window, from the key the TokenKeeper
    actually writes.

    `zcount` over the window rather than a bare `zcard`: the zset has no TTL and the keeper only prunes
    it in `_get_calls_in_window`, so counting every member counts calls that expired out of the window
    long ago and under-reports what is left. `zcount` is read-only, so this never mutates state the
    keeper owns.

    Never raises: a Redis blip must not cost the audit row, let alone the API call that is logging it.
    """
    try:
        floor = time.time() - _window_seconds()
        return int(redis_client.zcount(f"token:{token}:{_machine_id()}:timestamps", floor, '+inf') or 0)
    except Exception:
        return 0


def _egress_ip():
    """The machine's outbound IP, cached in Redis for an hour.

    THIS USED TO BE A BLOCKING `requests.get("https://api.ipify.org", timeout=5)` ON EVERY SUCCESSFUL
    API CALL. Two problems, the second serious:

      1. One third-party round-trip per PSN call. A whale's initial sync makes thousands.
      2. It sat OUTSIDE the try that guards the audit write, and `log_api_call` is itself called from
         inside `_execute_api_call`'s try. So an ipify outage raised here, landed in that method's
         `except Exception`, which called `log_api_call` again, which raised again -- and the second
         raise escaped into the job worker's broad handler, whose `finally` still marks the job
         complete. An outage at a third-party IP-echo service made every sync fail silently while
         reporting success.

    Now cached, and it swallows its own failures: an unknown IP is a worse audit row, not a lost sync.
    """
    now = time.time()
    # PROCESS-LOCAL FIRST, and this is not an optimisation. With Redis down, a Redis-only cache never
    # populates, so every single API call fell through to the blocking request again -- the original
    # bug, minus the raise. The local cache holds even when Redis does not.
    if _LOCAL_IP['value'] and now < _LOCAL_IP['expires']:
        return _LOCAL_IP['value']

    try:
        cached = redis_client.get(_EGRESS_IP_KEY)
        if cached:
            ip = cached.decode() if isinstance(cached, bytes) else str(cached)
            _LOCAL_IP.update(value=ip, expires=now + _EGRESS_IP_TTL)
            return ip
    except Exception:
        pass

    try:
        response = requests.get("https://api.ipify.org", timeout=5)
        response.raise_for_status()
        ip = response.text.strip()[:45]
    except Exception:
        # CACHE THE FAILURE, briefly. Returning "unknown" without caching meant every call retried
        # with a 5s timeout while ipify was down: the sync stopped failing and became unusable
        # instead, which is not an improvement.
        _LOCAL_IP.update(value='unknown', expires=now + _EGRESS_IP_FAIL_TTL)
        return 'unknown'

    _LOCAL_IP.update(value=ip, expires=now + _EGRESS_IP_TTL)
    try:
        redis_client.set(_EGRESS_IP_KEY, ip, ex=_EGRESS_IP_TTL)
    except Exception:
        pass
    return ip


def log_api_call(endpoint, token, profile_id, status_code, response_time, error_message=""):
    """
    Log API call details to APIAuditLog for monitoring and rate limiting.

    This function records details about PSN API calls including timing,
    status, errors, and remaining rate limit capacity.

    Args:
        endpoint: API endpoint that was called
        token: Authentication token used (will be hashed)
        profile_id: PSN profile ID involved in the call (None if profile doesn't exist)
        status_code: HTTP status code from response
        response_time: Request duration in milliseconds
        error_message: Error message if call failed (default: "")
    """
    from trophies.models import APIAuditLog, Profile

    logger = logging.getLogger(__name__)

    # NOTHING IN HERE MAY RAISE. This runs from inside `_execute_api_call`'s try, on the SUCCESS path,
    # so anything that escapes turns a completed PSN call into an exception -- which lands in that
    # method's `except Exception`, which calls this function again, which raises again. The second
    # raise escapes into the job worker's broad handler, whose `finally` still marks the job complete.
    # That is how a failure here becomes a silently-failed sync that reports success. The audit-log
    # write below was already guarded; the profile lookup and the token hash were not, and an
    # `OperationalError` from a recycled connection or an exhausted pool is entirely ordinary.
    validated_profile_id = None
    try:
        if profile_id is not None and Profile.objects.filter(id=profile_id).exists():
            validated_profile_id = profile_id
    except Exception:
        logger.warning("log_api_call: could not validate profile_id %s", profile_id)

    try:
        token_id = hashlib.sha256(token.encode()).hexdigest()[:64]
    except Exception:
        token_id = ''      # a None/bytes token must not cost the call that is being logged

    log_kwargs = dict(
        token_id=token_id,
        ip_used=_egress_ip(),
        endpoint=endpoint,
        profile_id=validated_profile_id,
        status_code=status_code,
        response_time=response_time,
        # Namespaced by MACHINE_ID, because that is what writes it. The TokenKeeper records call
        # timestamps under `token:{token}:{machine_id}:timestamps` (and enforces the window off that
        # same key, so throttling has always been correct) -- while this read omitted the machine
        # component. Nothing writes the machine-less key, so zcard returned 0 every time and EVERY row
        # ever written recorded the full 300. The column that tells you how close a token is running
        # was a constant. The cap comes from the same env var the keeper reads, so changing it there
        # does not silently make this lie again.
        calls_remaining=max(0, _max_calls() - _calls_in_window(token)),
    )

    try:
        APIAuditLog.objects.create(**log_kwargs)
    except IntegrityError:
        # Race condition: profile was deleted between exists() check and create()
        # Retry without the profile FK to preserve the audit log entry
        log_kwargs['profile_id'] = None
        try:
            APIAuditLog.objects.create(**log_kwargs)
        except Exception as e:
            logger.error(f"Failed to create API audit log: {e}")
    except Exception as e:
        logger.error(f"Failed to create API audit log: {e}")
