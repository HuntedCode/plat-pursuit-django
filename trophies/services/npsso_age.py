"""How old each NPSSO cookie is, for the TokenKeeper dashboard.

The NPSSO cookie is what actually expires. PSN never says when, and nothing renews it: every login
sends the cookie to Sony and gets back a fresh access and refresh token, never a new cookie (the
refresh token's expiry therefore resets on every login and says nothing about the cookie). So each
cookie's age is counted from the first time the TokenKeeper logged in with it, and the age at which
one stops working is recorded too, which over time shows the real lifetime.

Keyed by `token_fingerprint`, never the token. Stored without a TTL: the ages only mean something if
they outlive worker restarts (Render's Valkey keeps its data across restarts).

Nothing here may raise. These run inside the TokenKeeper's login and refresh `try` blocks, where an
exception marks a client that logged in fine as failed, so a Redis error only costs the age.
"""
import json
import logging
import time

from psnawp_api.core.psnawp_exceptions import PSNAWPAuthenticationError

from trophies.util_modules.cache import redis_client, token_fingerprint

logger = logging.getLogger('psn_api')

# Dashboard warning points. Rotating a cookie at amber leaves time to spare; the observed lifetime
# (see record_npsso_expired) is what should move these.
NPSSO_WARN_DAYS = 75
NPSSO_DANGER_DAYS = 85

FIRST_SEEN_KEY = 'token_keeper:npsso:first_seen:{}'
EXPIRED_KEY = 'token_keeper:npsso:expired:{}'
SECONDS_PER_DAY = 86400


def record_npsso_login(token, now=None):
    """Note a successful login with this cookie and return when it was first seen (epoch seconds).
    Only the first login is kept, so later logins never make a cookie look younger."""
    now = time.time() if now is None else now
    try:
        redis_client.set(FIRST_SEEN_KEY.format(token_fingerprint(token)), now, nx=True)
    except Exception:
        logger.warning("Could not record an NPSSO login", exc_info=True)
    return npsso_first_seen(token) or now


def npsso_first_seen(token):
    """When this cookie first logged in (epoch seconds), or None if it never has."""
    try:
        value = redis_client.get(FIRST_SEEN_KEY.format(token_fingerprint(token)))
        return float(value) if value is not None else None
    except Exception:
        logger.warning("Could not read an NPSSO's first login", exc_info=True)
        return None


def npsso_age_days(first_seen, now=None):
    """Whole-and-fractional days since `first_seen`, or None when it is unknown."""
    if first_seen is None:
        return None
    now = time.time() if now is None else now
    return (now - first_seen) / SECONDS_PER_DAY


def npsso_age_level(age_days):
    """'ok', 'warn' or 'danger' for the dashboard, or 'unknown' with no age."""
    if age_days is None:
        return 'unknown'
    if age_days >= NPSSO_DANGER_DAYS:
        return 'danger'
    if age_days >= NPSSO_WARN_DAYS:
        return 'warn'
    return 'ok'


def is_npsso_expired_error(exc):
    """Whether a login failed because Sony rejected the cookie itself.

    psnawp raises PSNAWPAuthenticationError for every login failure and marks this one (Sony's
    error 4165) only in the message, so the message is all there is to go on.
    """
    return isinstance(exc, PSNAWPAuthenticationError) and 'expired' in str(exc).lower()


def record_npsso_expired(token, now=None):
    """Record the age at which this cookie stopped working and return it in days (None if the
    cookie was never seen working). Only the first report is kept."""
    now = time.time() if now is None else now
    fingerprint = token_fingerprint(token)
    age = npsso_age_days(npsso_first_seen(token), now)
    record = json.dumps({'expired_at': now, 'age_days': age})
    try:
        first_report = redis_client.set(EXPIRED_KEY.format(fingerprint), record, nx=True)
    except Exception:
        logger.warning("Could not record an expired NPSSO", exc_info=True)
        first_report = True  # still say so in the log
    if first_report:
        age_text = f'{age:.1f} days' if age is not None else 'an unknown age'
        logger.warning(f"NPSSO {fingerprint[:12]} expired at {age_text}; replace it in TOKEN_GROUPS")
    return age
