import logging
import difflib
from celery import current_task, shared_task
from tenacity import (
    retry,
    wait_exponential,
    stop_after_attempt,
    retry_if_exception_type,
)
from requests.exceptions import HTTPError
from .models import Profile, Game
from .psn_manager import PSNManager, BasePSNTask
from .services import PsnApiService

logger = logging.getLogger("psn_api")

@shared_task(bind=True, base=BasePSNTask)
@retry(
    retry=retry_if_exception_type(HTTPError),
    wait=wait_exponential(multiplier=1, min=4, max=60),
    stop=stop_after_attempt(3),
)
def initial_sync(self, profile_id):
    """High-priority task: Sync initial profile data and top 10 recent games, queue the rest."""
    try:
        profile = Profile.objects.get(id=profile_id)
    except Profile.DoesNotExist as e:
        logger.error(f"Profile with id {profile_id} does not exist.")
        raise

    logger.info(
        f"Starting initial sync for profile {profile.psn_username} ({profile.id})"
    )

    manager = PSNManager()
    try:
        instance = manager.get_instance_for_job("initial_sync", profile.id)
    except ValueError:
        logger.warning(f"No instance available for initial_sync (profile {profile_id}) - deferring")
        manager.defer_job(profile_id, "initial_sync", [profile_id], 'high')
        return None

    # Sync profile
    profile = _sync_profile_data(instance, profile)
    logger.info(
        f"Synced profile data for profile {profile.psn_username} ({profile.account_id})"
    )

    # Sync Games & ProfileGames
    is_full = True
    limit = 200
    offset = 0
    trophy_titles = []
    title_stats = []
    profile_game_comm_ids = []
    while is_full:
        titles, is_full = manager.call_user_trophy_titles(
            instance, profile, limit, offset
        )
        trophy_titles.extend(list(titles))
        stats, _ = manager.call_user_title_stats(instance, profile, limit, offset)
        title_stats.extend(list(stats))
        title_stats, _ = _sync_profile_games_data(profile, trophy_titles, title_stats)

        for title in trophy_titles:
            profile_game_comm_ids.append(title.np_communication_id)
        trophy_titles = []
        offset += limit
    _sync_profile_games_title_stats(profile, title_stats)
    logger.info(
        f"Synced {len(profile_game_comm_ids)} games for profile {profile.psn_username} ({profile.account_id})"
    )

    # Sync Trophies and EarnedTrophies
    # First 10 - high prio override
    n_high_prio_games = min(10, len(profile_game_comm_ids))
    for np_comm_id in profile_game_comm_ids[:n_high_prio_games]:
        manager.assign_job(
            job_type="sync_game_trophies",
            args=[profile.id, np_comm_id, instance.instance_id],
            profile_id=profile.id,
            priority_override="high",
        )
    logger.info(
        f"Most recent {n_high_prio_games} queue in high-priority for profile {profile.psn_username} ({profile.account_id})"
    )

    for np_comm_id in profile_game_comm_ids[n_high_prio_games:]:
        manager.assign_job(
            job_type="sync_game_trophies",
            args=[profile.id, np_comm_id, instance.instance_id],
            profile_id=profile.id,
        )
    logger.info(
        f"Remaining {len(profile_game_comm_ids) - n_high_prio_games} games for profile {profile.psn_username} ({profile.account_id}) batched and jobs created"
    )
    logger.info(
        f"Profile {profile.psn_username} ({profile.account_id}) initial sync successful and trophies batched"
    )
    manager.complete_job(profile.id, current_task.request.queue, instance.instance_id)

@shared_task(bind=True, base=BasePSNTask)
@retry(
    retry=retry_if_exception_type(HTTPError),
    wait=wait_exponential(multiplier=1, min=4, max=60),
    stop=stop_after_attempt(3),
)
def sync_game_trophies(self, profile_id, np_comm_id):
    """Medium-priority task: Sync user trophies for a batch of games (up to 20)."""
    try:
        profile = Profile.objects.get(id=profile_id)
        game = Game.objects.get(np_communication_id=np_comm_id)
    except Profile.DoesNotExist:
        logger.error(f"Profile with id {profile_id} does not exist.")
        raise
    except Game.DoesNotExist:
        logger.error(f"Game with np_communication_id {np_comm_id} does not exist.")
        raise

    logger.info(
        f"Syncing {np_comm_id} trophy data for profile {profile.psn_username} ({profile.id})"
    )

    manager = PSNManager()
    try:
        instance = manager.get_instance_for_job("initial_sync", profile.id)
    except ValueError:
        logger.warning(f"No instance available for initial_sync (profile {profile_id}) - deferring")
        manager.defer_job(profile_id, "initial_sync", [profile_id], 'high')
        return None
    _sync_profile_trophy_data(manager, instance, profile, game) 
    logger.info(f"Trophies for {game.title_name} ({game.np_communication_id}) sync'd for profile {profile.psn_username} ({profile.account_id}) successfully")
    manager.complete_job(profile.id, current_task.request.queue, instance.instance_id)

@shared_task(bind=True, base=BasePSNTask)
@retry(
    retry=retry_if_exception_type(HTTPError),
    wait=wait_exponential(multiplier=1, min=4, max=60),
    stop=stop_after_attempt(3),
)
def profile_refresh(self, profile_id, priority="high"):
    """High-priority task: Refresh profile data, check game/trophy deltas and sync whats needed."""
    try:
        profile = Profile.objects.get(id=profile_id)
    except Profile.DoesNotExist as e:
        logger.error(f"Profile with id {profile_id} does not exist.")
        raise

    logger.info(
        f"Profile refresh initiated for {profile.psn_username} ({profile.account_id})"
    )

    manager = PSNManager()
    try:
        instance = manager.get_instance_for_job("initial_sync", profile.id)
    except ValueError:
        logger.warning(f"No instance available for initial_sync (profile {profile_id}) - deferring")
        manager.defer_job(profile_id, "initial_sync", [profile_id], priority)
        return None
    latest_sync = profile.last_synced

    # Sync Profile Data
    profile = _sync_profile_data(instance, profile)
    logger.info(
        f"Synced profile data for profile {profile.psn_username} ({profile.account_id})"
    )

    # Sync game data
    trophy_titles, _ = manager.call_user_trophy_titles(instance, profile, limit=200)
    title_stats, _ = manager.call_user_title_stats(instance, profile, limit=200)
    trophy_titles_to_be_updated = []
    title_stats_to_be_updated = []
    for title in trophy_titles:
        if title.get("last_updated_datetime") >= latest_sync:
            trophy_titles_to_be_updated.append(title)
    for stats in title_stats:
        if stats.get("last_played_date_time") > latest_sync:
            title_stats_to_be_updated.append(stats)

    remaining_title_stats, _ = _sync_profile_games_data(
        profile, title_stats_to_be_updated, title_stats_to_be_updated
    )
    _sync_profile_games_title_stats(profile, remaining_title_stats)
    logger.info(
        f"Synced {len(trophy_titles_to_be_updated) + len(remaining_title_stats)} games for profile {profile.psn_username} ({profile.account_id})"
    )

    np_comm_ids = []
    for title in trophy_titles_to_be_updated:
        np_comm_ids.append(title.np_communication_id)

    # Sync trophy data
    for np_comm_id in np_comm_ids:
        manager.assign_job(
            "sync_game_trophies",
            [profile, np_comm_id, instance.instance_id],
            profile_id=profile.id,
            priority_override=priority,
        )
    logger.info(
        f"{len(np_comm_ids)} games of trophy data to be sync'd for {profile.psn_username} ({profile.account_id}) has been batched."
    )

    logger.info(
        f"Profile {profile.psn_username} ({profile.account_id}) has been refreshed successfully!"
    )
    manager.complete_job(profile.id, current_task.request.queue, instance.instance_id)

def _sync_profile_data(manager : PSNManager, instance, profile: Profile):
    """Sync profile data with stats from profile_legacy."""
    try:
        legacy = manager.call_user_profile_legacy(instance, profile)
        profile = PsnApiService.update_profile_from_legacy(profile, legacy)
        return profile
    except HTTPError as e:
        logger.error(
            f"Failed profile sync for {profile.psn_username} ({profile.account_id}): {e}"
        )
        raise

def _sync_profile_games_data(profile: Profile, trophy_titles: list, title_stats: list):
    """Match trophy_titles with title_stats by name/platform, sync, return unmatched title_stats and games needing trophy updates."""
    games_needing_trophy_updates = []

    for trophy_title in trophy_titles:
        for i, title_stat in enumerate(title_stats):
            if (
                _match_game_names(trophy_title.title_name, title_stat.name)
                and str(title_stat.category.name) in [platform.value for platform in trophy_title.title_platform]
            ):
                game, created, needs_trophy_update = (
                    PsnApiService.create_or_update_game_from_title(
                        trophy_title, title_stat
                    )
                )
                if needs_trophy_update and not created:
                    games_needing_trophy_updates.append(game)
                profile_game, _ = (
                    PsnApiService.create_or_update_profile_game_from_title(
                        profile, game, trophy_title, title_stat
                    )
                )
                title_stats.pop(i)
                break

        game, created, needs_trophy_update = (
            PsnApiService.create_or_update_game_from_title(trophy_title)
        )
        if needs_trophy_update and not created:
            games_needing_trophy_updates.append(game)
        profile_game, _ = PsnApiService.create_or_update_profile_game_from_title(
            profile, game, trophy_title
        )
    return title_stats, games_needing_trophy_updates

def _match_game_names(name1, name2, threshold=0.9):
    """Fuzzy match game names with normalization."""
    name1 = name1.lower().replace('™', '').replace('®', '').strip()
    name2 = name2.lower().replace('™', '').replace('®', '').strip()
    ratio = difflib.SequenceMatcher(None, name1, name2).ratio()
    return ratio >= threshold

def _sync_profile_games_title_stats(profile: Profile, title_stats: list):
    """Update ProfileGame with only title_stats - no trophy data."""
    for stat in title_stats:
        try:
            game = Game.objects.get(title_id=stat.title_id)
        except Game.DoesNotExist as e:
            logger.warning(f"Game with title id {stat.title_id} could not be found.")
            continue
        PsnApiService.update_game_from_title_stats(profile, game, stat)

def _sync_profile_trophy_data(
    manager: PSNManager, instance, profile: Profile, game: Game
):
    """Sync Trophy & EarnedTrophy models for specified Game & Profile."""
    try:
        trophies_data, _ = manager.call_user_trophies(instance, profile, game)
        for trophy_data in trophies_data:
            trophy, _ = PsnApiService.create_or_update_trophy_from_trophy_data(game, trophy_data)
            earned_trophy, _ = (
                PsnApiService.create_or_update_earned_trophy_from_trophy_data(
                    profile, trophy, trophy_data
                )
            )
        logger.info(
            f"Synced trophies for {game.title_name} ({game.np_communication_id} for profile {profile.psn_username} ({profile.account_id}))"
        )
    except HTTPError as e:
        logger.error(
            f"Failed trophy sync for game {game.title_name} ({game.np_communication_id}) for profile {profile.psn_username} ({profile.account_id}): {e}"
        )