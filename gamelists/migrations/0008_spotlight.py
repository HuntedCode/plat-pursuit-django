# The Spotlight's storage: one nullable timestamp on `gamelists_gamelist`.
#
# THE COLUMN ONLY. Its partial index is a SEPARATE migration (0009) because that one has to run
# outside a transaction, and the two cannot share one.
#
# Safe on a populated table: nullable with NO default, so Postgres 11+ records it in the catalogue
# and rewrites no rows. The ACCESS EXCLUSIVE lock is held for the metadata update alone.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("gamelists", "0007_list_reports"),
    ]

    operations = [
        migrations.AddField(
            model_name="gamelist",
            name="featured_at",
            field=models.DateTimeField(
                blank=True,
                help_text="Set to feature this list in the Spotlight on the Game Lists browse page. The most recently set wins. Clear it to remove the Spotlight.",
                null=True,
            ),
        ),
    ]
