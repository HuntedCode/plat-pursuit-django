"""
Sync pipeline utilities for performance optimization.

Provides thread-local context managers to suppress expensive signal
handlers that are redundant during the sync pipeline (where notifications
are handled separately via DeferredNotificationService).
"""
import threading
from contextlib import contextmanager

_sync_context = threading.local()


@contextmanager
def sync_signal_suppressor():
    """
    Context manager to suppress the EarnedTrophy pre_save signal during sync.

    The pre_save signal (`capture_earned_trophy_previous_state`) fires a SELECT
    query for every EarnedTrophy.save() to track the previous `earned` value
    for notification detection. During sync, this is unnecessary because:

    1. Platinum notifications are handled via DeferredNotificationService
       (queued to Redis, created after game sync completes)
    2. The earned-flip detection is handled directly in
       create_or_update_earned_trophy_from_trophy_data()

    Usage in token_keeper.py _job_sync_trophies:
        with sync_signal_suppressor():
            for trophy_data in batch:
                ...
    """
    _sync_context.suppress_earned_trophy_presave = True
    try:
        yield
    finally:
        _sync_context.suppress_earned_trophy_presave = False


def is_sync_signal_suppressed():
    """Check if the sync signal suppressor is active on this thread."""
    return getattr(_sync_context, 'suppress_earned_trophy_presave', False)
