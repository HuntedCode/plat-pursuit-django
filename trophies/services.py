import logging
from django.utils import timezone
from django.db import transaction
from django.db.models import Q, F
from .models import Profile, Game, ProfileGame, Trophy, EarnedTrophy

logger = logging.getLogger("psn_api")

class PsnApiService:
    """Service class for PSN API data processing and model updates."""

    @classmethod
    def update_profile_from_legacy(cls, profile: Profile, legacy: dict) -> Profile:
        """Update Profile model from PSN legacy profile data."""
        profile.account_id = legacy["profile"].get("accountId")
        profile.np_id = legacy["profile"].get("npId")
        profile.avatar_url = (
            legacy["profile"]["avatarUrls"][0]["avatarUrl"]
            if legacy["profile"]["avatarUrls"]
            else None
        )
        profile.is_plus = legacy["profile"].get("plus", 0) == 1
        profile.about_me = legacy["profile"].get("aboutMe", "")
        profile.languages_used = legacy["profile"].get("languagesUsed", [])
        profile.trophy_level = legacy["profile"]["trophySummary"].get("level", 0)
        profile.progress = legacy["profile"]["trophySummary"].get("progress", 0)
        profile.earned_trophy_summary = legacy["profile"]["trophySummary"][
            "earnedTrophies"
        ]
        profile.last_synced = timezone.now()
        profile.save()
        return profile

    @classmethod
    def create_or_update_game(cls, trophy_title):
        """Create or update Game model from PSN trophy title data."""
        game, created = Game.objects.get_or_create(
            np_communication_id=trophy_title.np_communication_id,
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
                "played_count": 0
            },
        )
        needs_trophy_update = created
        if not created:
            if trophy_title.trophy_set_version != game.trophy_set_version:
                # NEED TO UPDATE ALL TROPHIES FOR THIS GAME!
                needs_trophy_update = True
                logger.warning(f"TROPHIES NEED TO BE UPDATED FOR {game.title_name}")
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
            game.save()
        return game, created, needs_trophy_update
    
    @classmethod
    def update_game_with_title_stats(cls, title_stats):
        games = Game.objects.filter(Q(title_id=title_stats.title_id) | (Q(title_name=title_stats.name) & Q(title_platform__contains=title_stats.category.name)))
        if games:
            for game in games:
                game.title_id = title_stats.title_id
                game.title_image = title_stats.image_url
                game.save()
            return games
        return None

    @classmethod
    def create_or_update_profile_game(cls, profile, game, trophy_title):
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
                "last_updated_datetime": trophy_title.last_updated_datetime
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
            profile_game.save()

            if created:
                game.played_count = F('played_count') + 1
                game.save()
        return profile_game, created

    @classmethod
    def update_profile_game_with_title_stats(cls, profile: Profile, title_stats):
        """Update ProfileGame from title_stats only - no trophy updates."""
        games = cls.update_game_with_title_stats(title_stats)
        if games:
            for game in games:
                try:
                    profile_game = ProfileGame.objects.get(profile=profile, game=game)
                except ProfileGame.DoesNotExist:
                    logger.warning(f"ProfileGame for profile {profile.id} and game {game.np_communication_id} does not exist.")
                    continue

                profile_game.play_count = title_stats.play_count
                profile_game.first_played_date_time = title_stats.first_played_date_time
                profile_game.last_played_date_time = title_stats.last_played_date_time
                profile_game.play_duration = title_stats.play_duration
                profile_game.save()
            return True
        return False
    
    @classmethod
    def assign_title_id(cls, np_comm_id, title_id):
        try:
            game = Game.objects.get(np_communication_id=np_comm_id)
        except Game.DoesNotExist:
            logger.error(f"Game with np_comm_id {np_comm_id} does not exist.")
            raise
        game.title_id = title_id
        game.save()
        

    @classmethod
    def create_or_update_trophy_from_trophy_data(cls, game, trophy_data):
        """Create or update Trophy model from PSN trophy data."""
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
                "trophy_rarity": trophy_data.trophy_rarity.value,
                "trophy_earn_rate": trophy_data.trophy_earn_rate,
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
            trophy.trophy_rarity = trophy_data.trophy_rarity.value
            trophy.trophy_earn_rate = trophy_data.trophy_earn_rate
            trophy.save()
        return trophy, created

    @classmethod
    def create_or_update_earned_trophy_from_trophy_data(
        cls, profile, trophy, trophy_data
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
                "earned_date_time": trophy_data.earned_date_time
            },
        )

        if not created:
            earned_trophy.earned = trophy_data.earned
            earned_trophy.trophy_hidden = trophy_data.trophy_hidden
            earned_trophy.progress = trophy_data.progress
            earned_trophy.progress_rate = trophy_data.progress_rate
            earned_trophy.progressed_date_time = trophy_data.progressed_date_time
            earned_trophy.earned_date_time = trophy_data.earned_date_time
            earned_trophy.save()

        if (created and trophy_data.earned == True) or (not created and earned_trophy.earned == False and trophy_data.earned == True):
            logger.info(f"Earned Count Pre: {trophy.earned_count}")
            trophy.earned_count = F('earned_count') + 1
            logger.info(f"Earned Count Post: {trophy.earned_count}")
            trophy.save()
            trophy.refresh_from_db()
            trophy.earn_rate = trophy.earned_count / trophy.game.played_count if trophy.game.played_count > 0 else 0.0
            trophy.save()
        return earned_trophy, created
