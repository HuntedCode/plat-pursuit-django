"""Data migration: seed the Day Zero event for the Pursuit Feed.

The Pursuit Feed deliberately has no historical backfill — see
docs/architecture/event-system.md for the rationale. To ensure the feed is
never empty on launch day (and to give the chronicle a fun first entry),
this migration creates a single system event with `event_type='day_zero'`
and `profile=None`.

The Day Zero event is idempotent across replays via `get_or_create` keyed
on `event_type='day_zero'`, so re-running migrations or restoring a backup
never produces duplicates.

The Activity tab on profile pages does not show null-profile events; only
the global Pursuit Feed surfaces the Day Zero entry.
"""
from django.db import migrations
from django.utils import timezone


DAY_ZERO_MESSAGE = (
    "The Pursuit Feed has begun. Trophy hunters of the world, "
    "let the chronicle commence."
)


def seed_day_zero(apps, schema_editor):
    Event = apps.get_model('trophies', 'Event')
    Event.objects.get_or_create(
        event_type='day_zero',
        profile=None,
        defaults={
            'occurred_at': timezone.now(),
            'metadata': {
                'seed': True,
                'message': DAY_ZERO_MESSAGE,
            },
        },
    )


def reverse_day_zero(apps, schema_editor):
    """Remove the Day Zero seed event on rollback."""
    Event = apps.get_model('trophies', 'Event')
    Event.objects.filter(event_type='day_zero', profile=None).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("trophies", "0184_event"),
    ]

    operations = [
        migrations.RunPython(seed_day_zero, reverse_day_zero),
    ]
