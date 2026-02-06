# Generated manually for badge progress quiz feature
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('trophies', '0126_monthlyrecap_activity_calendar'),
    ]

    operations = [
        migrations.AddField(
            model_name='monthlyrecap',
            name='badge_progress_quiz_data',
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text='Badge progress quiz data: {correct_badge_id, correct_badge_name, correct_progress_pct, correct_completed, correct_required, options: [{id, name, series, icon_url}]}'
            ),
        ),
    ]
