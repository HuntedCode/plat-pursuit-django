"""GroupBadgeAnnouncement + the backfill that MUST ship with it.

The table records every (profile, group_badge) already announced to Discord, so an empty one reads as
"nobody has ever been told about anything". Because badge cutover 5b also flips `evaluate_for_sync` to
`notify=True`, the first sync after this migration would re-announce every badge every hunter already
holds -- a webhook storm proportional to the whole userbase's badge count.

The backfill is IN the migration rather than on the deploy checklist deliberately: a manual step whose
failure mode is "spam every hunter in the Discord" is not a step worth trusting to a checkbox.
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
