"""GroupBadgeAnnouncement + the backfill that ships with it.

The table records every (profile, group_badge) already announced, so re-earning a badge cannot re-announce
it. `UserGroupBadge` is binary: a revoke DELETES the row, so a later re-earn is indistinguishable from a
first earn. PSN flux, a DLC drop or a curator editing a stage can therefore re-ping a hunter about a badge
they have held for a year -- something the legacy engine's `maintenance` state made impossible.

The backfill seeds one marker per CURRENTLY held badge, so hunters whose badges predate this column are
covered by the same guard the moment flux touches them.

WHAT THIS IS NOT: it is not protection against a first-sync announcement storm at deploy. An earlier draft
of this docstring claimed that, and it was wrong. `diff()` only emits an `award` when the profile does not
already hold the badge (`cur is None`), and `announce_badges_earned` fires only on `result['awarded']` --
so a still-held badge produces an `update` or nothing, and never announces. The deploy is quiet with or
without this backfill. The genuine case it covers is revoke -> re-earn, which is exactly what the 5a audit
raised it for.
"""

import django.db.models.deletion
from django.db import migrations, models


def seed_from_existing_holds(apps, schema_editor):
    """One marker per badge currently held: those hunters have already been told.

    Batched with `iterator()` + `bulk_create` so the memory cost is bounded by batch size rather than by
    the number of holds. `ignore_conflicts` makes it safe to re-run.
    """
    GroupBadgeAnnouncement = apps.get_model('trophies', 'GroupBadgeAnnouncement')
    UserGroupBadge = apps.get_model('trophies', 'UserGroupBadge')

    held = UserGroupBadge.objects.values_list('profile_id', 'group_badge_id').iterator(chunk_size=2000)
    batch = []
    for profile_id, group_badge_id in held:
        batch.append(GroupBadgeAnnouncement(profile_id=profile_id, group_badge_id=group_badge_id))
        if len(batch) >= 2000:
            GroupBadgeAnnouncement.objects.bulk_create(batch, ignore_conflicts=True)
            batch = []
    if batch:
        GroupBadgeAnnouncement.objects.bulk_create(batch, ignore_conflicts=True)


def drop_markers(apps, schema_editor):
    """Reverse: the table goes away with the CreateModel, so there is nothing to undo separately."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("trophies", "0304_drop_dashboard_config"),
    ]

    operations = [
        migrations.CreateModel(
            name="GroupBadgeAnnouncement",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("announced_at", models.DateTimeField(auto_now_add=True)),
                (
                    "group_badge",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="announcements",
                        to="trophies.groupbadge",
                    ),
                ),
                (
                    "profile",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="badge_announcements",
                        to="trophies.profile",
                    ),
                ),
            ],
            options={
                "unique_together": {("profile", "group_badge")},
            },
        ),
        migrations.RunPython(seed_from_existing_holds, drop_markers),
    ]
