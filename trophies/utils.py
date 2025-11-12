import difflib
import os
import redis
import hashlib
import requests
import logging
from dotenv import load_dotenv
from typing import List, Set


load_dotenv()
logger = logging.getLogger("psn_api")
redis_client = redis.Redis.from_url(
    os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0"),
    socket_timeout=None,
    socket_keepalive=True
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

def count_unique_game_groups(games_qs) -> int:
    games = list(games_qs)
    n = len(games)
    if n == 0:
        return 0
    
    parent = list(range(n))
    rank = [0] * n

    def find(x: int) -> int:
        if parent[x] != x:
            parent[x] = find(parent[x])
        return parent[x]
    
    def union(x: int, y: int):
        px, py = find(x), find(y)
        if px == py:
            return
        if rank[px] < rank[py]:
            parent[px] = py
        elif rank[py] > rank[px]:
            parent[py] = px
        else:
            parent[py] = px
            rank[px] += 1
    
    title_id_sets: List[Set[str]] = [set(game.title_ids) for game in games]
    for i in range(n):
        for j in range(i + 1, n):
            if title_id_sets[i] & title_id_sets[j]:
                union(i, j)
    
    unique_groups = len(set(find(i) for i in range(n)))
    return unique_groups


# Common PS Apps - No Trophies
TITLE_ID_BLACKLIST = ['CUSA05214_00', 'CUSA01015_00', 'CUSA00129_00', 'CUSA00131_00', 'CUSA05365_00', 'PPSA01650_00', 'PPSA02038_00', 'PPSA01614_00', 'PPSA01604_00', 'PPSA01665_00',]
TITLE_STATS_SUPPORTED_PLATFORMS = ['PS4', 'PS5']

SEARCH_ACCOUNT_IDS = ['7532533859249281768']