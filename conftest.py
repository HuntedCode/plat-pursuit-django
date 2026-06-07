"""Root pytest configuration and shared fixtures.

pytest-django reads DJANGO_SETTINGS_MODULE from pyproject.toml, so no settings
wiring is needed here. Fixtures below are convenience wrappers over the factories.
"""

import pytest


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
