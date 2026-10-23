"""Fixtures shared by the engine tests."""

import pytest

from trophies.models import PlatformGroup

PRODUCTION_EDITIONS = [
    # key, name, platforms, sort_order, exclude_delisted
    ('ultra-hd', 'Ultra HD', ['PS4', 'PS5'], 10, True),
    ('legacy-hd', 'Legacy HD', ['PS3', 'PSVITA'], 20, False),
]


@pytest.fixture
def production_editions():
    """Exactly the two production editions, with every field set rather than read off migration 0268's seed:
    a transactional test truncates seeded rows under --reuse-db, and a recreated Ultra HD would silently lose
    `exclude_delisted=True`. Any other group is deactivated."""
    PlatformGroup.objects.exclude(key__in=[key for key, *_ in PRODUCTION_EDITIONS]).update(is_active=False)
    return [
        PlatformGroup.objects.update_or_create(key=key, defaults={
            'name': name, 'platforms': platforms, 'sort_order': order, 'exclude_delisted': exclude_delisted,
            'is_active': True,
        })[0]
        for key, name, platforms, order, exclude_delisted in PRODUCTION_EDITIONS
    ]
