import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("trophies", "0214_roadmap_notes"),
    ]

    operations = [
        # Drop the old constraint before adding new fields, otherwise the
        # constraint check fires on every row and rejects the schema change.
        migrations.RemoveConstraint(
            model_name="roadmapnote",
            name="roadmap_note_target_consistency",
        ),
        migrations.AddField(
            model_name="roadmapnote",
            name="target_tab",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="notes",
                to="trophies.roadmaptab",
            ),
        ),
        migrations.AlterField(
            model_name="roadmapnote",
            name="target_kind",
            field=models.CharField(
                choices=[
                    ("guide", "Guide"),
                    ("tab", "Tab"),
                    ("step", "Step"),
                    ("trophy_guide", "Trophy guide"),
                ],
                max_length=20,
            ),
        ),
        migrations.AddIndex(
            model_name="roadmapnote",
            index=models.Index(fields=["target_tab"], name="roadmap_note_tab_idx"),
        ),
        migrations.AddConstraint(
            model_name="roadmapnote",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("target_kind", "guide"),
                        ("target_step__isnull", True),
                        ("target_tab__isnull", True),
                        ("target_trophy_guide__isnull", True),
                    ),
                    models.Q(
                        ("target_kind", "tab"),
                        ("target_step__isnull", True),
                        ("target_tab__isnull", False),
                        ("target_trophy_guide__isnull", True),
                    ),
                    models.Q(
                        ("target_kind", "step"),
                        ("target_step__isnull", False),
                        ("target_tab__isnull", True),
                        ("target_trophy_guide__isnull", True),
                    ),
                    models.Q(
                        ("target_kind", "trophy_guide"),
                        ("target_step__isnull", True),
                        ("target_tab__isnull", True),
                        ("target_trophy_guide__isnull", False),
                    ),
                    _connector="OR",
                ),
                name="roadmap_note_target_consistency",
            ),
        ),
    ]
