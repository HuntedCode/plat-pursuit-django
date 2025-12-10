import logging
import requests
from datetime import timedelta
from django.utils import timezone
from django.db.models import F
from trophies.models import Profile, Game, ProfileGame, Trophy, EarnedTrophy, Concept, TrophyGroup
from trophies.utils import DISCORD_PLATINUM_WEBHOOK_URL, PLAT_PURSUIT_EMOJI_ID, PLATINUM_EMOJI_ID
from psnawp_api.models.title_stats import TitleStats
from psnawp_api.models.trophies import TrophyTitle, TrophyGroupSummary

logger = logging.getLogger("psn_api")

class PsnApiService:
    """Service class for PSN API data processing and model updates."""

    @classmethod
    def update_profile_from_legacy(cls, profile: Profile, legacy: dict, is_public: bool) -> Profile:
        """Update Profile model from PSN legacy profile data."""
        profile.display_psn_username = legacy["profile"].get("onlineId")
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

        if is_public:
            profile.trophy_level = legacy["profile"]["trophySummary"].get("level", 0)
            profile.progress = legacy["profile"]["trophySummary"].get("progress", 0)
            profile.earned_trophy_summary = legacy["profile"]["trophySummary"][
                "earnedTrophies"
            ]
        
        profile.psn_history_public = is_public
        profile.last_synced = timezone.now()
        profile.save()
        return profile
    
    @classmethod 
    def update_profile_region(cls, profile: Profile, region_data) -> Profile:
        """Update profile with region data from PSN."""
        profile.country = region_data.name if region_data.name else ''
        profile.country_code = region_data.alpha_2 if region_data.alpha_2 else ''
        profile.flag = region_data.flag if region_data.flag else ''
        profile.save(update_fields=['country', 'country_code', 'flag'])
        return profile

    @classmethod
    def create_or_update_game(cls, trophy_title: TrophyTitle):
        """Create or update Game model from PSN trophy title data."""
        game, created = Game.objects.get_or_create(
            np_communication_id=trophy_title.np_communication_id.strip(),
            defaults={
                "np_service_name": trophy_title.np_service_name,
                "trophy_set_version": trophy_title.trophy_set_version,
                "title_name": trophy_title.title_name.strip(),
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
                "played_count": 0,
                "is_regional": False,
                "is_shovelware": False,
                "is_obtainable": True,
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
    def create_concept_from_details(cls, details, update_flag=False):
        try:
            descriptions_short = next((d['desc'] for d in details['descriptions'] if d['type'] == 'SHORT'), '')
        except:
            descriptions_short = ''
        try:
            descriptions_long = next((d['desc'] for d in details['descriptions'] if d['type'] == 'LONG'), '')
        except:
            descriptions_long = ''
        
        try:
            media = {
                'images': details.get('defaultProduct', {}).get('media', {}).get('images', []),
                'videos': details.get('defaultProduct', {}).get('media', {}).get('videos', [])
            }
            if not media['images']:
                media = {
                    'images': details.get('media', {}).get('images', []),
                    'videos': details.get('media', {}).get('videos', [])
                }
        except:
            media = {}
        return Concept.objects.get_or_create(
            concept_id=details.get('id'),
            defaults={
                'unified_title': details.get('nameEn', ''),
                'publisher_name': details.get('publisherName', ''),
                'genres': details.get('genres', []),
                'subgenres': details.get('subGenres', []),
                'descriptions': {
                    'short': descriptions_short,
                    'long': descriptions_long
                },
                'content_rating': details.get('contentRating', {}),
                'media': media
            })
    
    @classmethod
    def update_profile_game_with_title_stats(cls, profile: Profile, title_stats: TitleStats):
        games = Game.objects.filter(title_ids__contains=title_stats.title_id)        
        if games:
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
            return True
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
                "trophy_name": trophy_data.trophy_name.strip(),
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
            trophy.trophy_name = trophy_data.trophy_name.strip()
            trophy.trophy_detail = trophy_data.trophy_detail
            trophy.trophy_icon_url = trophy_data.trophy_icon_url
            trophy.trophy_group_id = trophy_data.trophy_group_id
            trophy.progress_target_value = trophy_data.trophy_progress_target_value
            trophy.reward_name = trophy_data.trophy_reward_name
            trophy.reward_img_url = trophy_data.trophy_reward_img_url
            trophy.trophy_rarity = trophy_data.trophy_rarity.value if trophy_data.trophy_rarity else ''
            trophy.trophy_earn_rate = trophy_data.trophy_earn_rate if trophy_data.trophy_earn_rate else 0.0
            trophy.save()
        
        if trophy.trophy_type == 'platinum':
            game.update_is_shovelware(trophy.trophy_earn_rate)
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
                "earned_date_time": trophy_data.earned_date_time
            },
        )

        notify = False
        if (created and trophy_data.earned == True) or ((not created) and earned_trophy.earned == False and trophy_data.earned == True):
            trophy.increment_earned_count()
            threshold = timezone.now() - timedelta(days=2)
            notify = profile.discord_id and earned_trophy.trophy.trophy_type == 'platinum' and trophy_data.earned_date_time >= threshold


        if not created:
            earned_trophy.earned = trophy_data.earned
            earned_trophy.trophy_hidden = trophy_data.trophy_hidden
            earned_trophy.progress = trophy_data.progress
            earned_trophy.progress_rate = trophy_data.progress_rate
            earned_trophy.progressed_date_time = trophy_data.progressed_date_time
            earned_trophy.earned_date_time = trophy_data.earned_date_time
            earned_trophy.save()

        if notify:
            earned_trophy.refresh_from_db()
            cls._notify_new_platinum(profile, earned_trophy)

        return earned_trophy, created
    
    @classmethod
    def _notify_new_platinum(self, profile: Profile, earned_trophy: EarnedTrophy):
        """Send Discord webhook embed for new platinum."""
        try:
            platinum_emoji = f"<:Platinum_Trophy:{PLATINUM_EMOJI_ID}>" if PLATINUM_EMOJI_ID else "🏆"
            plat_pursuit_emoji = f"<:PlatPursuit:{PLAT_PURSUIT_EMOJI_ID}>" if PLAT_PURSUIT_EMOJI_ID else "🏆"
            embed_data = {
                'title': f"🎉 New Platinum for {profile.display_psn_username}!",
                'description': f"{plat_pursuit_emoji} <@{profile.discord_id}> has earned a shiny new platinum!\n{platinum_emoji} *{earned_trophy.trophy.trophy_name}* in **{earned_trophy.trophy.game.title_name}**\n🌟 {earned_trophy.trophy.trophy_earn_rate}% (PSN)",
                'color': 0x003791,
                'thumbnail': {'url': earned_trophy.trophy.trophy_icon_url},
                'footer': {'text': f"Powered by Plat Pursuit | Earned: {earned_trophy.earned_date_time.strftime('%Y-%m-%d')}"}
            }
            payload = {'embeds': [embed_data]}
            response = requests.post(DISCORD_PLATINUM_WEBHOOK_URL, json=payload)
            response.raise_for_status()
            logger.info(f"Sent notification of new platinum for {profile.psn_username}")
        except requests.RequestException as e:
            logger.error(f"Webhook notification failed: {e}")

    @classmethod
    def get_profile_trophy_summary(cls, profile: Profile):
        return {
            'total': EarnedTrophy.objects.filter(profile=profile, earned=True).count(),
            'bronze': EarnedTrophy.objects.filter(profile=profile, earned=True, trophy__trophy_type='bronze').count(),
            'silver': EarnedTrophy.objects.filter(profile=profile, earned=True, trophy__trophy_type='silver').count(),
            'gold': EarnedTrophy.objects.filter(profile=profile, earned=True, trophy__trophy_type='gold').count(),
            'platinum': EarnedTrophy.objects.filter(profile=profile, earned=True, trophy__trophy_type='platinum').count(),
        }
    
    @classmethod
    def get_tracked_trophies_for_game(cls, profile: Profile, np_comm_id: str):
        try:
            game = Game.objects.get(np_communication_id=np_comm_id)
        except Game.DoesNotExist:
            logger.error(f"Could not find game {np_comm_id}")
            return

        trophies = {
            'total': EarnedTrophy.objects.filter(profile=profile, trophy__game=game, earned=True).count(),
            'bronze': EarnedTrophy.objects.filter(profile=profile, trophy__game=game, earned=True, trophy__trophy_type='bronze').count(),
            'silver': EarnedTrophy.objects.filter(profile=profile, trophy__game=game, earned=True, trophy__trophy_type='silver').count(),
            'gold': EarnedTrophy.objects.filter(profile=profile, trophy__game=game, earned=True, trophy__trophy_type='gold').count(),
            'platinum': EarnedTrophy.objects.filter(profile=profile, trophy__game=game, earned=True, trophy__trophy_type='platinum').count(),
        }

        return game, trophies