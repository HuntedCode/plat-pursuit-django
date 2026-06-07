"""Smoke tests proving the harness itself works.

These are intentionally trivial: they verify the test settings are active, the
database is reachable and migratable, and the base factories produce valid rows.
If these pass, the harness is sound and real engine tests can build on it.
"""

import pytest
from django.conf import settings


def test_test_settings_are_active():
    """We're running under settings_test, not the production settings."""
    assert settings.SETTINGS_MODULE == "plat_pursuit.settings_test"
    assert getattr(settings, "TESTING", False) is True
    # External I/O is neutralized.
    assert settings.EMAIL_BACKEND.endswith("locmem.EmailBackend")
    assert "locmem" in settings.CACHES["default"]["BACKEND"]


def test_database_uses_postgres():
    """Tests run against Postgres (matching prod), not SQLite."""
    assert "postgresql" in settings.DATABASES["default"]["ENGINE"]


@pytest.mark.django_db
def test_user_factory_creates_valid_user():
    from tests.factories import UserFactory

    u = UserFactory()
    assert u.pk is not None
    assert u.email
    assert u.check_password("password123")


@pytest.mark.django_db
def test_profile_factory_creates_linked_profile():
    from tests.factories import ProfileFactory

    p = ProfileFactory()
    assert p.pk is not None
    assert p.user is not None
    assert 3 <= len(p.psn_username) <= 16
