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
from django.core.cache import cache
from django.utils import timezone
from notifications.services.notification_service import NotificationService
from notifications.services.shareable_data_service import ShareableDataService
from notifications.models import NotificationTemplate
from trophies.models import Profile, Game, EarnedTrophy, ProfileGame
from trophies.models import UserBadge

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
            cache.set(key, json.dumps(data), timeout=PENDING_NOTIFICATION_TTL)
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
            data_json = cache.get(key)
            if not data_json:
                logger.debug(f"No pending platinum notification for profile {profile_id}, game {game_id}")
                return

            data = json.loads(data_json)

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
                cache.delete(key)
                return

            # Get notification template
            try:
                template = NotificationTemplate.objects.get(
                    name='platinum_earned',
                    auto_trigger_enabled=True
                )
            except NotificationTemplate.DoesNotExist:
                logger.error("Platinum earned template not found or not enabled")
                cache.delete(key)
                return

            # Get user
            if not profile.user:
                logger.debug(f"No user linked to profile {profile.id}")
                cache.delete(key)
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

            # Count user's total platinums (including this one)
            total_plats = EarnedTrophy.objects.filter(
                profile=profile,
                earned=True,
                trophy__trophy_type='platinum'
            ).count()

            # Get earned date and calculate yearly plats
            earned_date = timezone.datetime.fromisoformat(data["earned_date_time"]) if data["earned_date_time"] else None
            yearly_plats = 0
            earned_year = None

            if earned_date:
                earned_year = earned_date.year
                yearly_plats = EarnedTrophy.objects.filter(
                    profile=profile,
                    earned=True,
                    trophy__trophy_type='platinum',
                    earned_date_time__year=earned_year
                ).count()

            # Build context (replicates logic from signals.py lines 271-342)
            context = {
                'username': profile.display_psn_username or profile.psn_username,
                'trophy_name': trophy.trophy.trophy_name,
                'game_name': game.title_name,
                'game_id': game.id,
                'np_communication_id': game.np_communication_id,
                'concept_id': game.concept.id if game.concept else None,
                'trophy_detail': trophy.trophy.trophy_detail or '',
                'trophy_earn_rate': trophy.trophy.trophy_earn_rate or 0,
                'trophy_rarity': trophy.trophy.trophy_rarity,
                'trophy_icon_url': trophy.trophy.trophy_icon_url or '',
                'game_image': game.title_image or game.title_icon_url or '',
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
                'user_total_platinums': total_plats,
                'user_avatar_url': profile.avatar_url or '',
                'earned_date_time': data["earned_date_time"],
                'yearly_plats': yearly_plats,
                'earned_year': earned_year,
            }

            # Create notification
            NotificationService.create_from_template(
                recipient=profile.user,
                template=template,
                context=context,
            )

            logger.info(f"Created platinum notification for {profile.psn_username} - {game.title_name}")

            # Delete Redis key after successful creation
            cache.delete(key)

        except Exception as e:
            logger.exception(f"Failed to create platinum notification for profile {profile_id}, game {game_id}: {e}")

    @staticmethod
    def queue_badge_notification(profile, badge, context_data):
        """
        Queue a badge notification for later consolidation.

        Stores full context since XP/progress is already calculated at award time.
        Processing happens at sync completion (_job_sync_complete) or manually called
        at the end of admin commands (e.g., refresh_badge_series).

        Args:
            profile: Profile instance
            badge: Badge instance
            context_data: Full context dict from _get_badge_notification_context()
        """
        key = f"pending_badges:{profile.id}"

        try:
            # Fetch existing list or create new
            existing_json = cache.get(key)
            badge_list = json.loads(existing_json) if existing_json else []

            # Append new badge context
            badge_list.append(context_data)

            # Save back to Redis with 1 hour TTL (enough for sync or manual operations)
            cache.set(key, json.dumps(badge_list), timeout=3600)
            logger.info(f"Queued badge notification for {profile.psn_username} - {badge.name}")

        except Exception as e:
            logger.exception(f"Failed to queue badge notification: {e}")

    @staticmethod
    def create_badge_notifications(profile_id):
        """
        Create badge notifications for a profile after sync completes.

        Consolidates multiple tiers of the same badge series - only notifies for highest tier.

        Args:
            profile_id: Profile ID
        """
        key = f"pending_badges:{profile_id}"

        try:
            # Fetch queued badges
            data_json = cache.get(key)
            if not data_json:
                logger.debug(f"No pending badge notifications for profile {profile_id}")
                return

            badge_list = json.loads(data_json)
            if not badge_list:
                cache.delete(key)
                return

            # Get profile and user
            try:
                profile = Profile.objects.get(id=profile_id)
                if not profile.user:
                    logger.debug(f"No user linked to profile {profile_id}")
                    cache.delete(key)
                    return
            except Profile.DoesNotExist:
                logger.error(f"Profile {profile_id} not found")
                cache.delete(key)
                return

            # Get notification template
            try:
                template = NotificationTemplate.objects.get(
                    name='badge_awarded',
                    auto_trigger_enabled=True
                )
            except NotificationTemplate.DoesNotExist:
                logger.warning("Badge awarded template not found or not enabled")
                cache.delete(key)
                return

            # Group badges by series_slug
            by_series = defaultdict(list)
            no_series = []

            for badge_context in badge_list:
                series_slug = badge_context.get('series_slug', '')
                if series_slug:
                    by_series[series_slug].append(badge_context)
                else:
                    # No series (misc badges) - create immediately without consolidation
                    no_series.append(badge_context)

            # Create notifications for non-series badges
            for badge_context in no_series:
                try:
                    NotificationService.create_from_template(
                        recipient=profile.user,
                        template=template,
                        context=badge_context,
                    )
                    logger.info(f"Created badge notification for {profile.psn_username} - {badge_context.get('badge_name', 'Unknown')}")
                except Exception as e:
                    logger.exception(f"Failed to create badge notification: {e}")

            # For each series, keep only highest tier
            for series_slug, badge_contexts in by_series.items():
                # Sort by badge_tier descending
                badge_contexts.sort(key=lambda x: x.get('badge_tier', 0), reverse=True)
                highest_tier_badge = badge_contexts[0]

                try:
                    NotificationService.create_from_template(
                        recipient=profile.user,
                        template=template,
                        context=highest_tier_badge,
                    )
                    logger.info(
                        f"Created consolidated badge notification for {profile.psn_username} - "
                        f"{highest_tier_badge.get('badge_name', 'Unknown')} (highest of {len(badge_contexts)} tiers)"
                    )
                except Exception as e:
                    logger.exception(f"Failed to create badge notification for series {series_slug}: {e}")

            # Delete Redis key after processing
            cache.delete(key)

        except Exception as e:
            logger.exception(f"Failed to create badge notifications for profile {profile_id}: {e}")
