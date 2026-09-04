"""Root pytest configuration and shared fixtures.

pytest-django reads DJANGO_SETTINGS_MODULE from pyproject.toml, so no settings
wiring is needed here. Fixtures below are convenience wrappers over the factories.
"""

import pytest


@pytest.fixture(scope="session")
def django_db_setup(django_db_setup, django_db_blocker):
    """Re-seed the migration-seeded Job catalog if the (reused) test DB has lost it.

    Data migrations run only when the test DB is CREATED, but --reuse-db keeps the DB across
    runs -- and any transactional-DB test (live_server / TransactionTestCase, committed or a
    throwaway repro) TRUNCATES every table at teardown, migration-seeded rows included. That
    combination gutted the Job catalog in 2026-08 and silently zeroed every contract-claim
    test: XP splits across a contract's jobs, so zero jobs banks zero XP, and the failures
    pointed nowhere near the cause (an idempotency test even kept passing vacuously, 0 == 0).

    Shape matters (both from the final audit of this guard):
    - This WRAPS pytest-django's django_db_setup rather than being an autouse fixture, so it
      runs only when some collected test actually requests the DB -- the ~25 DB-free
      source-guard suites keep running with no database (and no db-test container) at all.
    - The repair is announced via warnings.warn, not print: a session-fixture print lands in
      the first test's captured-setup buffer and a green -q run shows nothing, which defeats
      the point of the notice (the flush it detects should get diagnosed, not resurface).

    The repair is the seed migration's own idempotent function, run once per session and
    committed OUTSIDE any test transaction so it sticks in the reused DB. Import via
    importlib because the module name starts with a digit.
    """
    import importlib
    import warnings

    from django.apps import apps as django_apps

    with django_db_blocker.unblock():
        from trophies.models import Job

        if not Job.objects.exists():
            # BOTH Job data migrations, not just the seed: 0256 assigned every job its icon
            # AFTER 0247 created them, and a seed-only repair left 25 icon-less jobs that
            # failed test_job_icons (the full-suite run that proved this guard's first cut
            # incomplete). If a future data migration mutates Jobs again, add it here.
            seed_mod = importlib.import_module("trophies.migrations.0247_seed_jobs")
            seed_mod.seed_jobs(django_apps, None)
            icons_mod = importlib.import_module("trophies.migrations.0256_job_icons")
            icons_mod.set_icons(django_apps, None)
            warnings.warn(
                "Job catalog was EMPTY in the reused test DB -- re-seeded "
                f"{Job.objects.count()} jobs. A transactional test truncated it; "
                "see docs/guides/testing.md Gotchas.",
                stacklevel=1,
            )


@pytest.fixture(autouse=True)
def _clear_rarity_cache():
    """Drop the caches that would otherwise leak a population figure between tests.

    settings_test uses LocMemCache, which lives for the whole run, so anything cached inside one test
    leaks into every test after it. `rarity.community_size` counts linked profiles once an hour, so
    without this the FIRST test to grade anything would fix the denominator for the entire session and
    every later rarity assertion would be measured against the wrong population.

    Deliberately ONE KEY, not `cache.clear()`. A full clear is the better isolation and was tried first
    -- but it fails four unrelated tests (company list/detail, franchise detail, milestones) that assert
    query counts and currently pass only because an earlier test happened to warm a cache for them.
    That order-dependence is real and pre-existing (they fail in isolation on unchanged code too), but
    it is not this change's to fix. Widen this to cache.clear() when those four are made self-contained.

    The leaderboard PICKER caches are here for the same reason: they answer "which countries and editions
    have anyone in them", cached for an hour because they are viewer-independent and change roughly never.
    In a test run that means the first test to open a board fixes the country list for every test after
    it, so a test that creates a hunter in a new country finds their country unselectable.
    """
    from django.core.cache import cache

    from trophies.services.moderation_service import OPEN_COUNT_CACHE_KEY
    from trophies.services.rarity import COMMUNITY_SIZE_CACHE_KEY

    # site_heartbeat_{date}_{hour} is deliberately NOT here: the consuming views only cache.get
    # (the hourly cron is the sole writer in prod), so a render can never warm it -- every test
    # that seeds it does its own try/finally delete. A future test that forgets the finally
    # would leak it; add the dated key here if that class of bug ever shows up.
    #
    # The moderation open-count is the navbar's attention marker, cached for five minutes. Same leak
    # shape as the two above: the first test to render a page as a moderator would fix the number
    # for the rest of the session, so a later test that files a report would find the marker still
    # reading zero.
    keys = [COMMUNITY_SIZE_CACHE_KEY, 'lb:picker:countries', 'lb:picker:editions',
            OPEN_COUNT_CACHE_KEY]

    def _drop():
        for key in keys:
            cache.delete(key)
        # The rest are keyed on the thing they describe -- a code set, a series slug, a job slug, a game
        # and its board -- so they have no single key to delete. LocMemCache exposes its own store, and
        # nothing in the suite relies on any `lb:picker:` entry surviving.
        #
        # Swept by PREFIX rather than by an enumerated list, because the list was the bug: the per-board
        # country pickers added in 2026-08 (`lb:picker:cc:*`) were not on it, so a fixture that reused a
        # series slug with different hunters got the previous test's country list -- which fails only in
        # a group run, never alone.
        store = getattr(cache, '_cache', None)
        if store is not None:
            for raw in [k for k in list(store) if 'lb:picker:' in k]:
                store.pop(raw, None)

    _drop()
    yield
    _drop()


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
