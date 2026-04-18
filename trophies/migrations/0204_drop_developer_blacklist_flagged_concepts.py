from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("trophies", "0203_replace_publisher_blacklist_with_developer_blacklist"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="developerblacklist",
            name="flagged_concepts",
        ),
        migrations.AlterField(
            model_name="developerblacklist",
            name="is_blacklisted",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "True while the company has a non-locked, primary-developed "
                    "game at >= EVIDENCE_THRESHOLD plat earn rate."
                ),
            ),
        ),
    ]
