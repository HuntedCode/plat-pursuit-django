# Per-trophy unobtainable flag (badge rewards phase 2): staff mark a trophy that can no longer be earned, and
# badges count a list as finished once every other trophy is earned. See trophies/services/excused_trophies.py.
#
# The column is a constant default, which Postgres 11+ adds without rewriting the table. Its partial index is
# 0350, a migration of its own, so a failed CONCURRENTLY build can be re-run without tripping over this column.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('trophies', '0348_stage_trophy_groups'),
    ]

    operations = [
        migrations.AddField(
            model_name='trophy',
            name='is_unobtainable',
            field=models.BooleanField(default=False, help_text='Can no longer be earned (servers closed, removed content). Badges excuse it: earning every other trophy counts as finishing the list. A flag in the base list also excuses the platinum.'),
        ),
    ]
