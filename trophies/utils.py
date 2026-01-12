import difflib
import os
import redis
import hashlib
import requests
import logging
import time
from django.db import transaction
from django.db.models import Window, Q, Max, F, Count, Sum, When, Value, IntegerField, Case, OuterRef, Exists, Subquery
from django.db.models.functions import RowNumber, Coalesce
from django.db.models.query import QuerySet
from django.conf import settings
from django.utils import timezone
from dotenv import load_dotenv
from typing import List, Set
from scipy import stats
from trophies.discord_utils.discord_notifications import send_batch_role_notification, notify_new_badge

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

# Badges

def check_profile_badges(profile, profilegame_ids, skip_notis: bool = False):
    from trophies.models import ProfileGame, Badge, Stage

    start_time = time.time()

    pg_qs: QuerySet[ProfileGame] = ProfileGame.objects.filter(id__in=profilegame_ids, profile=profile).select_related('game__concept').prefetch_related('game__concept__badges')

    if not pg_qs.exists():
        logger.info(f"No ProfileGames found for profile {profile.psn_username}")
        return 0
    
    concept_ids = pg_qs.values_list('game__concept_id', flat=True).filter(game__concept__isnull=False).distinct()

    stages = Stage.objects.filter(concepts__id__in=concept_ids).distinct()
    series_slugs = stages.values_list('series_slug', flat=True).distinct()
    badges = Badge.objects.filter(series_slug__in=series_slugs).distinct().order_by('tier')

    checked_count = 0
    for badge in badges:
        try:
            handle_badge(profile, badge, add_role_only=skip_notis)
            checked_count += 1
        except Exception as e:
            logger.error(f"Error checking badge {badge.id} for profile {profile.psn_username}: {e}")
    
    duration = time.time() - start_time
    logger.info(f"Checked {checked_count} unique badges for profile {profile.psn_username} in {duration:.2f}s")
    return checked_count

@transaction.atomic
def handle_badge(profile, badge, add_role_only=False):
    from trophies.models import UserBadge, UserBadgeProgress, Badge
    if not profile or not badge:
        return
    
    if badge.tier > 1:
        prev_tier = badge.tier - 1
        prev_badge = Badge.objects.filter(series_slug=badge.series_slug, tier=prev_tier).first()
        if prev_badge and not UserBadge.objects.filter(profile=profile, badge=prev_badge).exists():
            logger.info(f"Skipped {badge.name} for {profile.psn_username} - previous tier {prev_tier} not earned.")
            return

    if badge.badge_type in ['series', 'collection']:
        stage_completion_dict = badge.get_stage_completion(profile)
        print(stage_completion_dict)
        badge_earned = True
        completed_count = 0
        for stage, is_complete in stage_completion_dict.items():
            if stage == 0:
                continue
            elif not is_complete:
                badge_earned = False
                continue
            completed_count += 1

        progress, created = UserBadgeProgress.objects.get_or_create(profile=profile, badge=badge, defaults={'completed_concepts': completed_count})
        if not created:
            progress.completed_concepts = completed_count
            progress.last_checked = timezone.now()
            progress.save(update_fields=['completed_concepts', 'last_checked'])

        user_badge_exists = False
        badge_created = False
        if badge_earned:
            user_badge_exists = UserBadge.objects.filter(profile=profile, badge=badge).exists()
            if badge_earned and not user_badge_exists:
                UserBadge.objects.create(profile=profile, badge=badge)
                badge_created = True
                logger.info(f"Awarded badge {badge.effective_display_title} (tier: {badge.tier}) to {profile.display_psn_username}")
        elif not badge_earned and user_badge_exists:
            UserBadge.objects.filter(profile=profile, badge=badge).delete()
            logger.info(f"Revoked badge {badge.effective_display_title} (tier: {badge.tier}) from {profile.display_psn_username}")

        if badge_earned and badge.discord_role_id:
            if profile.is_discord_verified and profile.discord_id:
                notify_bot_role_earned(profile, badge.discord_role_id)
        
        if not add_role_only and badge_created and badge.discord_role_id:
            if profile.is_discord_verified and profile.discord_id:
                notify_new_badge(profile, badge)

        return badge_created

def notify_bot_role_earned(profile, role_id):
    """Notify Discord bot via API to assign role."""
    try:
        url = settings.BOT_API_URL + "/assign-role"
        headers = {
            'Authorization': f"Bearer {settings.BOT_API_KEY}",
            'Content-Type': 'application/json'
        }
        data = {
            'user_id': profile.discord_id,
            'role_id': role_id,
        }
        response = requests.post(url, json=data, headers=headers)
        response.raise_for_status()
        logger.info(f"Bot notified: Assigned role {role_id} to {profile.discord_id}.")
    except requests.RequestException as e:
        logger.error(f"Bot notification failed for role {role_id} (user {profile.psn_username}): {e}")

@transaction.atomic
def initial_badge_check(profile, discord_notify: bool = True):
    from trophies.models import ProfileGame, Badge, Stage

    start_time = time.time()

    pg_qs: QuerySet[ProfileGame] = ProfileGame.objects.filter(profile=profile).select_related('game__concept').prefetch_related('game__concept__badges')

    if not pg_qs.exists():
        logger.info(f"No ProfileGames found for profile {profile.psn_username}")
        return 0
    
    concept_ids = pg_qs.values_list('game__concept_id', flat=True).filter(game__concept__isnull=False).distinct()

    stages = Stage.objects.filter(concepts__id__in=concept_ids).distinct()
    series_slugs = stages.values_list('series_slug', flat=True).distinct()
    badges = Badge.objects.filter(series_slug__in=series_slugs).distinct().order_by('tier')

    role_granting_badges = []
    checked_count = 0
    for badge in badges:
        try:
            created = handle_badge(profile, badge, add_role_only=True)
            checked_count += 1
            if created and badge.discord_role_id:
                role_granting_badges.append(badge)
        except Exception as e:
            logger.error(f"Error checking badge {badge.id} for profile {profile.psn_username}: {e}")
    
    logger.info(f"Found {len(role_granting_badges)} qualifying role-granting badges")

    if discord_notify:
        if role_granting_badges and profile.is_discord_verified and profile.discord_id:
            send_batch_role_notification(profile, role_granting_badges)
        else:
            logger.info("No notification sent: missing verification, discord_id, or qualifying badges")

    duration = time.time() - start_time
    logger.info(f"Checked {checked_count} unique badges for profile {profile.psn_username} in {duration:.2f}s")
    return checked_count

# Leaderboards

def compute_earners_leaderboard(series_slug: str) -> list[dict]:
    """Compute earners sorted by earn date."""
    from trophies.models import UserBadge

    earners = UserBadge.objects.filter(
        badge__series_slug=series_slug,
        profile__is_linked=True
    ).select_related('profile', 'badge').annotate(
        row_number=Window(
            RowNumber(),
            partition_by=F('profile'),
            order_by=[F('badge__tier').desc(), F('earned_at').asc()]
        )
    ).filter(row_number=1).order_by(
        F('badge__tier').desc(), 'earned_at', 'profile__display_psn_username'
    )
    
    return [{
        'rank': rank + 1,
        'psn_username': earner.profile.display_psn_username,
        'earn_date': earner.earned_at.isoformat() if earner.earned_at else 'Unknown',
        'avatar_url': earner.profile.avatar_url,
        'flag': earner.profile.flag,
        'highest_tier': earner.badge.tier,
        'is_premium': earner.profile.user_is_premium,
    } for rank, earner in enumerate(earners)]

def compute_progress_leaderboard(series_slug: str) -> list[dict]:
    from trophies.models import Game, Concept, Stage, Profile, EarnedTrophy, UserBadgeProgress

    stages = Stage.objects.filter(series_slug=series_slug)
    concepts = Concept.objects.filter(stages__in=stages).distinct()
    games = Game.objects.filter(concept__in=concepts).distinct()

    badge_sub = UserBadgeProgress.objects.filter(profile=OuterRef('pk'), badge__series_slug=series_slug)
    trophy_sub = EarnedTrophy.objects.filter(profile=OuterRef('pk'), trophy__game__in=games, earned=True)

    earners = Profile.objects.filter(
        Q(is_linked=True) & (Exists(badge_sub) | Exists(trophy_sub))
    ).annotate(
        plats=Count('earned_trophy_entries__id', filter=Q(earned_trophy_entries__earned=True, earned_trophy_entries__trophy__game__in=games, earned_trophy_entries__trophy__trophy_type='platinum')),
        golds=Count('earned_trophy_entries__id', filter=Q(earned_trophy_entries__earned=True, earned_trophy_entries__trophy__game__in=games, earned_trophy_entries__trophy__trophy_type='gold')),
        silvers=Count('earned_trophy_entries__id', filter=Q(earned_trophy_entries__earned=True, earned_trophy_entries__trophy__game__in=games, earned_trophy_entries__trophy__trophy_type='silver')),
        bronzes=Count('earned_trophy_entries__id', filter=Q(earned_trophy_entries__earned=True, earned_trophy_entries__trophy__game__in=games, earned_trophy_entries__trophy__trophy_type='bronze')),
        max_earn_date=Max('earned_trophy_entries__earned_date_time', filter=Q(earned_trophy_entries__earned=True, earned_trophy_entries__trophy__game__in=games))
    ).order_by(
        '-plats', '-golds', '-silvers', '-bronzes', 'max_earn_date'
    ).only(
        'display_psn_username', 'flag', 'avatar_url', 'user_is_premium'
    )

    return [{
        'rank': rank + 1,
        'psn_username': earner.display_psn_username,
        'flag': earner.flag,
        'avatar_url': earner.avatar_url,
        'trophy_totals': {
            'plats': earner.plats,
            'golds': earner.golds,
            'silvers': earner.silvers,
            'bronzes': earner.bronzes,
        },
        'last_earned_date': earner.max_earn_date.isoformat() if earner.max_earn_date else 'Unknown',
        'is_premium': earner.user_is_premium,
    } for rank, earner in enumerate(earners)]

def compute_total_progress_leaderboard() -> list[dict]:
    from trophies.models import Game, Concept, Stage, Profile, EarnedTrophy

    stages = Stage.objects.all()
    concepts = Concept.objects.filter(stages__in=stages).distinct()
    games = Game.objects.filter(concept__in=concepts).distinct()

    trophy_sub = EarnedTrophy.objects.filter(profile=OuterRef('pk'), trophy__game__in=games, earned=True)

    earners = Profile.objects.filter(
        Q(is_linked=True) & Exists(trophy_sub)
    ).annotate(
        plats=Count('earned_trophy_entries__id', filter=Q(earned_trophy_entries__earned=True, earned_trophy_entries__trophy__game__in=games, earned_trophy_entries__trophy__trophy_type='platinum')),
        golds=Count('earned_trophy_entries__id', filter=Q(earned_trophy_entries__earned=True, earned_trophy_entries__trophy__game__in=games, earned_trophy_entries__trophy__trophy_type='gold')),
        silvers=Count('earned_trophy_entries__id', filter=Q(earned_trophy_entries__earned=True, earned_trophy_entries__trophy__game__in=games, earned_trophy_entries__trophy__trophy_type='silver')),
        bronzes=Count('earned_trophy_entries__id', filter=Q(earned_trophy_entries__earned=True, earned_trophy_entries__trophy__game__in=games, earned_trophy_entries__trophy__trophy_type='bronze')),
        max_earn_date=Max('earned_trophy_entries__earned_date_time', filter=Q(earned_trophy_entries__earned=True, earned_trophy_entries__trophy__game__in=games))
    ).order_by(
        '-plats', '-golds', '-silvers', '-bronzes', 'max_earn_date'
    ).only(
        'display_psn_username', 'flag', 'avatar_url', 'user_is_premium'
    )

    return [{
        'rank': rank + 1,
        'psn_username': earner.display_psn_username,
        'flag': earner.flag,
        'avatar_url': earner.avatar_url,
        'trophy_totals': {
            'plats': earner.plats,
            'golds': earner.golds,
            'silvers': earner.silvers,
            'bronzes': earner.bronzes,
        },
        'last_earned_date': earner.max_earn_date.isoformat() if earner.max_earn_date else 'Unknown',
        'is_premium': earner.user_is_premium,
    } for rank, earner in enumerate(earners)]

def compute_badge_xp_leaderboard() -> list[dict]:
    from trophies.models import Profile, UserBadgeProgress

    progress_sub = UserBadgeProgress.objects.filter(profile=OuterRef('pk'))

    progress_qs = UserBadgeProgress.objects.filter(
        profile=OuterRef('pk')
    ).values('profile').annotate(
        pxp=Sum(
            Case(
                When(badge__tier=1, then=F('completed_concepts') * Value(BRONZE_STAGE_XP)),
                When(badge__tier=2, then=F('completed_concepts') * Value(SILVER_STAGE_XP)),
                When(badge__tier=3, then=F('completed_concepts') * Value(GOLD_STAGE_XP)),
                When(badge__tier=4, then=F('completed_concepts') * Value(PLAT_STAGE_XP)),
                default=Value(0),
                output_field=IntegerField()
            )
        )
    ).values('pxp')

    earners = Profile.objects.filter(
        Q(is_linked=True) & Exists(progress_sub)
    ).annotate(
        progress_xp=Coalesce(Subquery(progress_qs[:1]), 0),
        badge_count=Count('badges', distinct=True),
        total_xp=F('progress_xp') + F('badge_count') * Value(BADGE_TIER_XP)
    ).filter(
        total_xp__gt=0
    ).order_by('-total_xp', '-badge_count', 'display_psn_username')

    return [
        {
            'rank': rank + 1,
            'psn_username': earner.display_psn_username,
            'flag': earner.flag,
            'avatar_url': earner.avatar_url,
            'is_premium': earner.user_is_premium,
            'total_xp': earner.total_xp,
            'total_badges': earner.badge_count,
        } for rank, earner in enumerate(earners)
    ]

# Update methods

def update_profile_games(profile):
    from trophies.models import ProfileGame
    profile.total_games = ProfileGame.objects.filter(profile=profile).count()
    profile.total_completes = ProfileGame.objects.filter(profile=profile, progress=100).count()
    profile.save(update_fields=['total_games', 'total_completes'])

def update_profile_trophy_counts(profile):
    from trophies.models import EarnedTrophy, ProfileGame
    from django.db.models import Sum

    trophy_totals = ProfileGame.objects.filter(profile=profile)
    if profile.hide_hiddens:
        trophy_totals = trophy_totals.filter(user_hidden=False)
    if profile.hide_zeros:
        trophy_totals = trophy_totals.exclude(earned_trophies_count=0)

    aggregates = trophy_totals.aggregate(
        unearned=Coalesce(Sum('unearned_trophies_count'), 0),
        earned=Coalesce(Sum('earned_trophies_count'), 0),
    )

    total_earned = aggregates['earned']
    total_unearned = aggregates['unearned']

    total = total_earned + total_unearned
    avg_progress = (total_earned / total * 100) if total > 0 else 0.0

    profile.total_trophies = total_earned
    profile.total_unearned = total_unearned
    profile.total_plats = EarnedTrophy.objects.filter(profile=profile, earned=True, trophy__trophy_type='platinum').count()
    profile.avg_progress = avg_progress
    profile.save(update_fields=['total_trophies', 'total_unearned', 'total_plats', 'avg_progress'])

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

BRONZE_STAGE_XP = 250
SILVER_STAGE_XP = 75
GOLD_STAGE_XP = 250
PLAT_STAGE_XP = 75
BADGE_TIER_XP = 3000