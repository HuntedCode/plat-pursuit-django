"""Test settings.

Inherits the full production configuration from settings.py, then overrides the
pieces that would otherwise hit the network, require real secrets, or slow the
suite down. The goal is "prod-like, but with external I/O neutralized" so the
engine is exercised faithfully while tests stay fast and hermetic.

Activated automatically by pytest via DJANGO_SETTINGS_MODULE in pyproject.toml.

Tests run against Postgres (inherited from settings.py), NOT SQLite: the engine
relies on Postgres-specific behavior (DB-side aggregation for whale profiles,
JSON fields, statement timeouts), so SQLite would give false confidence. Django
creates and tears down a throwaway "test_<dbname>" database automatically.
"""

import os

from .settings import *  # noqa: F401,F403,E402

# Marker so code/tests can branch on "are we under test" if ever needed.
TESTING = True

# Force a LOCAL test database, ignoring any DATABASE_URL in the environment.
# settings.py loads .env, which points DATABASE_URL at a remote DB (host "db",
# resolvable only inside Docker); the test runner CREATEs and DROPs a test DB,
# so it must never target that. Defaults below match the dedicated `db-test`
# service in docker-compose.yml (port 5544, separate from the dev `db` on 5432).
# CI overrides these via TEST_DB_* env vars to point at its own Postgres service.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("TEST_DB_NAME", "plat_pursuit_dev"),
        "USER": os.getenv("TEST_DB_USER", "plat_user"),
        "PASSWORD": os.getenv("TEST_DB_PASSWORD", "securepass"),
        "HOST": os.getenv("TEST_DB_HOST", "localhost"),
        "PORT": os.getenv("TEST_DB_PORT", "5544"),
    }
}

# Deterministic, non-secret key so tests never depend on a real .env value.
SECRET_KEY = "test-secret-key-not-for-production"  # noqa: S105

DEBUG = False
ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]

# Point the RAW redis client (token_keeper / leaderboards, created from REDIS_URL
# in trophies/util_modules/cache.py) at localhost instead of the Docker-internal
# "redis" host that .env uses. Without this, every signal-driven leaderboard
# write hangs on a DNS-resolution timeout before its try/except swallows it,
# making the suite crawl. With localhost it either succeeds (local redis up, DB
# index 15 keeps it isolated) or refuses instantly. Set before cache.py imports.
os.environ["REDIS_URL"] = os.getenv("TEST_REDIS_URL", "redis://localhost:6379/15")

# Never redirect to HTTPS or require secure cookies during tests (would 301 the
# test client and break view tests).
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

# In-memory Django cache: no Redis required for the cache framework in tests.
# (The raw redis client used by token_keeper / leaderboards is mocked per-test
# with the `fake_redis` fixture; see docs/guides/testing.md.)
CACHES = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
}

# Capture outbound email in memory (assert on mail.outbox) instead of sending.
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# Plain local storage; no S3 and no hashed-manifest staticfiles in tests.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
    },
}

# Fast password hashing so creating users in tests isn't bcrypt-slow.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Dummy Stripe key so any module-level / runtime use doesn't need a real secret.
import stripe  # noqa: E402

stripe.api_key = "sk_test_dummy"
