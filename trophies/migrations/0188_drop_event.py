"""Drop the Event table.

Final step in deferring the Pursuit Feed feature. The Event model has
been removed from `trophies/models.py` along with its custom manager,
admin registration, and the EventService scaffolding that used it. This
schema migration drops the corresponding `trophies_event` table.

The previous migration 0187 unwound the DashboardConfig data rewrites
that 0186 introduced, so by the time this runs there are no application
references to event rows. The data migration ran first because data
migrations must execute before the schema they depend on goes away.

Reverse: re-creating the Event table from migration history works (the
forward Event creation in 0184_event.py is unchanged), but every other
piece of the Event System has been deleted from the codebase. Rolling
back this rollback requires manually re-introducing the model, manager,
service, and emitter call sites — there's no automatic path back. The
reverse here is intentionally a CreateModel mirroring 0184 so the schema
is restorable, but the application code paths that wrote to it are gone.
"""
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('trophies', '0187_revert_dashboardconfig_pursuit_activity'),
    ]

    operations = [
        migrations.DeleteModel(
            name='Event',
        ),
    ]
