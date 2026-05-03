from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('trophies', '0220_remove_roadmap_derived_metadata'),
    ]

    operations = [
        migrations.AddField(
            model_name='igdbmatch',
            name='igdb_ps_release_dates',
            field=models.JSONField(
                default=list,
                blank=True,
                help_text=(
                    'Per-platform PlayStation release dates from IGDB. Sorted by '
                    'date ascending. Format: [{"platform": <int>, "date": "YYYY-MM-DD"}, ...]. '
                    'Populated from raw_response.release_dates by enrichment + backfill. '
                    'Used by the matcher (any-PS-date proximity) and per-platform '
                    'displays in admin review tools.'
                ),
            ),
        ),
    ]
