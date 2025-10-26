import logging
import threading
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

logger = logging.getLogger("psn_api")

class InstanceNotAvailableError(Exception):
    pass

@shared_task(bind=True, base=BasePSNTask)
@retry(
    retry=retry_if_exception_type((HTTPError, TimeoutError)),
    wait=wait_exponential(multiplier=1, min=4, max=60),
    stop=stop_after_attempt(3),
)
def initial_sync(self, profile_id, queue_name="high_priority"):
    """High-priority task: Sync initial profile data and top 10 recent games, queue the rest."""
    logger.info(f"Processing task in thread {threading.current_thread().name}")
    try:
        profile = Profile.objects.get(id=profile_id)
    except Profile.DoesNotExist:
        logger.error(f"Profile {profile_id} does not exist.")
    logger.info(f"Starting initial sync for profile {profile.id}")

    # Sync Profile
    manager = PSNManager()
    result = manager.publish_request(profile, 'initial_sync', 'sync_profile_data')
    logger.info(f"Synced profile data for profile {profile.id}")

    # Sync Games & ProfileGames
    result = manager.publish_request(profile, 'initial_sync', 'sync_profile_games_data')
    profile_game_comm_ids = result['game_ids']

    # Sync Trophies and EarnedTrophies
    # First 10 - high prio override
    n_high_prio_games = min(10, len(profile_game_comm_ids))
    for np_comm_id in profile_game_comm_ids[:n_high_prio_games]:
        manager.assign_job(
            job_type="sync_game_trophies",
            args=[profile.id, np_comm_id],
            profile_id=profile.id,
            priority_override="high",
        )
    logger.info(f"Most recent {n_high_prio_games} queue in high-priority for profile {profile.id}")

    for np_comm_id in profile_game_comm_ids[n_high_prio_games:]:
        manager.assign_job(
            job_type="sync_game_trophies",
            args=[profile.id, np_comm_id],
            profile_id=profile.id,
        )
    logger.info(f"Remaining {len(profile_game_comm_ids) - n_high_prio_games} games for profile {profile.id} batched and jobs created")
    logger.info(f"Profile {profile.id} initial sync successful and trophies batched")
    manager.complete_job(profile.id, queue_name)

@shared_task(bind=True, base=BasePSNTask)
@retry(
    retry=retry_if_exception_type((HTTPError, TimeoutError)),
    wait=wait_exponential(multiplier=1, min=4, max=60),
    stop=stop_after_attempt(3),
)
def sync_game_trophies(self, profile_id, np_comm_id, queue_name="medium_priority"):
    """Medium-priority task: Sync user trophies for a batch of games (up to 20)."""
    logger.info(f"Processing task in thread {threading.current_thread().name}")
    try:
        profile = Profile.objects.get(id=profile_id)
        game = Game.objects.get(np_communication_id=np_comm_id)
    except Profile.DoesNotExist:
        logger.error(f"Profile with id {profile_id} does not exist.")
        raise
    except Game.DoesNotExist:
        logger.error(f"Game with np_communication_id {np_comm_id} does not exist.")
        raise
    logger.info(f"Syncing {np_comm_id} trophy data for profile {profile.id}")

    manager = PSNManager()
    result = manager.publish_request(profile, 'sync_trophies', 'sync_profile_trophies', args=[profile_id, np_comm_id])
    logger.info(f"Trophies for {np_comm_id} sync'd for profile {profile.id} successfully")
    manager.complete_job(profile.id, queue_name)

@shared_task(bind=True, base=BasePSNTask)
@retry(
    retry=retry_if_exception_type(HTTPError),
    wait=wait_exponential(multiplier=1, min=4, max=60),
    stop=stop_after_attempt(3),
)
def profile_refresh(self, profile_id, priority="high", queue_name="high_priority"):
    """High-priority task: Refresh profile data, check game/trophy deltas and sync whats needed."""
    try:
        profile = Profile.objects.get(id=profile_id)
    except Profile.DoesNotExist as e:
        logger.error(f"Profile with id {profile_id} does not exist.")
        raise
    logger.info(f"Profile refresh initiated for {profile.id}")

    manager = PSNManager()
    latest_sync = profile.last_synced
    job_type = 'profile_refresh' if priority == 'high' else 'manual_refresh'

    # Sync Profile Data
    result = manager.publish_request(profile, job_type, 'sync_profile_data', args=[latest_sync])
    logger.info(f"Synced profile data for profile {profile.id}")

    # Refresh Game Data
    result = manager.publish_request(profile, job_type, 'refresh_profile_game_data')
    profile_game_comm_ids = result['game_ids']

    # Sync trophy data
    for np_comm_id in profile_game_comm_ids:
        manager.assign_job(
            "sync_game_trophies",
            [profile, np_comm_id],
            profile_id=profile.id,
            priority_override=priority,
        )
    logger.info(f"{len(profile_game_comm_ids)} games of trophy data to be sync'd for {profile.id} has been batched.")

    logger.info(f"Profile {profile.id} has been refreshed successfully!")
    manager.complete_job(profile.id, queue_name)