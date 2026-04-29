import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("trophies", "0213_roadmap_revision_auto_taken_over"),
    ]

    operations = [
        migrations.CreateModel(
            name="RoadmapNote",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                (
                    "target_kind",
                    models.CharField(
                        choices=[
                            ("guide", "Guide"),
                            ("step", "Step"),
                            ("trophy_guide", "Trophy guide"),
                        ],
                        max_length=20,
                    ),
                ),
                ("body", models.TextField()),
                (
                    "status",
                    models.CharField(
                        choices=[("open", "Open"), ("resolved", "Resolved")],
                        default="open",
                        max_length=20,
                    ),
                ),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "author",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="authored_roadmap_notes",
                        to="trophies.profile",
                    ),
                ),
                (
                    "resolved_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="resolved_roadmap_notes",
                        to="trophies.profile",
                    ),
                ),
                (
                    "roadmap",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="notes",
                        to="trophies.roadmap",
                    ),
                ),
                (
                    "target_step",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="notes",
                        to="trophies.roadmapstep",
                    ),
                ),
                (
                    "target_trophy_guide",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="notes",
                        to="trophies.trophyguide",
                    ),
                ),
            ],
            options={
                "verbose_name": "Roadmap Note",
                "verbose_name_plural": "Roadmap Notes",
                "ordering": ["created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="roadmapnote",
            index=models.Index(
                fields=["roadmap", "status", "created_at"], name="roadmap_note_thread_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="roadmapnote",
            index=models.Index(fields=["target_step"], name="roadmap_note_step_idx"),
        ),
        migrations.AddIndex(
            model_name="roadmapnote",
            index=models.Index(
                fields=["target_trophy_guide"], name="roadmap_note_guide_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="roadmapnote",
            index=models.Index(
                fields=["author", "-created_at"], name="roadmap_note_author_idx"
            ),
        ),
        migrations.AddConstraint(
            model_name="roadmapnote",
            constraint=models.CheckConstraint(
                check=(
                    models.Q(
                        ("target_kind", "guide"),
                        ("target_step__isnull", True),
                        ("target_trophy_guide__isnull", True),
                    )
                    | models.Q(
                        ("target_kind", "step"),
                        ("target_step__isnull", False),
                        ("target_trophy_guide__isnull", True),
                    )
                    | models.Q(
                        ("target_kind", "trophy_guide"),
                        ("target_step__isnull", True),
                        ("target_trophy_guide__isnull", False),
                    )
                ),
                name="roadmap_note_target_consistency",
            ),
        ),
        migrations.CreateModel(
            name="RoadmapNoteRead",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("last_read_at", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "profile",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="roadmap_note_reads",
                        to="trophies.profile",
                    ),
                ),
                (
                    "roadmap",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="note_reads",
                        to="trophies.roadmap",
                    ),
                ),
            ],
            options={
                "verbose_name": "Roadmap Note Read",
                "verbose_name_plural": "Roadmap Note Reads",
                "unique_together": {("profile", "roadmap")},
            },
        ),
        migrations.AddIndex(
            model_name="roadmapnoteread",
            index=models.Index(fields=["profile", "roadmap"], name="roadmap_note_read_idx"),
        ),
    ]
