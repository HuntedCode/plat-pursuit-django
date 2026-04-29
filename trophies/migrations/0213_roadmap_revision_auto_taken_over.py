from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("trophies", "0212_roadmap_lock_revision_attribution"),
    ]

    operations = [
        migrations.AlterField(
            model_name="roadmaprevision",
            name="action_type",
            field=models.CharField(
                choices=[
                    ("created", "Created"),
                    ("edited", "Edited"),
                    ("published", "Published"),
                    ("unpublished", "Unpublished"),
                    ("restored", "Restored"),
                    ("force_unlocked", "Force unlocked"),
                    ("auto_taken_over", "Auto-taken over (stale lock)"),
                ],
                max_length=20,
            ),
        ),
    ]
