import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("trophies", "0147_alter_milestone_criteria_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="badge",
            name="funded_by",
            field=models.ForeignKey(
                blank=True,
                help_text="Profile of the donor who funded this badge artwork.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="funded_badges",
                to="trophies.profile",
            ),
        ),
    ]
