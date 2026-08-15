"""Reword the middle recommendation: "Great game, rough platinum" -> "Good game, tough plat".

LABEL ONLY. The value (`good_game_bad_plat`) is untouched, so no row changes and no data moves -- Django
simply wants a migration whenever `choices` differs from the last recorded state. Nothing to deploy in
order; this can apply whenever.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("trophies", "0295_alter_userconceptrating_recommendation"),
    ]

    operations = [
        migrations.AlterField(
            model_name="userconceptrating",
            name="recommendation",
            field=models.CharField(
                blank=True,
                choices=[
                    ("worth_it", "Do it"),
                    ("good_game_bad_plat", "Good game, tough plat"),
                    ("skip", "Skip it"),
                ],
                default="",
                help_text="Would you send someone else after this platinum? Blank = predates the field (re-asked by the wizard).",
                max_length=20,
            ),
        ),
    ]
