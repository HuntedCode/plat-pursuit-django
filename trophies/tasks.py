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
    result = manager.publish_request(profile, 'initial_sync', 'sync_profile_data', timeout=120)
    logger.info(f"Synced profile data for profile {profile.id}")

    # Sync Games & ProfileGames
    result = manager.publish_request(profile, 'initial_sync', 'sync_profile_games_data', timeout=999999)
    profile_game_comm_ids = result['game_ids']
    remaining_title_stats = result['title_stats']

    for np_comm_id in profile_game_comm_ids:
        manager.assign_job(
            job_type="sync_game_trophies",
            args=[profile.id, np_comm_id],
            profile_id=profile.id,
        )
    logger.info(f"{len(profile_game_comm_ids)} games for profile {profile.id} queued.")

    page_size = 5
    limit = page_size
    offset = 0
    while offset < len(remaining_title_stats):
        manager.assign_job(
            job_type="sync_title_stats_by_title",
            args=[profile.id, remaining_title_stats[offset:limit]],
            profile_id=profile.id,
        )
        limit += page_size
        offset += page_size
    logger.info (f"Remaining {len(remaining_title_stats)} title stats by title queued.")

    logger.info(f"Profile {profile.id} initial sync successful!")
    manager.complete_job(profile.id, queue_name)

@shared_task(bind=True, base=BasePSNTask)
@retry(
    retry=retry_if_exception_type((HTTPError, TimeoutError)),
    wait=wait_exponential(multiplier=1, min=4, max=60),
    stop=stop_after_attempt(3),
)
def initial_sync_no_trophies(self, profile_id, queue_name="high_priority"):
    """High-priority task: Sync initial profile data and top 10 recent games, queue the rest."""
    logger.info(f"Processing task in thread {threading.current_thread().name}")
    try:
        profile = Profile.objects.get(id=profile_id)
    except Profile.DoesNotExist:
        logger.error(f"Profile {profile_id} does not exist.")
    logger.info(f"Starting initial sync for profile {profile.id}")

    # Sync Profile
    manager = PSNManager()
    result = manager.publish_request(profile, 'initial_sync', 'sync_profile_data', timeout=1200)
    logger.info(f"Synced profile data for profile {profile.id}")

    # Sync Games & ProfileGames
    result = manager.publish_request(profile, 'initial_sync', 'sync_profile_games_data')
    profile_game_comm_ids = result['game_ids']

    logger.info(f"Profile {profile.id} initial sync successful")
    manager.complete_job(profile.id, queue_name)

@shared_task(bind=True, base=BasePSNTask)
@retry(
    retry=retry_if_exception_type((HTTPError, TimeoutError)),
    wait=wait_exponential(multiplier=1, min=4, max=60),
    stop=stop_after_attempt(3),
)
def sync_game_trophies(self, profile_id, np_comm_id, queue_name="low_priority"):
    """Medium-priority task: Sync user trophies for a batch of games (up to 20)."""
    logger.info(f"Processing task in thread {threading.current_thread().name}")
    try:
        profile = Profile.objects.get(id=profile_id)
    except Profile.DoesNotExist:
        logger.error(f"Profile with id {profile_id} does not exist.")
        raise
    except Game.DoesNotExist:
        logger.error(f"Game with np_communication_id {np_comm_id} does not exist.")
        raise
    logger.info(f"Syncing {np_comm_id} trophy data for profile {profile.id}")

    manager = PSNManager()
    result = manager.publish_request(profile, 'sync_game_trophies', 'sync_profile_trophies', args=[np_comm_id])
    logger.info(f"Trophies for {np_comm_id} sync'd for profile {profile.id} successfully")
    manager.complete_job(profile.id, queue_name)

@shared_task(bind=True, base=BasePSNTask)
@retry(
    retry=retry_if_exception_type((HTTPError, TimeoutError)),
    wait=wait_exponential(multiplier=1, min=4, max=60),
    stop=stop_after_attempt(3),
)
def sync_title_stats_by_title(self, profile_id, title_stats, queue_name="low_priority"):
    """Medium-priority task: Sync user trophies for a batch of games (up to 20)."""
    logger.info(f"Processing task in thread {threading.current_thread().name}")
    try:
        profile = Profile.objects.get(id=profile_id)
    except Profile.DoesNotExist:
        logger.error(f"Profile with id {profile_id} does not exist.")
        raise
    logger.info(f"Syncing {len(title_stats)} title stats by title for profile {profile.id}")

    manager = PSNManager()
    result = manager.publish_request(profile, 'sync_title_stats_by_title', 'sync_trophy_titles_for_title', args=[title_stats])
    logger.info(f"{len(title_stats)} title stats by title sync'd for profile {profile.id} successfully")
    manager.complete_job(profile.id, queue_name)

@shared_task(bind=True, base=BasePSNTask, queue='high_priority')
@retry(
    retry=retry_if_exception_type((HTTPError, TimeoutError)),
    wait=wait_exponential(multiplier=1, min=4, max=60),
    stop=stop_after_attempt(3),
)
def profile_refresh(self, profile_id, trophy_priority="medium", queue_name="high_priority"):
    """High-priority task: Refresh profile data, check game/trophy deltas and sync whats needed."""
    try:
        profile = Profile.objects.get(id=profile_id)
    except Profile.DoesNotExist as e:
        logger.error(f"Profile with id {profile_id} does not exist.")
        raise
    logger.info(f"Profile refresh initiated for {profile.id}")

    manager = PSNManager()
    latest_sync = profile.last_synced.timestamp()
    job_type = 'profile_refresh' if trophy_priority == "medium" else 'manual_refresh'

    # Sync Profile Data
    result = manager.publish_request(profile, job_type, 'sync_profile_data', timeout=120)
    logger.info(f"Synced profile data for profile {profile.id}")

    # Refresh Game Data
    result = manager.publish_request(profile, job_type, 'refresh_profile_game_data', timeout=999999, args=[latest_sync])
    profile_game_comm_ids = result['game_ids']

    # Sync trophy data
    for np_comm_id in profile_game_comm_ids:
        manager.assign_job(
            "sync_game_trophies",
            [profile.id, np_comm_id],
            profile_id=profile.id,
            priority_override=trophy_priority,
        )
    logger.info(f"{len(profile_game_comm_ids)} games of trophy data to be sync'd for {profile.id} has been batched.")

    logger.info(f"Profile {profile.id} has been refreshed successfully!")
    manager.complete_job(profile.id, queue_name)