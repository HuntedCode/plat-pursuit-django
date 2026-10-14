"""The Redis preflight must explain a missing Redis, quickly, and stay quiet when Redis answers.

The conftest wiring (stop the session, fail with this message, before the test DB is built) was
verified by hand in 2026-10 with TEST_REDIS_URL pointed at a closed port, both serially and under
pytest-xdist.
"""
import time

import redis

from tests.redis_preflight import START_HINT, _without_password, redis_unreachable_reason

#: Port 1 is reserved and never runs Redis; 127.0.0.1 avoids an IPv6 attempt on Windows.
CLOSED_URL = 'redis://127.0.0.1:1/15'
FAST_TIMEOUT_SECONDS = 0.2
#: Generous on purpose: the regression this guards against (redis-py's default connect
#: retries with backoff) took seven seconds on average and up to fifteen.
FAST_ENOUGH_SECONDS = 3


def test_a_missing_redis_is_explained_quickly():
    started = time.monotonic()
    reason = redis_unreachable_reason(CLOSED_URL, timeout=FAST_TIMEOUT_SECONDS)
    assert time.monotonic() - started < FAST_ENOUGH_SECONDS
    assert reason is not None
    assert CLOSED_URL in reason
    assert START_HINT in reason


def test_a_reachable_redis_passes(monkeypatch):
    monkeypatch.setattr(redis.Redis, 'ping', lambda self: True)
    assert redis_unreachable_reason(CLOSED_URL) is None


def test_a_password_in_the_url_is_never_printed():
    url = 'redis://:hunter2@127.0.0.1:1/15'
    reason = redis_unreachable_reason(url, timeout=FAST_TIMEOUT_SECONDS)
    assert 'hunter2' not in reason
    assert ':***@127.0.0.1:1/15' in reason


def test_urls_without_a_password_are_printed_unchanged():
    assert _without_password(CLOSED_URL) == CLOSED_URL
    assert _without_password('rediss://user:pw@cache.example:6380/0') == 'rediss://user:***@cache.example:6380/0'
