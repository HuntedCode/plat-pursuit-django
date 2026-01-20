"""
Cache utilities - Redis client and caching helpers.

Provides Redis client configuration and utility functions for caching.
"""
import os
import redis
import hashlib
import requests
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

    try:
        APIAuditLog.objects.create(
            token_id=hashlib.sha256(token.encode()).hexdigest()[:64],
            ip_used=(
                requests.get("https://api.ipify.org", timeout=5).text
                if not error_message
                else "unknown"
            ),
            endpoint=endpoint,
            profile_id=validated_profile_id,
            status_code=status_code,
            response_time=response_time,
            error_message=error_message,
            calls_remaining=max(
                0, 300 - int(redis_client.zcard(f"token:{token}:timestamps") or 0)
            ),
        )
    except Exception as e:
        # Log the error but don't crash the application
        # API audit logging is important but shouldn't break core functionality
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Failed to create API audit log: {e}")
