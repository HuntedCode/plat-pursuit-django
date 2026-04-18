from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("trophies", "0202_seed_platinum_case_showcases"),
    ]

    operations = [
        migrations.DeleteModel(
            name="PublisherBlacklist",
        ),
        migrations.CreateModel(
            name="DeveloperBlacklist",
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
                ("date_added", models.DateTimeField(auto_now_add=True)),
                (
                    "flagged_concepts",
                    models.JSONField(
                        blank=True,
                        default=list,
                        help_text="List of concept_id strings whose games triggered this entry.",
                    ),
                ),
                (
                    "is_blacklisted",
                    models.BooleanField(
                        default=False,
                        help_text="True when any concept is tracked. Other concepts by this developer get auto-flagged.",
                    ),
                ),
                ("notes", models.TextField(blank=True)),
                (
                    "company",
                    models.OneToOneField(
                        on_delete=models.deletion.CASCADE,
                        related_name="developer_blacklist_entry",
                        to="trophies.company",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(fields=["is_blacklisted"], name="dev_blacklist_active_idx"),
                ],
            },
        ),
    ]
