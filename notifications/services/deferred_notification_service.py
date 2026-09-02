"""
DeferredNotificationService - Handles queuing and deferred creation of notifications during sync.

This service solves two problems:
1. Platinum notifications showing inaccurate trophy counts (created before sync completes)
2. Badge notifications spamming users with multiple tiers of the same badge series

Uses Redis to queue notifications during sync and creates them at appropriate completion points:
- Platinums: Created after each game's sync completes
- Badges: Created after full sync with tier consolidation
"""
import json
import logging
from collections import defaultdict
from datetime import datetime
from django.conf import settings
from django.utils import timezone
from notifications.services.notification_service import NotificationService
from core.services.shareable_data_service import ShareableDataService
from notifications.models import Notification, NotificationTemplate
from trophies.models import Profile, Game, EarnedTrophy, ProfileGame
from trophies.models import UserBadge
from trophies.util_modules.cache import redis_client

logger = logging.getLogger("deferred_notifications")

# Redis TTL for pending notifications (2 hours, matches sync timeout)
PENDING_NOTIFICATION_TTL = 7200


class DeferredNotificationService:
    """Service for queuing and creating deferred notifications during sync."""

    @staticmethod
    def queue_platinum_notification(profile, game, trophy, earned_date):
        """
        Queue a platinum notification to be created after game sync completes.

        Stores minimal context in Redis - full context fetched at creation time for accuracy.

        Args:
            profile: Profile instance
            game: Game instance
            trophy: Trophy instance
            earned_date: datetime of when platinum was earned
        """
        key = f"pending_platinum:{profile.id}:{game.id}"

        data = {
            "profile_id": profile.id,
            "game_id": game.id,
            "trophy_id": trophy.id,
            "earned_date_time": earned_date.isoformat() if earned_date else None,
        }

        try:
            redis_client.set(key, json.dumps(data), ex=PENDING_NOTIFICATION_TTL)
            logger.info(f"Queued platinum notification for {profile.psn_username} - {game.title_name}")
        except Exception as e:
            logger.exception(f"Failed to queue platinum notification: {e}")

    @staticmethod
    def create_platinum_notification_for_game(profile_id, game_id):
        """
        Create platinum notification for a specific game after its sync completes.

        Fetches queued data from Redis, gathers fresh context, and creates notification.

        Args:
            profile_id: Profile ID
            game_id: Game ID
        """
        key = f"pending_platinum:{profile_id}:{game_id}"

        try:
            # Fetch queued data
            data_raw = redis_client.get(key)
            if not data_raw:
                logger.debug(f"No pending platinum notification for profile {profile_id}, game {game_id}")
                return

            data = json.loads(data_raw)

            # Fetch fresh database objects
            try:
                profile = Profile.objects.get(id=data["profile_id"])
                game = Game.objects.get(id=data["game_id"])
                trophy = EarnedTrophy.objects.select_related('trophy').get(
                    profile=profile,
                    trophy__id=data["trophy_id"]
                )
            except (Profile.DoesNotExist, Game.DoesNotExist, EarnedTrophy.DoesNotExist) as e:
                logger.error(f"Failed to fetch objects for platinum notification: {e}")
                redis_client.delete(key)
                return

            # Safety net: skip if game was flagged as shovelware after queueing
            if game.is_shovelware:
                logger.info(f"Skipping platinum notification for {game.title_name} - flagged as shovelware")
                redis_client.delete(key)
                return

            # Get notification template
            try:
                template = NotificationTemplate.objects.get(
                    name='platinum_earned',
                    auto_trigger_enabled=True
                )
            except NotificationTemplate.DoesNotExist:
                logger.error("Platinum earned template not found or not enabled")
                redis_client.delete(key)
                return

            # Get user
            if not profile.user:
                logger.debug(f"No user linked to profile {profile.id}")
                redis_client.delete(key)
                return

            # Check for existing notification to prevent duplicates
            existing = Notification.objects.filter(
                recipient=profile.user,
                notification_type='platinum_earned',
                metadata__game_id=game.id,
            ).exists()
            if existing:
                logger.info(f"Platinum notification already exists for {profile.psn_username} - {game.title_name}, skipping")
                redis_client.delete(key)
                return

            # Fetch fresh ProfileGame data for date/duration stats
            profile_game = ProfileGame.objects.filter(
                profile=profile,
                game=game
            ).first()

            # Compute trophy counts fresh from EarnedTrophy records
            # (ProfileGame.earned_trophies_count is stale at this point - not updated until _job_sync_complete)
            earned_trophy_qs = EarnedTrophy.objects.filter(profile=profile, trophy__game=game)
            fresh_earned_count = earned_trophy_qs.filter(earned=True).count()
            fresh_total_count = earned_trophy_qs.count()
            fresh_progress = round((fresh_earned_count / fresh_total_count) * 100) if fresh_total_count > 0 else 0

            # Per-platinum totals (user_total_platinums, yearly_plats) used to
            # be stored here, but the count was racy when multiple plats from
            # one sync were processed in non-chronological order. The inbox
            # links to the Plat Cards page where the count is computed
            # live, so it isn't needed in the frozen metadata anymore.

            # Build context (replicates logic from signals.py)
            context = {
                'username': profile.display_psn_username or profile.psn_username,
                'trophy_name': trophy.trophy.trophy_name,
                'game_name': game.title_name,
                'game_id': game.id,
                'earned_trophy_id': trophy.id,
                'np_communication_id': game.np_communication_id,
                'concept_id': game.concept.id if game.concept else None,
                'trophy_detail': trophy.trophy.trophy_detail or '',
                'trophy_earn_rate': trophy.trophy.trophy_earn_rate or 0,
                'trophy_rarity': trophy.trophy.trophy_rarity,
                'trophy_icon_url': trophy.trophy.trophy_icon_url or '',
                'game_image': game.display_image_url_large,
                'rarity_label': ShareableDataService.get_rarity_label(trophy.trophy.trophy_rarity),
                'title_platform': game.title_platform,
                'region': game.region,
                'is_regional': game.is_regional,
                'first_played_date_time': profile_game.first_played_date_time.isoformat() if profile_game and profile_game.first_played_date_time else None,
                'last_played_date_time': profile_game.last_played_date_time.isoformat() if profile_game and profile_game.last_played_date_time else None,
                'play_duration_seconds': profile_game.play_duration.total_seconds() if profile_game and profile_game.play_duration else None,
                'earned_trophies_count': fresh_earned_count,
                'total_trophies_count': fresh_total_count,
                'progress_percentage': fresh_progress,
                'user_avatar_url': profile.avatar_url or '',
                'earned_date_time': data["earned_date_time"],
            }

            # Create notification
            NotificationService.create_from_template(
                recipient=profile.user,
                template=template,
                context=context,
            )

            logger.info(f"Created platinum notification for {profile.psn_username} - {game.title_name}")

            # Delete Redis key after successful creation
            redis_client.delete(key)

        except Exception as e:
            logger.exception(f"Failed to create platinum notification for profile {profile_id}, game {game_id}: {e}")
