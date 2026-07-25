# Creates ProfileTrophyGroup, the per-(profile, trophy group) standings denorm behind the group-scoped
# leaderboards. Its two leaderboard indexes (ptg_progress_idx, ptg_speed_idx) are created inline with the
# table: it is brand new and empty, so index creation is instant and needs no CONCURRENTLY (that only
# matters when adding an index to a table that already holds rows -- see 0262 for pg_playtime_idx, which
# does). The table is populated afterward by backfill_profile_trophy_groups and kept fresh by
# PSNApiService.update_profilegame_stats.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("trophies", "0260_profilegame_leaderboard_index"),
    ]

    operations = [
        migrations.CreateModel(
            name="ProfileTrophyGroup",
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
                (
                    "progress",
                    models.PositiveSmallIntegerField(
                        default=0,
                        help_text="Completion % within this group, FLOORED -- so it reads 100 iff every trophy in the group is earned.",
                    ),
                ),
                (
                    "earned_trophies",
                    models.JSONField(
                        blank=True,
                        default=dict,
                        help_text="Per-tier EARNED counts in this group, e.g. {'platinum':1,'gold':2,'silver':4,'bronze':9}. Feeds the row tier dots.",
                    ),
                ),
                (
                    "first_trophy_at",
                    models.DateTimeField(
                        blank=True,
                        help_text="Earliest trophy earned in this group (the speed-board start).",
                        null=True,
                    ),
                ),
                (
                    "last_trophy_at",
                    models.DateTimeField(
                        blank=True,
                        help_text="Latest trophy earned in this group -- the progress-board recency tiebreak AND the speed-board completion time.",
                        null=True,
                    ),
                ),
                (
                    "completion_seconds",
                    models.PositiveIntegerField(
                        blank=True,
                        help_text="Elapsed first->last trophy in seconds, set ONLY when the group is fully earned and defines >=2 trophies. Null = not on the speed board.",
                        null=True,
                    ),
                ),
            ],
        ),
        migrations.AddField(
            model_name="profiletrophygroup",
            name="profile",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="trophy_group_standings",
                to="trophies.profile",
            ),
        ),
        migrations.AddField(
            model_name="profiletrophygroup",
            name="trophy_group",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="profile_standings",
                to="trophies.trophygroup",
            ),
        ),
        migrations.AddIndex(
            model_name="profiletrophygroup",
            index=models.Index(
                fields=["trophy_group", "-progress", "last_trophy_at", "profile"],
                name="ptg_progress_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="profiletrophygroup",
            index=models.Index(
                condition=models.Q(("completion_seconds__isnull", False)),
                fields=[
                    "trophy_group",
                    "completion_seconds",
                    "last_trophy_at",
                    "profile",
                ],
                name="ptg_speed_idx",
            ),
        ),
        migrations.AlterUniqueTogether(
            name="profiletrophygroup",
            unique_together={("profile", "trophy_group")},
        ),
    ]
