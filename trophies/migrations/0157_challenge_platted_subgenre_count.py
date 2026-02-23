from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("trophies", "0156_dashboard_config"),
    ]

    operations = [
        migrations.AddField(
            model_name="challenge",
            name="platted_subgenre_count",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
