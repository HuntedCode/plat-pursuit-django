"""Root pytest configuration and shared fixtures.

pytest-django reads DJANGO_SETTINGS_MODULE from pyproject.toml, so no settings
wiring is needed here. Fixtures below are convenience wrappers over the factories.
"""

import pytest


@pytest.fixture(autouse=True)
def _clear_rarity_cache():
    """Drop the cached rarity denominator between tests.

    settings_test uses LocMemCache, which lives for the whole run, so anything cached inside one test
    leaks into every test after it. `rarity.community_size` counts linked profiles once an hour, so
    without this the FIRST test to grade anything would fix the denominator for the entire session and
    every later rarity assertion would be measured against the wrong population.

    Deliberately ONE KEY, not `cache.clear()`. A full clear is the better isolation and was tried first
    -- but it fails four unrelated tests (company list/detail, franchise detail, milestones) that assert
    query counts and currently pass only because an earlier test happened to warm a cache for them.
    That order-dependence is real and pre-existing (they fail in isolation on unchanged code too), but
    it is not this change's to fix. Widen this to cache.clear() when those four are made self-contained.
    """
    from django.core.cache import cache

    from trophies.services.rarity import COMMUNITY_SIZE_CACHE_KEY

    cache.delete(COMMUNITY_SIZE_CACHE_KEY)
    yield
    cache.delete(COMMUNITY_SIZE_CACHE_KEY)


@pytest.fixture
def user(db):
    """A saved CustomUser."""
    from tests.factories import UserFactory

    return UserFactory()


@pytest.fixture
def profile(db):
    """A saved Profile with a linked CustomUser."""
    from tests.factories import ProfileFactory

    return ProfileFactory()


@pytest.fixture
def fake_redis(monkeypatch):
    """Swap the raw redis client for an in-memory fake.

    Use in any test that exercises token_keeper / leaderboard code paths that
    talk to Redis directly (the Django cache framework already uses locmem in
    test settings). Extend the patch targets here as spine tests need them.
    """
    import fakeredis

    server = fakeredis.FakeServer()
    client = fakeredis.FakeStrictRedis(server=server)
    # NOTE: patch targets are added as spine tests are written, e.g.:
    #   monkeypatch.setattr("trophies.util_modules.cache.redis_client", client)
    return client
