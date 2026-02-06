# Generated manually to fully denormalize recap data
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('trophies', '0127_monthlyrecap_badge_progress_quiz_data'),
    ]

    operations = [
        migrations.AddField(
            model_name='monthlyrecap',
            name='quiz_total_trophies_data',
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text='Total trophies quiz data: {correct_value, options: [numbers]}'
            ),
        ),
        migrations.AddField(
            model_name='monthlyrecap',
            name='quiz_rarest_trophy_data',
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text='Rarest trophy quiz data: {correct_trophy_id, options: [{id, name, icon_url, game, trophy_type}]}'
            ),
        ),
        migrations.AddField(
            model_name='monthlyrecap',
            name='quiz_active_day_data',
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text='Active day quiz data: {correct_day, correct_day_name, correct_count, day_counts, day_names}'
            ),
        ),
        migrations.AddField(
            model_name='monthlyrecap',
            name='streak_data',
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text='Streak data: {longest_streak, streak_start, streak_end, total_active_days}'
            ),
        ),
        migrations.AddField(
            model_name='monthlyrecap',
            name='time_analysis_data',
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text='Time of day analysis: {periods: {morning: N, afternoon: N, evening: N, night: N}, most_active_period, most_active_count}'
            ),
        ),
    ]
