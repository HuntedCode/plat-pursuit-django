import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("trophies", "0211_profile_roadmap_role"),
    ]

    operations = [
        # Attribution FKs on existing roadmap content models.
        migrations.AddField(
            model_name="roadmaptab",
            name="created_by",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "Profile that first wrote this tab's content fields. "
                    "Used for writer-ownership scoping."
                ),
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="authored_roadmap_tabs",
                to="trophies.profile",
            ),
        ),
        migrations.AddField(
            model_name="roadmaptab",
            name="last_edited_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="last_edited_roadmap_tabs",
                to="trophies.profile",
            ),
        ),
        migrations.AddField(
            model_name="roadmapstep",
            name="created_by",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "Profile that originally created this step. Used for "
                    "writer-ownership scoping (writers can only edit steps they created)."
                ),
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="authored_roadmap_steps",
                to="trophies.profile",
            ),
        ),
        migrations.AddField(
            model_name="roadmapstep",
            name="last_edited_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="last_edited_roadmap_steps",
                to="trophies.profile",
            ),
        ),
        migrations.AddField(
            model_name="trophyguide",
            name="created_by",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "Profile that originally wrote this trophy guide. "
                    "Used for writer-ownership scoping."
                ),
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="authored_trophy_guides",
                to="trophies.profile",
            ),
        ),
        migrations.AddField(
            model_name="trophyguide",
            name="last_edited_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="last_edited_trophy_guides",
                to="trophies.profile",
            ),
        ),
        # New models: edit lock + revision history.
        migrations.CreateModel(
            name="RoadmapEditLock",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("acquired_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("last_heartbeat", models.DateTimeField(default=django.utils.timezone.now)),
                ("expires_at", models.DateTimeField()),
                ("branch_payload", models.JSONField(default=dict)),
                ("payload_version", models.PositiveSmallIntegerField(default=1)),
                (
                    "holder",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="held_roadmap_locks",
                        to="trophies.profile",
                    ),
                ),
                (
                    "roadmap",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="edit_lock",
                        to="trophies.roadmap",
                    ),
                ),
            ],
            options={
                "verbose_name": "Roadmap Edit Lock",
                "verbose_name_plural": "Roadmap Edit Locks",
            },
        ),
        migrations.AddIndex(
            model_name="roadmapeditlock",
            index=models.Index(fields=["expires_at"], name="roadmap_lock_expires_idx"),
        ),
        migrations.CreateModel(
            name="RoadmapRevision",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                (
                    "action_type",
                    models.CharField(
                        choices=[
                            ("created", "Created"),
                            ("edited", "Edited"),
                            ("published", "Published"),
                            ("unpublished", "Unpublished"),
                            ("restored", "Restored"),
                            ("force_unlocked", "Force unlocked"),
                        ],
                        max_length=20,
                    ),
                ),
                (
                    "snapshot",
                    models.JSONField(
                        help_text=(
                            "Full guide state after this action: tabs, steps, "
                            "trophy guides, attribution."
                        )
                    ),
                ),
                (
                    "summary",
                    models.CharField(
                        blank=True,
                        help_text=(
                            "Auto-generated short description "
                            '(e.g., "edited 3 steps in Story Walkthrough").'
                        ),
                        max_length=200,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "author",
                    models.ForeignKey(
                        blank=True,
                        help_text=(
                            "Profile that triggered this revision. NULL preserves "
                            "history if profile is later deleted."
                        ),
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="authored_roadmap_revisions",
                        to="trophies.profile",
                    ),
                ),
                (
                    "roadmap",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="revisions",
                        to="trophies.roadmap",
                    ),
                ),
            ],
            options={
                "verbose_name": "Roadmap Revision",
                "verbose_name_plural": "Roadmap Revisions",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="roadmaprevision",
            index=models.Index(
                fields=["roadmap", "-created_at"], name="roadmap_rev_recent_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="roadmaprevision",
            index=models.Index(
                fields=["author", "-created_at"], name="roadmap_rev_author_idx"
            ),
        ),
    ]
