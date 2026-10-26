# A badge's masters (its count and list): a partial index over the few mastered holds.
#
# Built with AddIndexConcurrently (+ atomic = False) so CREATE INDEX does not write-lock UserGroupBadge while
# badge evaluations write to it. Same pattern as 0350.
#
# NOTE: if a CONCURRENTLY build fails partway, Postgres leaves an INVALID index behind that must be dropped
# manually before re-running:
#     DROP INDEX CONCURRENTLY IF EXISTS ugb_badge_mastered_idx;

from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):

    # CREATE INDEX CONCURRENTLY cannot run inside a transaction.
    atomic = False

    dependencies = [
        ('trophies', '0353_badge_mastery'),
    ]

    operations = [
        AddIndexConcurrently(
            model_name='usergroupbadge',
            index=models.Index(condition=models.Q(('mastered_at__isnull', False)), fields=['group_badge', 'mastered_at', 'profile'], name='ugb_badge_mastered_idx'),
        ),
    ]
