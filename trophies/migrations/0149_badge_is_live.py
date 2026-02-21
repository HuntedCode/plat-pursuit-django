from django.db import migrations, models


def set_existing_badges_live(apps, schema_editor):
    """Mark all existing badges as live since they are already public."""
    Badge = apps.get_model('trophies', 'Badge')
    Badge.objects.all().update(is_live=True)


class Migration(migrations.Migration):

    dependencies = [
        ('trophies', '0148_badge_funded_by'),
    ]

    operations = [
        migrations.AddField(
            model_name='badge',
            name='is_live',
            field=models.BooleanField(
                default=False,
                help_text='Whether this badge is visible to regular users. New badges start hidden until explicitly released.',
            ),
        ),
        migrations.RunPython(set_existing_badges_live, migrations.RunPython.noop),
        migrations.AddIndex(
            model_name='badge',
            index=models.Index(fields=['is_live'], name='badge_is_live_idx'),
        ),
    ]
