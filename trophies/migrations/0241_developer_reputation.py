from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('trophies', '0240_roadmap_trial_writers_alter_profile_roadmap_role'),
    ]

    operations = [
        migrations.RenameModel(
            old_name='DeveloperBlacklist',
            new_name='DeveloperReputation',
        ),
        migrations.AddField(
            model_name='developerreputation',
            name='is_whitelisted',
            field=models.BooleanField(
                default=False,
                help_text="Admin full exemption: the company's primary-developed concepts are never auto-flagged. Wins over is_blacklisted.",
            ),
        ),
        migrations.AlterField(
            model_name='developerreputation',
            name='company',
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='developer_reputation_entry',
                to='trophies.company',
            ),
        ),
        migrations.AlterField(
            model_name='developerreputation',
            name='is_blacklisted',
            field=models.BooleanField(
                default=False,
                help_text="True while >50% of the company's platinum-bearing primary-developed concepts are independently shovelware.",
            ),
        ),
    ]
