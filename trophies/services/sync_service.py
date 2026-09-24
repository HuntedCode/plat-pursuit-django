"""
Profile synchronization service.

This module handles the coordination of PSN profile synchronization,
including cooldown management, sync eligibility checks, and sync initiation.
"""
import math
from datetime import timedelta
from django.utils import timezone
from typing import NamedTuple, Tuple, Optional


class RefreshOutcome(NamedTuple):
    """The result of asking for a profile refresh, and what to tell the asker.

    `reason` is machine-readable so a caller can pick an HTTP status; `message` is the sentence a
    person reads, and it lives here rather than at each call site on purpose (see
    `SyncService.request_refresh`).
    """
    ok: bool
    reason: str      # '' when ok, else SyncService.REFUSED_*
    message: str
    seconds: int     # seconds until the next refresh is possible; 0 when unknown or available now


def _cooldown_phrase(seconds: int) -> str:
    """"40 minutes" / "35 seconds", for a sentence a person reads.

    Deliberately coarse. `seconds` rides on the same payload precisely so a client can render a
    ticking countdown instead of this; that is what the profile-page control does with it.
    """
    if seconds <= 0:
        return 'a moment'   # not reachable from a refusal, but "0 seconds" would be nonsense if it were
    if seconds < 60:
        return '1 second' if seconds == 1 else f'{seconds} seconds'
    minutes = math.ceil(seconds / 60)
    return '1 minute' if minutes == 1 else f'{minutes} minutes'


class SyncService:
    """Coordinates profile synchronization with PSN."""

    # Sync cooldown periods
    PREFERRED_COOLDOWN = timedelta(minutes=5)
    STANDARD_COOLDOWN = timedelta(hours=1)

    #: The vocabulary of `reason` on a refused refresh, for a client to switch on. Callers map
    #: these to an HTTP status. `request_refresh` only ever emits the first two -- the rate limit
    #: belongs to the view layer -- but they share one namespace because they share one wire
    #: field, and splitting the list across two modules is how a client handles two of three.
    REFUSED_OUTAGE = 'outage'
    REFUSED_COOLDOWN = 'cooldown'
    REFUSED_RATE_LIMIT = 'rate_limited'

    #: Refusing an ANONYMOUS caller a refresh of a hunter we already track. Not on the service --
    #: it never sees a request -- but in the same vocabulary, because it arrives in the same `reason`
    #: field and a client wants one list to switch on.
    REFUSED_SIGN_IN = 'sign_in'

    #: Not a refusal: a sync was ALREADY running, so what the asker wants is happening and nothing
    #: new was queued. Rides on `ok=True` with a reason, because the honest answer is yes-but-not-
    #: because-of-you, and a client should show progress rather than either an error or a fresh
    #: 'queued' beat.
    ALREADY_SYNCING = 'already_syncing'

    #: The one wording for a PSN outage on a sync path. It said "Please try again later" in one view
    #: and "Syncs will resume automatically when PSN recovers" in another; the second is the one worth
    #: keeping, because it tells a hunter not to sit there retrying. (`profile_views` and `api/views`
    #: still carry their own copies; those are other surfaces and not this branch's business.)
    OUTAGE_MESSAGE = (
        'PlayStation Network is currently unavailable. '
        'Syncs will resume automatically when PSN recovers.'
    )

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
    def initiate_sync(profile, jump_queue: bool = False) -> bool:
        """
        Attempt to initiate profile synchronization via job queue.

        Checks cooldown period and queues sync job if eligible.

        Args:
            profile: Profile instance to sync
            jump_queue: Serve this ahead of the rest of its queue, because a human is waiting.
                See `PSNManager.assign_job` for what that does and does not buy.

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
        PSNManager.profile_refresh(profile, jump_queue=jump_queue)
        return True

    @staticmethod
    def request_refresh(profile, jump_queue: bool = False) -> RefreshOutcome:
        """Try to refresh `profile`, and say what happened.

        THE one place that decides whether a refresh can happen and what the asker is told. It exists
        because three callers each decided that independently and one of them got it wrong:
        `SearchSyncProfileView` discarded `attempt_sync()`'s return value and reported
        `{'success': True}` for a refresh the cooldown had refused, while `TriggerSyncView` twelve
        lines above it did the same check correctly. Routing every caller through one function is
        what stops the next one being wrong in a fourth way.

        The cooldown it consults is already a PER-PROFILE throttle anchored on `last_synced`, not a
        per-caller one, so concurrent askers coalesce into one refresh for free and no separate
        request-level lock is needed.

        Args:
            profile: the Profile to refresh.
            jump_queue: serve it ahead of the rest of its queue, because a human is waiting.
                See `PSNManager.assign_job` for what that does and does not buy.

        Returns:
            RefreshOutcome: `ok` with an empty reason when a refresh was queued; `ok` with
                `ALREADY_SYNCING` when one was already running; not-ok with `REFUSED_COOLDOWN` or
                `REFUSED_OUTAGE`. `seconds` is meaningful only on a cooldown refusal -- on the other
                branches the caller has progress to show, not a countdown to run.
        """
        from trophies.psn_manager import PSNManager   # circular import at module scope

        if PSNManager.is_psn_outage_active():
            return RefreshOutcome(False, SyncService.REFUSED_OUTAGE, SyncService.OUTAGE_MESSAGE, 0)

        # Checked BEFORE `initiate_sync`, because `PSNManager.profile_refresh` has branches for
        # 'error' and 'synced' only: on 'syncing' it falls off the end returning None while
        # `initiate_sync` returns True regardless, so this used to answer "Refreshing now." for a job
        # nobody queued. Reachable rather than theoretical -- `last_synced` is stamped at the START of
        # a sync, so a sync running longer than the cooldown leaves the cooldown open while the status
        # is still 'syncing', and a profile stuck in 'syncing' is a state the worker has a rescuer for.
        if profile.sync_status == 'syncing':
            return RefreshOutcome(True, SyncService.ALREADY_SYNCING, 'Already refreshing.', 0)

        if SyncService.initiate_sync(profile, jump_queue=jump_queue):
            return RefreshOutcome(True, '', 'Refreshing now.', 0)

        seconds = SyncService.get_seconds_to_next_sync(profile)
        return RefreshOutcome(
            False,
            SyncService.REFUSED_COOLDOWN,
            f'Synced recently. You can refresh again in {_cooldown_phrase(seconds)}.',
            seconds,
        )

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
