import difflib
import os
import redis
import hashlib
import requests
import logging
from django.db.models import Exists, OuterRef, Q
from django.conf import settings
from django.utils import timezone
from datetime import timedelta
from dotenv import load_dotenv
from typing import List, Set
from scipy import stats


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

def calculate_trimmed_mean(data, trim_percent=0.1):
    """Calculate trimmed mean to handle outliers."""
    if not data:
        return None
    return stats.trim_mean(data, trim_percent)

def get_badge_metrics(profile, badge):
    """Computes the number of qualifying Concepts achieved by the user and required value for a series badge. Returns 0 for misc badges or invalid configs."""
    from trophies.models import Game, EarnedTrophy, ProfileGame

    if badge.badge_type == 'series':
    
        platform_filter = get_platform_filter(badge)    
        is_complete_required = badge.tier in [2, 4]
        is_obtainable_required = badge.tier in [1, 2]

        if is_obtainable_required:
            qualifying_games_qs = Game.objects.filter(platform_filter, is_obtainable=True, defined_trophies__platinum__gt=0).prefetch_related('concept')
        else:
            qualifying_games_qs = Game.objects.filter(platform_filter, defined_trophies__platinum__gt=0).prefetch_related('concept')
        if badge.concepts.count() > 0:
            filtered_concepts_qs = badge.concepts.filter(Exists(qualifying_games_qs.filter(concept=OuterRef('pk')))).distinct()
        elif badge.base_badge and badge.base_badge.concepts.count() > 0:
            filtered_concepts_qs = badge.base_badge.concepts.filter(Exists(qualifying_games_qs.filter(concept=OuterRef('pk')))).distinct()

        required = filtered_concepts_qs.count()
        if required == 0:
            return {'achieved': 0, 'required': 0}
        
        achieved = 0
        for concept in filtered_concepts_qs:
            qual_games = [g for g in qualifying_games_qs if g.concept == concept]
            if not qual_games:
                continue
            
            if is_complete_required:
                    if ProfileGame.objects.filter(profile=profile, game__in=qual_games, progress=100).exists():
                        achieved += 1
            else:
                if EarnedTrophy.objects.filter(profile=profile, trophy__game__in=qual_games, trophy__trophy_type='platinum', earned=True).exists():
                    achieved += 1
        return {'achieved': achieved, 'required': required}
   
    elif badge.badge_type == 'misc':
        # Placeholder - update logic later.
        achieved = 0
        required = badge.requirements.get('count', 0)
        return {'achieved': achieved, 'required': required}
    
    return {'achieved': 0, 'required': 0}
        

def get_platform_filter(badge):
    allowed_platforms = MODERN_PLATFORMS if badge.tier in [1, 2] else ALL_PLATFORMS
    platform_filter = Q()
    for plat in allowed_platforms:
        platform_filter |= Q(title_platform__contains=plat)
    return platform_filter

def process_badge(profile, badge):
    """Handles progress update and earning check for a single badge."""
    from trophies.models import UserBadge, UserBadgeProgress

    if badge.badge_type == 'misc':
        # Placeholder - implement later
        return
    
    metrics = get_badge_metrics(profile, badge)
    achieved = metrics['achieved']
    required = metrics['required']

    progress, _ = UserBadgeProgress.objects.get_or_create(
        profile=profile,
        badge=badge,
        defaults={
            'completed_concepts': achieved if badge.badge_type == 'series' else 0,
            'required_concepts': required if badge.badge_type == 'series' else 0,
            'progress_value': achieved if badge.badge_type == 'misc' else 0,
            'required_value': required if badge.badge_type == 'misc' else 0,
        }    
    )
    if badge.badge_type == 'series':
        progress.completed_concepts = achieved
        progress.required_concepts = required
    elif badge.badge_type == 'misc':
        progress.progress_value = achieved
        progress.required_value = required
    progress.save(update_fields=['completed_concepts', 'required_concepts', 'progress_value', 'required_value', 'last_checked'])

    needed = required if badge.requires_all else max(badge.min_required, 1)
    if achieved >= needed and needed > 0 and not UserBadge.objects.filter(profile=profile, badge=badge).exists():
        UserBadge.objects.create(profile=profile, badge=badge)
        return True
    return False

def notify_new_badge(profile, badge):
    """Send Discord webhook embed for new badge."""
    try:
        platinum_emoji = f"<:Platinum_Trophy:{PLATINUM_EMOJI_ID}>" if PLATINUM_EMOJI_ID else "🏆"
        plat_pursuit_emoji = f"<:PlatPursuit:{PLAT_PURSUIT_EMOJI_ID}>" if PLAT_PURSUIT_EMOJI_ID else "🏆"

        thumbnail_url = ''
        if badge.icon or badge.base_badge:
            if settings.DEBUG:
                thumbnail_url = 'https://psnobj.prod.dl.playstation.net/psnobj/NPWR20813_00/19515081-883c-41e2-9c49-8a8706c59efc.png'
            else:
                if badge.icon:
                    thumbnail_url = f"{settings.SITE_URL.rstrip('/')}{badge.icon.url}"
                else:
                    thumbnail_url = f"{settings.SITE_URL.rstrip('/')}{badge.base_badge.icon.url}"

        description = f"{plat_pursuit_emoji} <@{profile.discord_id}> has earned a brand new badge!\n{platinum_emoji} **{badge.name}**"
        if badge.discord_role_id:
            description += f"\nYou've earned the <@&{badge.discord_role_id}> role! Congrats! 🎉"

        embed_data = {
            'title': f"🚨 New Badge for {profile.display_psn_username}! 🚨",
            'description': description,
            'color': 0x674EA7,
            'thumbnail': {'url': thumbnail_url},
            'footer': {'text': f"Powered by Plat Pursuit | No Trophy Can Hide From Us"},
        }
        payload = {'embeds': [embed_data]}
        response = requests.post(DISCORD_PLATINUM_WEBHOOK_URL, json=payload)
        response.raise_for_status()
        logger.info(f"Sent notification of new badge for {profile.psn_username}")
        if badge.discord_role_id:
            notify_bot_badge_earned(profile, badge)
    except requests.RequestException as e:
        logger.error(f"Webhook notification failed: {e}")

def notify_bot_badge_earned(profile, badge):
    """Notify Discord bot via API to assign role."""
    try:
        url = settings.BOT_API_URL + "/assign-role"
        headers = {
            'Authorization': f"Bearer {settings.BOT_API_KEY}",
            'Content-Type': 'application/json'
        }
        data = {
            'user_id': profile.discord_id,
            'role_id': badge.discord_role_id,
        }
        response = requests.post(url, json=data, headers=headers)
        response.raise_for_status()
        logger.info(f"Bot notified: Assigned role {badge.discord_role_id} to {profile.discord_id} for badge {badge.name}")
    except requests.RequestException as e:
        logger.error(f"Bot notification failed for badge {badge.name} (user {profile.psn_username}): {e}")

def get_next_sync(profile) -> int:
    # Update logic later
    next_sync = profile.last_synced + timedelta(hours=1)
    return next_sync

# Common PS Apps - No Trophies
MODERN_PLATFORMS = ['PS4', 'PS5']
ALL_PLATFORMS = MODERN_PLATFORMS + ['PS3', 'PSVITA', 'PSVR']

TITLE_ID_BLACKLIST = ['CUSA05214_00', 'CUSA01015_00', 'CUSA00129_00', 'CUSA00131_00', 'CUSA05365_00', 'PPSA01650_00', 'PPSA02038_00', 'PPSA01614_00', 'PPSA01604_00', 'PPSA01665_00',]
TITLE_STATS_SUPPORTED_PLATFORMS = MODERN_PLATFORMS
PREFERRED_MEDIA_PLATFORMS = ['PS5']

SEARCH_ACCOUNT_IDS = ['7532533859249281768']

NA_REGION_CODES = ['IP', 'UB', 'UP', 'US', 'UT']
EU_REGION_CODES = ['EB', 'EP']
JP_REGION_CODES = ['JA', 'JB', 'JP', 'KP']
AS_REGION_CODES = ['HA,' 'HB', 'HP', 'HT']

SHOVELWARE_THRESHOLD = 90.0

DISCORD_PLATINUM_WEBHOOK_URL = os.getenv('DISCORD_PLATINUM_WEBHOOK_URL')
PLATINUM_EMOJI_ID = os.getenv('PLATINUM_EMOJI_ID')
PLAT_PURSUIT_EMOJI_ID = os.getenv('PLAT_PURSUIT_EMOJI_ID')