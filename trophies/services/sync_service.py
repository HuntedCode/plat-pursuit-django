"""
Profile synchronization service.

This module handles the coordination of PSN profile synchronization,
including cooldown management, sync eligibility checks, and sync initiation.
"""
from datetime import timedelta
from django.utils import timezone
from typing import Tuple, Optional


class SyncService:
    """Coordinates profile synchronization with PSN."""

    # Sync cooldown periods
    PREFERRED_COOLDOWN = timedelta(minutes=5)
    STANDARD_COOLDOWN = timedelta(hours=1)

    @staticmethod
    def can_sync(profile) -> Tuple[bool, Optional[str]]:
        """
        Check if a profile is eligible for synchronization.

        Checks cooldown period based on sync tier:
        - Preferred tier: 5 minute cooldown
        - Standard tier: 1 hour cooldown

        Args:
            profile: Profile instance to check

        Returns:
            Tuple[bool, Optional[str]]: (can_sync, reason_if_not)
                - (True, None) if sync is allowed
                - (False, "reason") if sync is blocked

        Example:
            >>> can_sync, reason = SyncService.can_sync(profile)
            >>> if not can_sync:
            ...     print(f"Cannot sync: {reason}")
        """
        if not profile.last_synced:
            return True, None

        cooldown = (
            SyncService.PREFERRED_COOLDOWN
            if profile.sync_tier == 'preferred'
            else SyncService.STANDARD_COOLDOWN
        )

        next_sync_time = profile.last_synced + cooldown
        if next_sync_time > timezone.now():
            seconds_left = (next_sync_time - timezone.now()).total_seconds()
            return False, f"Cooldown active: {int(seconds_left)} seconds remaining"

        return True, None

    @staticmethod
    def get_seconds_to_next_sync(profile) -> int:
        """
        Calculate seconds remaining until next sync is allowed.

        Args:
            profile: Profile instance

        Returns:
            int: Seconds until next sync allowed, or 0 if sync is available now

        Example:
            >>> seconds = SyncService.get_seconds_to_next_sync(profile)
            >>> if seconds > 0:
            ...     print(f"Wait {seconds} seconds before syncing")
        """
        if not profile.last_synced:
            return 0

        cooldown = (
            SyncService.PREFERRED_COOLDOWN
            if profile.sync_tier == 'preferred'
            else SyncService.STANDARD_COOLDOWN
        )

        next_sync_time = profile.last_synced + cooldown
        if next_sync_time > timezone.now():
            return int((next_sync_time - timezone.now()).total_seconds())

        return 0

    @staticmethod
    def get_time_since_last_sync(profile) -> timedelta:
        """
        Get time elapsed since last successful sync.

        Args:
            profile: Profile instance

        Returns:
            timedelta: Time since last sync, or 0 if never synced

        Example:
            >>> delta = SyncService.get_time_since_last_sync(profile)
            >>> print(f"Last synced {delta.total_seconds()} seconds ago")
        """
        if profile.last_synced:
            return timezone.now() - profile.last_synced
        return timedelta(0)

    @staticmethod
    def initiate_sync(profile) -> bool:
        """
        Attempt to initiate profile synchronization via job queue.

        Checks cooldown period and queues sync job if eligible.

        Args:
            profile: Profile instance to sync

        Returns:
            bool: True if sync was initiated, False if cooldown active

        Example:
            >>> if SyncService.initiate_sync(profile):
            ...     print("Sync started")
            ... else:
            ...     print("Sync blocked by cooldown")
        """
        can_sync, reason = SyncService.can_sync(profile)
        if not can_sync:
            return False

        # Import here to avoid circular dependency
        from trophies.psn_manager import PSNManager
        PSNManager.profile_refresh(profile)
        return True

    @staticmethod
    def get_cooldown_duration(profile) -> timedelta:
        """
        Get the cooldown duration for a profile based on sync tier.

        Args:
            profile: Profile instance

        Returns:
            timedelta: Cooldown duration (5 minutes for preferred, 1 hour for standard)

        Example:
            >>> cooldown = SyncService.get_cooldown_duration(profile)
            >>> print(f"Cooldown: {cooldown.total_seconds()} seconds")
        """
        return (
            SyncService.PREFERRED_COOLDOWN
            if profile.sync_tier == 'preferred'
            else SyncService.STANDARD_COOLDOWN
        )
