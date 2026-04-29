from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("trophies", "0210_add_concept_title_lock_and_review"),
    ]

    operations = [
        migrations.AddField(
            model_name="profile",
            name="roadmap_role",
            field=models.CharField(
                choices=[
                    ("none", "None"),
                    ("writer", "Writer"),
                    ("editor", "Editor"),
                    ("publisher", "Publisher"),
                ],
                default="none",
                help_text=(
                    "Roadmap authoring role. Independent of is_staff. "
                    "writer: edit own sections on unpublished guides. "
                    "editor: edit any field on unpublished guides. "
                    "publisher: editor power plus toggle visibility and force-break locks."
                ),
                max_length=20,
            ),
        ),
    ]
