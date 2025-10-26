import os
import redis
import hashlib
import requests
import logging
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("psn_api")
redis_client = redis.Redis.from_url(
    os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0"),
    socket_timeout=5,
    socket_connect_timeout=5,
    retry_on_timeout=True
)

def log_api_call(
    endpoint, token, profile_id, status_code, response_time, error_message=""
):
    """Log API call to APIAuditLog."""
    from .models import APIAuditLog

    APIAuditLog.objects.create(
        token_id=hashlib.sha256(token.encode()).hexdigest()[:64],
        ip_used=(
            requests.get("https://api.ipify.org", timeout=5).text
            if not error_message
            else "unknown"
        ),
        endpoint=endpoint,
        profile_id=profile_id,
        status_code=status_code,
        response_time=response_time,
        error_message=error_message,
        calls_remaining=max(
            0, 300 - int(redis_client.zcard(f"token:{token}:timestamps") or 0)
        ),
    )