"""
Cache utilities - Redis client and caching helpers.

Provides Redis client configuration and utility functions for caching.
"""
import os
import logging
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


def _machine_id():
    """The same value the TokenKeeper namespaces its Redis keys with (token_keeper.py sets it from
    this env var too). Read per call rather than cached at import so a restart picks up a change."""
    return os.getenv("MACHINE_ID", "default")


def _calls_in_window(token):
    """Calls this machine has made on `token` inside the rolling window, from the key the TokenKeeper
    actually writes. Never raises: a Redis blip must not cost the audit row (or the API call that is
    logging it)."""
    try:
        return int(redis_client.zcard(f"token:{token}:{_machine_id()}:timestamps") or 0)
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
    try:
        cached = redis_client.get(_EGRESS_IP_KEY)
        if cached:
            return cached.decode() if isinstance(cached, bytes) else str(cached)
    except Exception:
        pass

    try:
        ip = requests.get("https://api.ipify.org", timeout=5).text.strip()[:45]
    except Exception:
        return "unknown"

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

    # Validate profile_id exists in database before creating the log entry
    # This prevents foreign key constraint violations
    validated_profile_id = None
    if profile_id is not None:
        if Profile.objects.filter(id=profile_id).exists():
            validated_profile_id = profile_id

    logger = logging.getLogger(__name__)
    log_kwargs = dict(
        token_id=hashlib.sha256(token.encode()).hexdigest()[:64],
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
        # was a constant.
        calls_remaining=max(0, 300 - _calls_in_window(token)),
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
