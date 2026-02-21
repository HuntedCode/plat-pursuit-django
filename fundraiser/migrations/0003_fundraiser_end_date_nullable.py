from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("fundraiser", "0002_fix_badge_claim_profile_on_delete"),
    ]

    operations = [
        migrations.AlterField(
            model_name="fundraiser",
            name="end_date",
            field=models.DateTimeField(
                blank=True,
                help_text="When the fundraiser closes. Leave blank for perpetual campaigns that never end.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="fundraiser",
            name="banner_dismiss_days",
            field=models.PositiveIntegerField(
                default=7,
                help_text="Days the banner stays dismissed after a user closes it. 0 means it reappears every session.",
            ),
        ),
    ]
