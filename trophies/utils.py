import difflib
import os
import redis
import hashlib
import requests
import logging
import time
from django.db import transaction
from django.db.models import Exists, OuterRef, Q, Count
from django.db.models.query import QuerySet
from django.conf import settings
from datetime import timedelta
from dotenv import load_dotenv
from typing import List, Set, Dict
from scipy import stats


load_dotenv()
logger = logging.getLogger("psn_api")
redis_client = redis.Redis.from_url(
    os.getenv("REDIS_URL", "redis://localhost:6379/0"),
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

def check_profile_badges(profile, profilegame_ids):
    from trophies.models import ProfileGame, Badge

    start_time = time.time()

    pg_qs: QuerySet[ProfileGame] = ProfileGame.objects.filter(id__in=profilegame_ids, profile=profile).select_related('game__concept').prefetch_related('game__concept__badges')

    if not pg_qs.exists():
        logger.info(f"No ProfileGames found for profile {profile.psn_username}")
        return 0
    
    concept_ids = pg_qs.values_list('game__concept_id', flat=True).filter(game__concept__isnull=False).distinct()

    base_badges_qs = Badge.objects.filter(concepts__id__in=concept_ids, base_badge__isnull=True).distinct()

    derived_badges_qs = Badge.objects.filter(base_badge__in=base_badges_qs).distinct()

    unique_badges_qs = base_badges_qs | derived_badges_qs
    unique_badges_qs = unique_badges_qs.prefetch_related('concepts', 'base_badge')

    checked_count = 0
    for badge in unique_badges_qs:
        try:
            process_badge(profile, badge)
            checked_count += 1
        except Exception as e:
            logger.error(f"Error checking badge {badge.id} for profile {profile.psn_username}: {e}")
    
    duration = time.time() - start_time
    logger.info(f"Checked {checked_count} unique badges for profile {profile.psn_username} in {duration:.2f}s")
    return checked_count

def get_badge_metrics(profile, badge):
    """Computes the number of qualifying Concepts achieved by the user and required value for a series badge. Returns 0 for misc badges or invalid configs."""
    from trophies.models import Game, EarnedTrophy, ProfileGame, Badge

    if badge.badge_type == 'series':
        platform_filter = get_platform_filter(badge)    
        is_complete_required = badge.tier in [2, 4]
        is_obtainable_required = badge.tier in [1, 2]

        qualifying_games_filter = Q(platform_filter, defined_trophies__platinum__gt=0)
        if is_obtainable_required:
            qualifying_games_filter &= Q(is_obtainable=True)
            qualifying_games_filter &= Q(is_delisted=False)
        
        concepts_qs = badge.concepts if badge.concepts.exists() else (badge.base_badge.concepts if badge.base_badge else Badge.objects.none())
        if not concepts_qs.exists():
            return {'achieved': 0, 'required': 0}
        
        filtered_concepts_qs = concepts_qs.filter(Exists(Game.objects.filter(qualifying_games_filter, concept=OuterRef('pk')))).distinct()

        required = filtered_concepts_qs.count()
        if required == 0:
            return {'achieved': 0, 'required': 0}
        
        achieved = 0
        if is_complete_required:
            user_achievements_qs = ProfileGame.objects.filter(profile=profile, progress=100, game__concept__in=filtered_concepts_qs, game__defined_trophies__platinum__gt=0)
            if is_obtainable_required:
                user_achievements_qs = user_achievements_qs.filter(game__is_obtainable=True, game__is_delisted=False)
            achieved = user_achievements_qs.values('game__concept').distinct().count()
        else:
            user_achievements_qs = EarnedTrophy.objects.filter(profile=profile, earned=True, trophy__trophy_type='platinum', trophy__game__concept__in=filtered_concepts_qs, trophy__game__defined_trophies__platinum__gt=0)
            if is_obtainable_required:
                user_achievements_qs = user_achievements_qs.filter(trophy__game__is_obtainable=True, trophy__game__is_delisted=False)
            achieved = user_achievements_qs.values('trophy__game__concept').distinct().count()

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

@transaction.atomic
def process_badge(profile, badge, notify_bot=False):
    """Handles progress update and earning check for a single badge."""
    from trophies.models import UserBadge, UserBadgeProgress

    if badge.badge_type == 'misc':
        # Placeholder - implement later
        return
    
    metrics = get_badge_metrics(profile, badge)
    achieved = metrics['achieved']
    required = metrics['required']

    progress_fields = {}
    if badge.badge_type == 'series':
        progress_fields = {'completed_concepts': achieved, 'required_concepts': required}
    elif badge.badge_type == 'misc':
        progress_fields = {'progress_value': achieved, 'required_value': required}
    
    progress, created = UserBadgeProgress.objects.get_or_create(profile=profile, badge=badge, defaults=progress_fields)
    if not created:
        for field, value in progress_fields.items():
            setattr(progress, field, value)
        if progress_fields:
            progress.save(update_fields=list(progress_fields.keys()))
    
    needed = required if badge.requires_all else max(badge.min_required, 1)
    user_badge_exists = UserBadge.objects.filter(profile=profile, badge=badge).exists()

    newly_awarded = False
    if achieved >= needed and needed > 0 and not user_badge_exists:
        UserBadge.objects.create(profile=profile, badge=badge)
        logger.info(f"Awarded badge {badge.effective_display_title} (tier: {badge.tier}) to {profile.display_psn_username}")
        newly_awarded = True
    elif achieved < needed and user_badge_exists:
        UserBadge.objects.filter(profile=profile, badge=badge).delete()
        logger.info(f"Revoked badge {badge.effective_display_title} (tier: {badge.tier}) from {profile.display_psn_username}")

    if badge.discord_role_id and (newly_awarded or notify_bot):
        if profile.is_discord_verified and profile.discord_id:
            notify_bot_badge_earned(profile, badge)

    return newly_awarded
    
def notify_new_badge(profile, badge):
    """Send Discord webhook embed for new badge."""
    try:
        platinum_emoji = f"<:Platinum_Trophy:{PLATINUM_EMOJI_ID}>" if PLATINUM_EMOJI_ID else "🏆"
        plat_pursuit_emoji = f"<:PlatPursuit:{PLAT_PURSUIT_EMOJI_ID}>" if PLAT_PURSUIT_EMOJI_ID else "🏆"

        thumbnail_url = ''
        if badge.badge_image or badge.base_badge:
            if settings.DEBUG:
                thumbnail_url = 'https://psnobj.prod.dl.playstation.net/psnobj/NPWR20813_00/19515081-883c-41e2-9c49-8a8706c59efc.png'
            else:
                if badge.badge_image:
                    thumbnail_url = badge.badge_image.url
                else:
                    thumbnail_url = badge.base_badge.badge_image.url

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

def send_batch_role_notification(profile, badges):
    """
    Sends a single Discord embed listing ONLY the badges that grant a Discord role.
    Uses the first such badge's image as thumbnail.
    """
    if not badges:
        return

    platinum_emoji = f"<:Platinum_Trophy:{PLATINUM_EMOJI_ID}>" if PLATINUM_EMOJI_ID else "🏆"
    plat_pursuit_emoji = f"<:PlatPursuit:{PLAT_PURSUIT_EMOJI_ID}>" if PLAT_PURSUIT_EMOJI_ID else "🏆"

    role_badges = [b for b in badges if b.discord_role_id]

    if not role_badges:
        logger.info(f"No role-granting badges for {profile.psn_username} — skipping notification")
        return

    first_badge = role_badges[0]
    thumbnail_url = None
    if settings.DEBUG:
        thumbnail_url = 'https://psnobj.prod.dl.playstation.net/psnobj/NPWR20813_00/19515081-883c-41e2-9c49-8a8706c59efc.png'
    else:
        if first_badge.badge_image:
            thumbnail_url = first_badge.badge_image.url
        elif first_badge.base_badge and first_badge.base_badge.badge_image:
            thumbnail_url = first_badge.base_badge.badge_image.url

    badge_lines = []
    for badge in role_badges:
        badge_lines.append(f"{platinum_emoji} **{badge.name}** <@&{badge.discord_role_id}>")

    description = (
        f"{plat_pursuit_emoji} <@{profile.discord_id}> — here are the Discord roles you've earned on Plat Pursuit!\n\n"
        + "\n".join(badge_lines)
        + "\n\nThank you for being part of the community! 🎉"
    )

    embed_data = {
        'title': f"🎖️ Your Plat Pursuit Discord Roles ({len(role_badges)} total)",
        'description': description,
        'color': 0x674EA7,
        'footer': {'text': 'Powered by Plat Pursuit | No Trophy Can Hide From Us'},
    }
    if thumbnail_url:
        embed_data['thumbnail'] = {'url': thumbnail_url}

    payload = {'embeds': [embed_data]}
    try:
        response = requests.post(DISCORD_PLATINUM_WEBHOOK_URL, json=payload)
        response.raise_for_status()
        logger.info(f"Sent batch role notification for {len(role_badges)} role-granting badges to {profile.psn_username}")
    except requests.RequestException as e:
        logger.error(f"Batch role webhook notification failed: {e}")

@transaction.atomic
def check_discord_role_badges(profile):
    from trophies.models import Badge
    start_time = time.time()

    logger.info(f"verified/discord_id: {profile.is_discord_verified} | {profile.discord_id}")

    discord_badges = Badge.objects.filter(discord_role_id__isnull=False).prefetch_related('concepts', 'concepts__games', 'base_badge')

    if not discord_badges.exists():
        logger.info(f"No badges with discord_role_id found. Skipping for profile {profile.psn_username}")
        return 0
    
    discord_badge_ids = set(discord_badges.values_list('id', flat=True))
    derived_badges = Badge.objects.filter(base_badge__id__in=discord_badge_ids).prefetch_related('concepts', 'base_badge')

    badges_to_process = set(discord_badges) | set(derived_badges)
    role_granting_badges = []

    checked_count = 0
    for badge in badges_to_process:
        try:
            was_newly_awarded = process_badge(profile, badge, notify_bot=True)

            metrics = get_badge_metrics(profile, badge)
            achieved = metrics['achieved']
            required = metrics['required']
            needed = required if badge.requires_all else max(badge.min_required, 1)

            qualifies = achieved >= needed and needed > 0
            logger.info(f"Badge {badge.name} (ID {badge.id}): achieved={achieved}, needed={needed}, qualifies={qualifies}, role_id={badge.discord_role_id}")

            if qualifies:
                role_granting_badges.append(badge)

            checked_count += 1
        except Exception as e:
            logger.error(f"Error processing Discord role badge {badge.id} for profile {profile.psn_username}: {e}")
    
    logger.info(f"Found {len(role_granting_badges)} qualifying role-granting badges")

    if role_granting_badges and profile.is_discord_verified and profile.discord_id:
        send_batch_role_notification(profile, role_granting_badges)
    else:
        logger.info("No notification sent: missing verification, discord_id, or qualifying badges")

    duration = time.time() - start_time
    logger.info(
        f"Processed {checked_count} Discord role related badges "
        f"(direct: {len(discord_badges)}, derived: {len(derived_badges)}) "
        f"for profile {profile.psn_username} in {duration:.2f}s"
    )
    return checked_count

def get_next_sync(profile) -> int:
    # Update logic later
    next_sync = profile.last_synced + timedelta(hours=1)
    return next_sync

def update_profile_games(profile):
    from trophies.models import ProfileGame
    profile.total_games = ProfileGame.objects.filter(profile=profile).count()
    profile.total_completes = ProfileGame.objects.filter(profile=profile, progress=100).count()
    profile.avg_progress = profile.get_average_progress()
    profile.save(update_fields=['total_games', 'total_completes', 'avg_progress'])

def update_profile_trophy_counts(profile):
    from trophies.models import EarnedTrophy
    profile.total_trophies = EarnedTrophy.objects.filter(profile=profile, earned=True).count()
    profile.total_unearned = EarnedTrophy.objects.filter(profile=profile, earned=False).count()
    profile.total_plats = EarnedTrophy.objects.filter(profile=profile, earned=True, trophy__trophy_type='platinum').count()
    profile.save(update_fields=['total_trophies', 'total_unearned', 'total_plats'])

def detect_asian_language(title: str) -> str:
    """Detect the primary Asian language in a game title."""
    def count_chinese(text):
        return sum(1 for c in text if '\u4e00' <= c <= '\u9fff') # Han (chinese primary)
    
    def count_japanese_unique(text):
        hiragana = sum(1 for c in text if '\u3040' <= c <= '\u309f')
        katakana = sum(1 for c in text if '\u30a0' <= c <= '\u30ff')
        return hiragana + katakana
    
    def count_korean(text):
        return sum(1 for c in text if '\uac00' <= c <= '\ud7af') # Hangul
    
    japanese_unique = count_japanese_unique(title)
    korean = count_korean(title)
    chinese = count_chinese(title) - japanese_unique

    max_count = max(chinese, japanese_unique, korean)
    if max_count == 0:
        return 'Unknown'
    elif japanese_unique == max_count:
        return 'JP'
    elif korean == max_count:
        return 'KR'
    elif chinese == max_count:
        return 'CN'

MODERN_PLATFORMS = ['PS5', 'PS4']
ALL_PLATFORMS = MODERN_PLATFORMS + ['PS3', 'PSVITA', 'PSVR']

TITLE_ID_BLACKLIST = ['CUSA05214_00', 'CUSA01015_00', 'CUSA00129_00', 'CUSA00131_00', 'CUSA05365_00', 'PPSA01650_00', 'PPSA02038_00', 'PPSA01614_00', 'PPSA01604_00', 'PPSA01665_00',]
TITLE_STATS_SUPPORTED_PLATFORMS = MODERN_PLATFORMS
PREFERRED_MEDIA_PLATFORMS = ['PS5']

SEARCH_ACCOUNT_IDS = ['7532533859249281768']

NA_REGION_CODES = ['IP', 'UB', 'UP', 'US', 'UT']
EU_REGION_CODES = ['EB', 'EP']
JP_REGION_CODES = ['JA', 'JB', 'JP', 'KP']
AS_REGION_CODES = ['HA,' 'HB', 'HP', 'HT']
KR_REGION_CODES = ['KR']
CN_REGION_CODES = ['CN']
REGIONS = ['NA', 'EU', 'JP', 'AS', 'KR', 'CN']

SHOVELWARE_THRESHOLD = 90.0

DISCORD_PLATINUM_WEBHOOK_URL = os.getenv('DISCORD_PLATINUM_WEBHOOK_URL')
PLATINUM_EMOJI_ID = os.getenv('PLATINUM_EMOJI_ID')
PLAT_PURSUIT_EMOJI_ID = os.getenv('PLAT_PURSUIT_EMOJI_ID')