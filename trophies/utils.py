import difflib
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

def match_names(name1, name2, threshold=0.9):
    """Fuzzy match game names with normalization."""
    name1 = name1.lower().replace('™', '').replace('®', '').strip()
    name2 = name2.lower().replace('™', '').replace('®', '').strip()
    ratio = difflib.SequenceMatcher(None, name1, name2).ratio()
    return ratio >= threshold


# Common PS Apps - No Trophies
TITLE_ID_BLACKLIST = ['CUSA05214_00', 'CUSA01015_00', 'CUSA00129_00', 'CUSA00131_00', 'CUSA05365_00', 'PPSA01650_00', 'PPSA02038_00', 'PPSA01614_00', 'PPSA01604_00', 'PPSA01665_00',]