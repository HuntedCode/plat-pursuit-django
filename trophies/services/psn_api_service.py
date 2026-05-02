import logging
import re
import time
from datetime import timedelta
from django.utils import timezone
from django.db import transaction, IntegrityError, OperationalError
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type
from collections import defaultdict
from django.db.models import F, Count, Max, Q
from trophies.models import Profile, Game, ProfileGame, Trophy, EarnedTrophy, Concept, TrophyGroup, Badge
from psnawp_api.models.title_stats import TitleStats
from psnawp_api.models.trophies import TrophyTitle, TrophyGroupSummary
from trophies.discord_utils.discord_notifications import notify_new_platinum

logger = logging.getLogger("psn_api")


# CJK-character regex used to prevent title regressions: once a Concept's
# unified_title is Latin (either natively or promoted via IGDB rename), we
# don't let a later PSN sync clobber it back to a CJK form. Mirrors the
# pattern in IGDBService but kept local to avoid an import cycle.
_CJK_PATTERN = re.compile(
    '['
    '\u3040-\u309F\u30A0-\u30FF\u3400-\u4DBF\u4E00-\u9FFF'
    '\uAC00-\uD7AF\u1100-\u11FF\u3130-\u318F\uFF00-\uFFEF'
    ']'
)

class PsnApiService:
    """Service class for PSN API data processing and model updates."""

    @classmethod
    def update_profile_from_legacy(cls, profile: Profile, legacy: dict, is_public: bool) -> Profile:
        """Update Profile model from PSN legacy profile data."""
        account_id = legacy["profile"].get("accountId")
        new_psn_username = legacy["profile"].get("onlineId").lower()

        # Check if another profile already has this account_id (duplicate detection)
        if account_id and profile.account_id != account_id:
            existing_profile = Profile.objects.filter(account_id=account_id).exclude(id=profile.id).first()

            if existing_profile:
                # A duplicate exists - automatically merge into the existing profile
                logger.warning(
                    f"Duplicate account_id detected: Profile {profile.id} ({profile.psn_username}) "
                    f"has same account_id {account_id} as Profile {existing_profile.id} ({existing_profile.psn_username}). "
                    f"Auto-merging: updating existing profile to new username '{new_psn_username}' and deleting duplicate."
                )

                # Transfer any user linkage from duplicate to existing profile if existing doesn't have one
                if profile.user and not existing_profile.user:
                    logger.info(f"Transferring user {profile.user.id} from duplicate profile {profile.id} to existing profile {existing_profile.id}")
                    existing_profile.user = profile.user
                    existing_profile.is_linked = profile.is_linked

                # Transfer Discord linkage if duplicate has it but existing doesn't
                if profile.discord_id and not existing_profile.discord_id:
                    logger.info(f"Transferring Discord ID {profile.discord_id} from duplicate profile {profile.id} to existing profile {existing_profile.id}")
                    existing_profile.discord_id = profile.discord_id
                    existing_profile.discord_linked_at = profile.discord_linked_at
                    existing_profile.is_discord_verified = profile.is_discord_verified

                # Update the existing profile with new data and continue sync on it
                profile = existing_profile
                logger.info(f"Switched to existing profile {profile.id} for sync continuation")

        # Update profile data from PSN
        profile.psn_username = new_psn_username
        profile.display_psn_username = legacy["profile"].get("onlineId")
        profile.account_id = account_id
        profile.np_id = legacy["profile"].get("npId")
        profile.avatar_url = (
            legacy["profile"]["avatarUrls"][0]["avatarUrl"]
            if legacy["profile"]["avatarUrls"]
            else None
        )
        profile.is_plus = legacy["profile"].get("plus", 0) == 1
        profile.about_me = legacy["profile"].get("aboutMe", "")
        profile.languages_used = legacy["profile"].get("languagesUsed", [])

        if is_public:
            profile.trophy_level = legacy["profile"]["trophySummary"].get("level", 0)
            profile.progress = legacy["profile"]["trophySummary"].get("progress", 0)
            profile.earned_trophy_summary = legacy["profile"]["trophySummary"][
                "earnedTrophies"
            ]

        profile.psn_history_public = is_public
        profile.last_synced = timezone.now()

        try:
            profile.save(update_fields=['psn_username', 'display_psn_username', 'account_id', 'np_id', 'avatar_url', 'is_plus', 'about_me', 'languages_used', 'trophy_level', 'progress', 'earned_trophy_summary', 'psn_history_public', 'last_synced', 'user', 'is_linked', 'discord_id', 'discord_linked_at', 'is_discord_verified'])
        except IntegrityError as e:
            error_msg = str(e).lower()
            if 'account_id' in error_msg:
                # Race condition - another sync just saved the same account_id
                logger.error(f"Race condition on account_id {account_id} for profile {profile.id}")
                profile.set_sync_status('error')
                raise ValueError(f"Race condition: account_id {account_id} already exists")
            elif 'psn_username' in error_msg:
                # The username we're trying to update to already exists as another profile
                # This means we successfully merged into existing but the old duplicate still exists
                logger.warning(f"Username {new_psn_username} already exists - this is expected during merge cleanup")
                # Just re-save without updating psn_username since it's already correct on existing profile
                profile.save(update_fields=['display_psn_username', 'account_id', 'np_id', 'avatar_url', 'is_plus', 'about_me', 'languages_used', 'trophy_level', 'progress', 'earned_trophy_summary', 'psn_history_public', 'last_synced', 'user', 'is_linked', 'discord_id', 'discord_linked_at', 'is_discord_verified'])
            else:
                raise

        return profile

    @classmethod
    def delete_duplicate_profile(cls, duplicate_profile_id: int, merged_into_profile_id: int):
        """
        Safely delete a duplicate profile after it has been merged into another profile.

        This should only be called after all data has been transferred to the target profile.
        """
        try:
            duplicate = Profile.objects.get(id=duplicate_profile_id)

            # Safety check: ensure this profile doesn't have a user or Discord link that wasn't transferred
            if duplicate.user:
                logger.warning(
                    f"Duplicate profile {duplicate_profile_id} still has user {duplicate.user.id} linked. "
                    f"Not deleting to prevent data loss. Manual review required."
                )
                return False

            if duplicate.discord_id:
                logger.warning(
                    f"Duplicate profile {duplicate_profile_id} still has Discord ID {duplicate.discord_id}. "
                    f"Not deleting to prevent data loss. Manual review required."
                )
                return False

            # Check if the duplicate has any trophy data
            from trophies.models import ProfileGame, EarnedTrophy
            profilegame_count = ProfileGame.objects.filter(profile=duplicate).count()
            earned_trophy_count = EarnedTrophy.objects.filter(profile=duplicate).count()

            if profilegame_count > 0 or earned_trophy_count > 0:
                logger.warning(
                    f"Duplicate profile {duplicate_profile_id} has trophy data "
                    f"({profilegame_count} games, {earned_trophy_count} trophies). "
                    f"Not deleting to prevent data loss. Manual review required."
                )
                return False

            # Safe to delete - no important data
            username = duplicate.psn_username
            duplicate.delete()
            logger.info(
                f"Successfully deleted duplicate profile {duplicate_profile_id} ({username}) "
                f"which was merged into profile {merged_into_profile_id}"
            )
            return True

        except Profile.DoesNotExist:
            logger.info(f"Duplicate profile {duplicate_profile_id} already deleted")
            return True
        except Exception as e:
            logger.error(f"Error deleting duplicate profile {duplicate_profile_id}: {e}")
            return False

    @classmethod
    def update_profile_region(cls, profile: Profile, region_data) -> Profile:
        """Update profile with region data from PSN."""
        profile.country = region_data.name if region_data.name else ''
        profile.country_code = region_data.alpha_2 if region_data.alpha_2 else ''
        profile.flag = region_data.flag if region_data.flag else ''
        profile.save(update_fields=['country', 'country_code', 'flag'])
        return profile

    @classmethod
    @retry(stop=stop_after_attempt(3), wait=wait_fixed(1), retry=retry_if_exception_type(OperationalError), reraise=True)
    def create_or_update_game(cls, trophy_title: TrophyTitle):
        """Create or update Game model from PSN trophy title data.

        The defaults dict only contains fields derived from the TrophyTitle payload.
        Any other Game field (flags, counters, status fields) should rely on its
        model-level default. Do NOT hardcode values here just to mirror model defaults,
        the list will rot every time a new field is added to Game.
        """
        game, created = Game.objects.get_or_create(
            np_communication_id=trophy_title.np_communication_id.strip(),
            defaults={
                "np_service_name": trophy_title.np_service_name,
                "trophy_set_version": trophy_title.trophy_set_version,
                "title_name": trophy_title.title_name,
                "title_detail": trophy_title.title_detail,
                "title_icon_url": trophy_title.title_icon_url,
                "title_platform": [platform.value for platform in trophy_title.title_platform],
                "has_trophy_groups": trophy_title.has_trophy_groups,
                "defined_trophies": {
                    "bronze": trophy_title.defined_trophies.bronze,
                    "silver": trophy_title.defined_trophies.silver,
                    "gold": trophy_title.defined_trophies.gold,
                    "platinum": trophy_title.defined_trophies.platinum
                },
            },
        )
        needs_trophy_update = created
        if not created:
            if trophy_title.trophy_set_version != game.trophy_set_version:
                # NEED TO UPDATE ALL TROPHIES FOR THIS GAME!
                needs_trophy_update = True
                logger.warning(f"TROPHIES NEED TO BE UPDATED FOR {game.title_name}")
            update_fields = [
                'trophy_set_version', 'np_service_name', 'title_detail',
                'title_icon_url', 'has_trophy_groups', 'defined_trophies',
            ]
            if not game.lock_title:
                game.title_name = trophy_title.title_name
                update_fields.append('title_name')
            game.trophy_set_version = trophy_title.trophy_set_version
            game.np_service_name = trophy_title.np_service_name
            game.title_detail = trophy_title.title_detail
            game.title_icon_url = trophy_title.title_icon_url
            game.has_trophy_groups = trophy_title.has_trophy_groups
            game.defined_trophies = {
                "bronze": trophy_title.defined_trophies.bronze,
                "silver": trophy_title.defined_trophies.silver,
                "gold": trophy_title.defined_trophies.gold,
                "platinum": trophy_title.defined_trophies.platinum
            }
            game.save(update_fields=update_fields)
        return game, created, needs_trophy_update
    
    @classmethod
    def create_or_update_trophy_groups_from_summary(cls, game: Game, summary: TrophyGroupSummary):
        trophy_group, created = TrophyGroup.objects.get_or_create(
            game=game, trophy_group_id=summary.trophy_group_id,
            defaults={
                'trophy_group_name': summary.trophy_group_name,
                'trophy_group_detail': summary.trophy_group_detail,
                'trophy_group_icon_url': summary.trophy_group_icon_url,
                'defined_trophies': {
                    'bronze': summary.defined_trophies.bronze,
                    'silver': summary.defined_trophies.silver,
                    'gold': summary.defined_trophies.gold,
                    'platinum': summary.defined_trophies.platinum
                }
            }
        )

        if not created:
            trophy_group.trophy_group_name = summary.trophy_group_name
            trophy_group.trophy_group_detail = summary.trophy_group_detail
            trophy_group.trophy_group_icon_url = summary.trophy_group_icon_url
            trophy_group.defined_trophies = {
                'bronze': summary.defined_trophies.bronze,
                'silver': summary.defined_trophies.silver,
                'gold': summary.defined_trophies.gold,
                'platinum': summary.defined_trophies.platinum
            }
            trophy_group.save()
        return trophy_group, created
    
    @classmethod
    def create_concept_from_details(cls, details):
        try:
            descriptions_short = next((d['desc'] for d in details['descriptions'] if d['type'] == 'SHORT'), '')
        except Exception:
            descriptions_short = ''
        try:
            descriptions_long = next((d['desc'] for d in details['descriptions'] if d['type'] == 'LONG'), '')
        except Exception:
            descriptions_long = ''

        return Concept.objects.get_or_create(
            concept_id=details.get('id'),
            defaults={
                'unified_title': details.get('nameEn') or details.get('name', ''),
                'publisher_name': details.get('publisherName', ''),
                'genres': details.get('genres', []),
                'subgenres': details.get('subGenres', []),
                'descriptions': {
                    'short': descriptions_short,
                    'long': descriptions_long
                },
                'content_rating': details.get('contentRating', {}),
            })

    @classmethod
    def update_concept_english_fields(cls, concept, details):
        """Refresh language-sensitive concept fields from a US/en-US PSN response.

        Called only on the English sync path for an already-existing concept, so a
        concept first created from an Asian-region fallback upgrades to canonical
        English metadata once we finally see it. Only overwrites when the new value
        is non-empty, so a sparse refresh cannot blank out previously-good data.
        """
        try:
            descriptions_short = next((d['desc'] for d in details['descriptions'] if d['type'] == 'SHORT'), '')
        except Exception:
            descriptions_short = ''
        try:
            descriptions_long = next((d['desc'] for d in details['descriptions'] if d['type'] == 'LONG'), '')
        except Exception:
            descriptions_long = ''

        update_fields = []

        new_title = details.get('nameEn') or details.get('name', '')
        if new_title and concept.unified_title != new_title:
            # Admin title-lock: the concept's unified_title was manually
            # curated (typically via `review_title_merges`). Sync must not
            # touch it, period.
            if concept.title_lock:
                logger.info(
                    f'Skipping unified_title update for concept {concept.concept_id}: '
                    f'title_lock=True (current: "{concept.unified_title}", '
                    f'PSN: "{new_title}")'
                )
            # CJK-regression guard: once a concept's title is Latin (either
            # natively or promoted via IGDB rename), don't let a later PSN
            # sync overwrite it back to a CJK form. Without this, a Japanese
            # PSN sync response would undo our IGDB-English rename.
            elif (_CJK_PATTERN.search(new_title)
                    and concept.unified_title
                    and not _CJK_PATTERN.search(concept.unified_title)):
                logger.info(
                    f'Skipping unified_title update for concept {concept.concept_id}: '
                    f'refusing to regress "{concept.unified_title}" (Latin) -> '
                    f'"{new_title}" (CJK)'
                )
            else:
                concept.unified_title = new_title
                update_fields.append('unified_title')

        new_publisher = details.get('publisherName', '')
        if new_publisher and concept.publisher_name != new_publisher:
            concept.publisher_name = new_publisher
            update_fields.append('publisher_name')

        new_genres = details.get('genres', [])
        if new_genres and concept.genres != new_genres:
            concept.genres = new_genres
            update_fields.append('genres')

        new_subgenres = details.get('subGenres', [])
        if new_subgenres and concept.subgenres != new_subgenres:
            concept.subgenres = new_subgenres
            update_fields.append('subgenres')

        new_descriptions = {'short': descriptions_short, 'long': descriptions_long}
        if (descriptions_short or descriptions_long) and concept.descriptions != new_descriptions:
            concept.descriptions = new_descriptions
            update_fields.append('descriptions')

        new_content_rating = details.get('contentRating', {})
        if new_content_rating and concept.content_rating != new_content_rating:
            concept.content_rating = new_content_rating
            update_fields.append('content_rating')

        if update_fields:
            concept.save(update_fields=update_fields)
            logger.info(
                f"Concept {concept.concept_id} refreshed English fields: "
                f"{', '.join(update_fields)}"
            )
    
    @classmethod
    def update_profile_game_with_title_stats(cls, profile: Profile, title_stats: TitleStats):
        games = Game.objects.filter(title_ids__contains=title_stats.title_id)
        if games:
            needs_refresh = False
            for game in games:
                try:
                    profile_game = ProfileGame.objects.get(profile=profile, game=game)
                except ProfileGame.DoesNotExist:
                    logger.error(f"Could not find ProfileGame entry for {profile} - {game}")
                    return False
                game.title_image = title_stats.image_url
                game.save(update_fields=['title_image'])
                profile_game.play_count = title_stats.play_count
                profile_game.first_played_date_time = title_stats.first_played_date_time
                profile_game.last_played_date_time = title_stats.last_played_date_time
                profile_game.play_duration = title_stats.play_duration
                profile_game.save(update_fields=['play_count', 'first_played_date_time', 'last_played_date_time', 'play_duration'])

                if game.concept_lock:
                    continue
                if game.concept and not str(game.concept.concept_id).startswith('PP_'):
                    if game.concept_stale:
                        logger.info(f"Game {game} marked as concept_stale. Queuing for concept refresh.")
                        needs_refresh = True
                    elif title_stats.title_id not in game.concept.title_ids:
                        logger.info(f"Game {game} has concept {game.concept.concept_id} but title_id "
                                    f"{title_stats.title_id} not in concept's title_ids. Queuing for concept refresh.")
                        needs_refresh = True
                else:
                    logger.warning(f"Game {title_stats.title_id} does not have an expected concept.")
                    needs_refresh = True

            return not needs_refresh
        logger.warning(f"No games found for {title_stats.title_id}")
        return False

    @classmethod
    def create_or_update_profile_game(cls, profile, game, trophy_title: TrophyTitle):
        """Create or update ProfileGame model from PSN trophy title data."""
        profile_game, created = ProfileGame.objects.get_or_create(
            profile=profile,
            game=game,
            defaults={
                "progress": trophy_title.progress,
                "hidden_flag": trophy_title.hidden_flag,
                "earned_trophies": {
                    "bronze": trophy_title.earned_trophies.bronze,
                    "silver": trophy_title.earned_trophies.silver,
                    "gold": trophy_title.earned_trophies.gold,
                    "platinum": trophy_title.earned_trophies.platinum
                },
                "last_updated_datetime": trophy_title.last_updated_datetime,
                "user_hidden": False,
            },
        )

        if not created and (profile_game.last_updated_datetime != trophy_title.last_updated_datetime):
            profile_game.progress = trophy_title.progress
            profile_game.hidden_flag = trophy_title.hidden_flag
            profile_game.earned_trophies = {
                "bronze": trophy_title.earned_trophies.bronze,
                "silver": trophy_title.earned_trophies.silver,
                "gold": trophy_title.earned_trophies.gold,
                "platinum": trophy_title.earned_trophies.platinum
            }
            profile_game.last_updated_datetime = trophy_title.last_updated_datetime
            profile_game.user_hidden = False  # PSN returned this game, so it's not hidden
            profile_game.save()

        if created:
            game.played_count = F('played_count') + 1
            game.save()
        return profile_game, created

    @classmethod
    def create_or_update_trophy_from_trophy_data(cls, game: Game, trophy_data):
        """Create or update Trophy model from PSN trophy data."""
        trophy_rarity = getattr(trophy_data, 'trophy_rarity', None) or None
        trophy_earn_rate = getattr(trophy_data, 'trophy_earn_rate', 0.0) or 0.0

        trophy, created = Trophy.objects.get_or_create(
            trophy_id=trophy_data.trophy_id,
            game=game,
            defaults={
                "trophy_set_version": trophy_data.trophy_set_version,
                "trophy_type": trophy_data.trophy_type.value,
                "trophy_name": trophy_data.trophy_name,
                "trophy_detail": trophy_data.trophy_detail,
                "trophy_icon_url": trophy_data.trophy_icon_url,
                "trophy_group_id": trophy_data.trophy_group_id,
                "progress_target_value": trophy_data.trophy_progress_target_value,
                "reward_name": trophy_data.trophy_reward_name,
                "reward_img_url": trophy_data.trophy_reward_img_url,
                "trophy_rarity": trophy_rarity.value if trophy_rarity else None,
                "trophy_earn_rate": trophy_earn_rate if trophy_earn_rate else 0.0,
                "earned_count": 0,
                "earn_rate": 0.0
            },
        )
        if not created:
            trophy.trophy_set_version = trophy_data.trophy_set_version
            trophy.trophy_type = trophy_data.trophy_type.value
            trophy.trophy_name = trophy_data.trophy_name
            trophy.trophy_detail = trophy_data.trophy_detail
            trophy.trophy_icon_url = trophy_data.trophy_icon_url
            trophy.trophy_group_id = trophy_data.trophy_group_id
            trophy.progress_target_value = trophy_data.trophy_progress_target_value
            trophy.reward_name = trophy_data.trophy_reward_name
            trophy.reward_img_url = trophy_data.trophy_reward_img_url
            trophy.trophy_rarity = trophy_data.trophy_rarity.value if trophy_data.trophy_rarity else None
            trophy.trophy_earn_rate = trophy_data.trophy_earn_rate if trophy_data.trophy_earn_rate else 0.0
            trophy.save()
        
        if trophy.trophy_type == 'platinum':
            from trophies.services.shovelware_detection_service import ShovelwareDetectionService
            ShovelwareDetectionService.evaluate_game(game)
            # Refresh in-memory game object: evaluate_game uses queryset .update()
            # which changes DB but not the in-memory instance. Downstream checks
            # (deferred notifications, post_save signal) need fresh shovelware_status.
            game.refresh_from_db()
        return trophy, created

    @classmethod
    def create_or_update_earned_trophy_from_trophy_data(
        cls, profile: Profile, trophy: Trophy, trophy_data
    ):
        """Create or update EarnedTrophy model from PSN trophy data."""
        earned_trophy, created = EarnedTrophy.objects.get_or_create(
            profile=profile,
            trophy=trophy,
            defaults={
                "earned": trophy_data.earned,
                "trophy_hidden": trophy_data.trophy_hidden,
                "progress": trophy_data.progress,
                "progress_rate": trophy_data.progress_rate,
                "progressed_date_time": trophy_data.progressed_date_time,
                "earned_date_time": trophy_data.earned_date_time,
                "user_hidden": False,
            },
        )
        # Stamp the trophy type so the post_save signal handlers maintaining
        # Trophy.earned_count and Profile.total_<type> counters can read it
        # without an extra SELECT for the Trophy row. See trophies/signals.py.
        earned_trophy._trophy_type = trophy.trophy_type

        # Detect earned flip (unearned -> earned) for notification purposes
        is_earned_flip = (not created) and earned_trophy.earned == False and trophy_data.earned == True
        is_new_earn = (created and trophy_data.earned == True) or is_earned_flip

        notify = False
        if is_new_earn:
            threshold = timezone.now() - timedelta(days=2)
            notify = (
                profile.discord_id
                and earned_trophy.trophy.trophy_type == 'platinum'
                and trophy_data.earned_date_time is not None
                and trophy_data.earned_date_time >= threshold
            )

        if not created:
            # Stamp the previous earned value so the post_save signals that
            # maintain Trophy.earned_count and Profile.total_<type> can detect
            # transitions during sync. We use `_sync_previous_earned` (not
            # `_previous_earned`) deliberately: the existing pre_save handler
            # in notifications/signals.py overwrites `_previous_earned` to
            # None during sync to disable the post_save notification handler,
            # which would otherwise clobber any stamp we put on that name.
            # See trophies/signals.py for the consumer.
            earned_trophy._sync_previous_earned = earned_trophy.earned
            earned_trophy.earned = trophy_data.earned
            earned_trophy.trophy_hidden = trophy_data.trophy_hidden
            earned_trophy.progress = trophy_data.progress
            earned_trophy.progress_rate = trophy_data.progress_rate
            earned_trophy.progressed_date_time = trophy_data.progressed_date_time
            earned_trophy.earned_date_time = trophy_data.earned_date_time
            earned_trophy.user_hidden = False  # PSN returned this trophy, so it's not hidden
            earned_trophy.save()

        if notify:
            earned_trophy.refresh_from_db()
            if not earned_trophy.trophy.game.is_shovelware:
                notify_new_platinum(profile, earned_trophy)

        # During sync with pre_save suppressed, the post_save signal won't detect earned
        # flips (it relies on _previous_earned from pre_save). Queue the deferred platinum
        # notification directly for the flip case so in-app notifications are still created.
        if is_earned_flip and trophy.trophy_type == 'platinum' and profile.sync_status == 'syncing':
            if trophy_data.earned_date_time and not trophy.game.is_shovelware and profile.user:
                threshold = timezone.now() - timedelta(days=2)
                if trophy_data.earned_date_time >= threshold:
                    try:
                        from notifications.services.deferred_notification_service import DeferredNotificationService
                        DeferredNotificationService.queue_platinum_notification(
                            profile=profile, game=trophy.game,
                            trophy=trophy, earned_date=trophy_data.earned_date_time,
                        )
                    except Exception:
                        logger.exception(f"Failed to queue deferred platinum notification for earned flip")

        return earned_trophy, created

    @classmethod
    def get_profile_trophy_summary(cls, profile: Profile):
        """Get trophy counts using a single aggregation query instead of 5 separate queries.

        Excludes trophies flagged user_hidden so the local count matches PSN's
        trophy_summary (which omits trophies from games the user has hidden).
        """
        try:
            result = EarnedTrophy.objects.filter(profile=profile, earned=True, user_hidden=False).aggregate(
                total=Count('id'),
                bronze=Count('id', filter=Q(trophy__trophy_type='bronze')),
                silver=Count('id', filter=Q(trophy__trophy_type='silver')),
                gold=Count('id', filter=Q(trophy__trophy_type='gold')),
                platinum=Count('id', filter=Q(trophy__trophy_type='platinum')),
            )
            return result
        except Exception:
            return {
                'total': 0,
                'bronze': 0,
                'silver': 0,
                'gold': 0,
                'platinum': 0,
            }

    
    @classmethod
    def get_tracked_trophies_for_game(cls, profile: Profile, np_comm_id: str):
        """Get trophy counts for a game using a single aggregation query."""
        try:
            game = Game.objects.get(np_communication_id=np_comm_id)
        except Game.DoesNotExist:
            logger.error(f"Could not find game {np_comm_id}")
            raise

        trophies = EarnedTrophy.objects.filter(
            profile=profile, trophy__game=game, earned=True
        ).aggregate(
            total=Count('id'),
            bronze=Count('id', filter=Q(trophy__trophy_type='bronze')),
            silver=Count('id', filter=Q(trophy__trophy_type='silver')),
            gold=Count('id', filter=Q(trophy__trophy_type='gold')),
            platinum=Count('id', filter=Q(trophy__trophy_type='platinum')),
        )

        return game, trophies
    
    @classmethod
    @retry(stop=stop_after_attempt(3), wait=wait_fixed(1), retry=retry_if_exception_type(OperationalError), reraise=True)
    def update_profilegame_stats(cls, profilegame_ids: list[int]):
        start_time = time.time()
        batch_size = 500

        pg_qs = ProfileGame.objects.filter(id__in=profilegame_ids).select_related('game', 'profile')
        total_pgs = pg_qs.count()

        if total_pgs == 0:
            logger.info("No ProfileGames to update.")
            return

        # Collect profile_id/game_id pairs for the batch annotated query
        pg_list = list(pg_qs)
        profile_ids = {pg.profile_id for pg in pg_list}
        game_ids_set = {pg.game_id for pg in pg_list}

        # Single annotated query: compute all 4 stats grouped by (profile_id, game_id)
        stats_qs = (
            EarnedTrophy.objects
            .filter(profile_id__in=profile_ids, trophy__game_id__in=game_ids_set)
            .values('profile_id', 'trophy__game_id')
            .annotate(
                earned_count=Count('id', filter=Q(earned=True)),
                unearned_count=Count('id', filter=Q(earned=False)),
                plat_earned=Count('id', filter=Q(earned=True, trophy__trophy_type='platinum')),
                max_earned_date=Max('earned_date_time', filter=Q(earned=True)),
            )
        )
        stats_dict = {
            (row['profile_id'], row['trophy__game_id']): row
            for row in stats_qs
        }

        pg_to_update = []
        unique_game_ids = set()

        for pg in pg_list:
            row = stats_dict.get((pg.profile_id, pg.game_id), {})
            pg.earned_trophies_count = row.get('earned_count', 0)
            pg.unearned_trophies_count = row.get('unearned_count', 0)
            pg.has_plat = row.get('plat_earned', 0) > 0
            pg.most_recent_trophy_date = row.get('max_earned_date')
            pg_to_update.append(pg)
            unique_game_ids.add(pg.game_id)

        # Bulk update in batches
        for i in range(0, len(pg_to_update), batch_size):
            batch = pg_to_update[i:i + batch_size]
            ProfileGame.objects.bulk_update(batch, ['earned_trophies_count', 'unearned_trophies_count', 'has_plat', 'most_recent_trophy_date'])
            logger.info(f"Updated batch of {len(batch)} ProfileGames.")
        logger.info(f"Updated {total_pgs} ProfileGames.")

        # NOTE: Trophy.earned_count / earn_rate / Game.played_count used to be
        # recomputed inline here on every sync_complete. Under concurrent
        # sync_completes, those global GROUP BY queries fanned out into
        # massive contention and pegged DB CPU until the web container OOM'd
        # (May 2026 incident). Those aggregates are global stats that don't
        # need real-time updates per-profile — they're now reconciled by the
        # daily `recalc_earn_rates` cron, with incremental signal updates
        # keeping them live between cron runs. See
        # docs/guides/cron-jobs.md#recalc_earn_rates.

        duration = time.time() - start_time
        logger.info(f"Completed per-profile stats update for {len(profilegame_ids)} ProfileGames in {duration:.2f}s")
            
    
    @classmethod
    def create_badge_group_from_form(cls, form_data: dict):
        name = form_data['name']
        badge_type_name = form_data['badge_type'].capitalize()
        if badge_type_name == 'Collection':
            title = 'Collector'
        elif badge_type_name == 'Megamix':
            title = 'Mega Master'
        elif badge_type_name == 'Developer':
            title = 'Studio Champion'
        elif badge_type_name == 'User':
            title = 'Community Champion'
        elif badge_type_name == 'Genre':
            title = 'Genre Guru'
        else:
            title = 'Series Master'

        submitted_by = form_data.get('submitted_by')

        if badge_type_name == 'Developer':
            description = f"Earn plats from {name} games!"
        elif badge_type_name == 'User':
            submitter_name = submitted_by.psn_username if submitted_by else 'the community'
            description = f"A community-curated badge submitted by: {submitter_name}"
        else:
            description = f"Earn plats in the {name} {badge_type_name}!"
        create_kwargs = {
            'name': name + ' Bronze',
            'series_slug': form_data['series_slug'] or '',
            'description': description,
            'display_title': f"{name} {title}",
            'display_series': f"{name} {badge_type_name}",
            'tier': 1,
            'badge_type': form_data['badge_type'],
            'view_count': 0,
        }
        if submitted_by:
            create_kwargs['submitted_by'] = submitted_by

        base_badge = Badge.objects.create(**create_kwargs)
        base_badge.save()
        base_badge.update_most_recent_concept()

        for i, tier in enumerate(['Silver', 'Gold', 'Platinum']):
            tier_kwargs = {
                'name': name + f" {tier}",
                'series_slug': form_data['series_slug'] or '',
                'base_badge': base_badge,
                'tier': i + 2,
                'badge_type': form_data['badge_type'],
                'view_count': 0,
            }
            if submitted_by:
                tier_kwargs['submitted_by'] = submitted_by
            badge = Badge.objects.create(**tier_kwargs)