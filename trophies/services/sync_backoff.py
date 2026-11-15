"""Exponential backoff for auto-retrying a profile whose syncs keep failing.

Why it exists: `refresh_profiles` picks profiles by `last_synced` age, and a sync that fails on its
FIRST PSN call (a deleted or banned account answering NotFound) never stamps `last_synced`. Those
profiles looked overdue on every 15-minute cron run and were re-queued, and failed, forever.

The sync side calls `record_failure` (from `Profile.set_sync_status('error')`) and `clear` (when
`sync_complete` succeeds). The cron calls `backing_off` to skip `error` profiles still inside their
window. Manual syncs (the hunter's refresh button, staff actions) never consult it.

Two Redis keys per profile, both under `sync_error_*`:
  - `sync_error_streak:<id>`: consecutive failed attempts, kept 30 days.
  - `sync_error_backoff:<id>`: present while the profile is backing off; its TTL is the delay.

Redis trouble never fails a sync: every call here logs and degrades to "no backoff".
"""
import logging

from trophies.util_modules import cache

logger = logging.getLogger('psn_api')

STREAK_KEY = 'sync_error_streak:{}'
BACKOFF_KEY = 'sync_error_backoff:{}'
BASE_DELAY_SECONDS = 60 * 60
#: Matches the unregistered refresh cadence: an account that keeps failing is retried weekly forever,
#: so one that comes back recovers within a week with no staff action.
MAX_DELAY_SECONDS = 7 * 24 * 60 * 60
STREAK_TTL_SECONDS = 30 * 24 * 60 * 60


def retry_delay(streak):
    """Seconds to wait after the `streak`-th consecutive failure: 1h, 2h, 4h, ... capped at a week."""
    return min(BASE_DELAY_SECONDS * 2 ** (max(streak, 1) - 1), MAX_DELAY_SECONDS)


def record_failure(profile_id):
    """Count one failed attempt and start its backoff window.

    Counts at most once per window: a single failed sync can mark the profile 'error' from several
    jobs (sync_profile_data, the privacy handler, sync_complete), and that is one attempt, not three.
    """
    redis = cache.redis_client
    backoff_key = BACKOFF_KEY.format(profile_id)
    try:
        # SET NX is the gate, so two jobs reporting the same failure at once still count it once.
        # It claims the window at the longest delay, then shortens it to this streak's.
        if not redis.set(backoff_key, '1', nx=True, ex=MAX_DELAY_SECONDS):
            return
        streak_key = STREAK_KEY.format(profile_id)
        streak = redis.incr(streak_key)
        redis.expire(streak_key, STREAK_TTL_SECONDS)
        redis.expire(backoff_key, retry_delay(streak))
    except Exception:
        logger.exception(f'[profile {profile_id}] could not record sync failure backoff')


def clear(profile_id):
    """A sync succeeded: the next failure starts again at the shortest delay."""
    try:
        cache.redis_client.delete(STREAK_KEY.format(profile_id), BACKOFF_KEY.format(profile_id))
    except Exception:
        logger.exception(f'[profile {profile_id}] could not clear sync failure backoff')


def backing_off(profile_ids):
    """The subset of `profile_ids` still inside a backoff window. One round trip for the batch."""
    profile_ids = list(profile_ids)
    if not profile_ids:
        return set()
    try:
        flags = cache.redis_client.mget([BACKOFF_KEY.format(pid) for pid in profile_ids])
    except Exception:
        logger.exception('could not read sync failure backoff; retrying every error profile')
        return set()
    return {pid for pid, flag in zip(profile_ids, flags) if flag is not None}
