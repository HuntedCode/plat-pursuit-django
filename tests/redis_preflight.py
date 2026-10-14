"""Fail a test run up front when the Redis the suite needs is not running.

Model saves fire signals that write leaderboards and token-keeper state through the raw redis
client (`trophies/util_modules/cache.py`). Those writes are best-effort, so a missing Redis does
not fail them; it slows them down. On Linux and macOS a connect to a closed local port is refused
at once, but on Windows it is retried for about two seconds per address (IPv6, then IPv4), so every
such write stalls. In 2026-10 a full parallel run on a fresh Windows laptop crawled for over half
an hour with idle workers and no error, and 28 tests that need a real Redis then failed anyway.

One quick PING, before the test database is created or migrated, turns that into a stop within
a couple of seconds that says what to start.
"""
from urllib.parse import urlsplit, urlunsplit

import redis
from redis.backoff import NoBackoff
from redis.retry import Retry

#: Long enough for a healthy local Redis, short enough that a missing one is reported quickly.
PROBE_TIMEOUT_SECONDS = 1.0

START_HINT = "docker compose up -d redis"


def _without_password(url):
    """The URL as it is safe to print: any password is masked."""
    parts = urlsplit(url)
    if parts.password is None:
        return url
    host = parts.netloc.rsplit("@", 1)[1]
    return urlunsplit(parts._replace(netloc=f"{parts.username or ''}:***@{host}"))


def redis_unreachable_reason(url, timeout=PROBE_TIMEOUT_SECONDS):
    """Return why the Redis at `url` cannot be used, or None when it answers a PING."""
    # No retries: redis-py otherwise retries a failed connect three times with backoff,
    # which stretched a missing Redis from one timeout to as long as fifteen seconds.
    client = redis.Redis.from_url(
        url,
        socket_connect_timeout=timeout,
        socket_timeout=timeout,
        retry=Retry(NoBackoff(), 0),
    )
    try:
        client.ping()
    except redis.exceptions.RedisError as e:
        return (
            f"Redis is not reachable at {_without_password(url)} ({e}). Tests that use the "
            f"database need it: start it with `{START_HINT}` (see docs/guides/testing.md)."
        )
    finally:
        client.close()
    return None
