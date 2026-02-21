from django.db import migrations, models
from django.utils import timezone


def migrate_shovelware_flags(apps, schema_editor):
    """Convert existing is_shovelware boolean to new shovelware_status field."""
    Game = apps.get_model('trophies', 'Game')
    now = timezone.now()

    Game.objects.filter(is_shovelware=True).update(
        shovelware_status='auto_flagged',
        shovelware_updated_at=now,
    )
    Game.objects.filter(is_shovelware=False).update(
        shovelware_status='clean',
    )


class Migration(migrations.Migration):

    dependencies = [
        ('trophies', '0149_badge_is_live'),
    ]

    operations = [
        # --- Game: new shovelware fields ---
        migrations.AddField(
            model_name='game',
            name='shovelware_status',
            field=models.CharField(
                choices=[
                    ('clean', 'Clean'),
                    ('auto_flagged', 'Auto-Flagged'),
                    ('manually_flagged', 'Manually Flagged'),
                    ('manually_cleared', 'Manually Cleared'),
                ],
                default='clean',
                help_text='Shovelware detection status. Manual statuses are never overwritten by auto-detection.',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='game',
            name='shovelware_lock',
            field=models.BooleanField(
                default=False,
                help_text='Admin lock: prevents auto-detection from changing this game\'s shovelware status.',
            ),
        ),
        migrations.AddField(
            model_name='game',
            name='shovelware_updated_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name='game',
            index=models.Index(fields=['shovelware_status'], name='game_sw_status_idx'),
        ),

        # --- Data migration: copy is_shovelware -> shovelware_status ---
        migrations.RunPython(migrate_shovelware_flags, migrations.RunPython.noop),

        # --- Game: remove old fields ---
        migrations.RemoveIndex(
            model_name='game',
            name='game_shovelware_idx',
        ),
        migrations.RemoveField(
            model_name='game',
            name='is_shovelware',
        ),

        # --- PublisherBlacklist: restructure ---
        migrations.AddField(
            model_name='publisherblacklist',
            name='flagged_concepts',
            field=models.JSONField(
                blank=True, default=list,
                help_text='List of concept IDs whose games triggered this entry.',
            ),
        ),
        migrations.AddField(
            model_name='publisherblacklist',
            name='is_blacklisted',
            field=models.BooleanField(
                default=False,
                help_text='True when flagged_concepts reaches 5+. All publisher games get flagged.',
            ),
        ),
        migrations.AddField(
            model_name='publisherblacklist',
            name='notes',
            field=models.TextField(blank=True),
        ),
    ]
