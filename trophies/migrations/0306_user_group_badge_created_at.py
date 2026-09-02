"""UserGroupBadge.created_at -- the award timestamp, distinct from the completion date.

`earned_at` is the engine's `earned_date`: when the HUNTER finished the qualifying games. It is the
leaderboard sort key and it is deliberately rewritten whenever a badge's iteration changes. It is not, and
was never, "when we gave them this".

The legacy `UserBadge.earned_at` WAS award time (`auto_now_add=True`), so every surface repointed off it
inherited the wrong meaning:

  - a series shipped today and awarded to hunters who platted its games in 2019 reported ZERO badges
    earned this week, and none of those hunters saw it in their digest
  - a curator adding a stage to an old series rewrote every holder's `earned_at` to their most recent
    completion, so long-held badges reappeared as "earned this week"

The backfill sets `created_at = earned_at` rather than leaving the `timezone.now` default. Without it,
every pre-existing row would carry a deploy-day timestamp and the first digest after deploy would tell
every hunter they had just earned their entire collection. `earned_at` is the closest honest value we
have for rows awarded before this column existed, and it errs toward silence.
"""

import django.utils.timezone
from django.db import migrations, models
from django.db.models import F


def backfill_created_at(apps, schema_editor):
    """One DB-side UPDATE. Whale-safe by construction: no rows are pulled into Python."""
    UserGroupBadge = apps.get_model('trophies', 'UserGroupBadge')
    UserGroupBadge.objects.update(created_at=F('earned_at'))


def noop_reverse(apps, schema_editor):
    """Reverse: the column goes away with the AddField, so there is nothing to undo separately."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("trophies", "0305_group_badge_announcement"),
    ]

    operations = [
        migrations.AddField(
            model_name="usergroupbadge",
            name="created_at",
            field=models.DateTimeField(
                db_index=True,
                default=django.utils.timezone.now,
                editable=False,
                help_text="When WE awarded this row. Distinct from earned_at, which is the hunter's completion date and moves when the badge's iteration changes. Use this for 'earned this week'.",
            ),
        ),
        migrations.RunPython(backfill_created_at, noop_reverse),
    ]
