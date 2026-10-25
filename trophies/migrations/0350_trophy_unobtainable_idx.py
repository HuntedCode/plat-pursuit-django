# Partial index for "which of these lists have an unobtainable trophy?" (every badge catalogue build): a handful
# of flagged rows in a multi-million-row table.
#
# Built with AddIndexConcurrently (+ atomic = False) so CREATE INDEX does not write-lock Trophy while the sync
# workers write to it. Same pattern as 0257 / 0260.
#
# NOTE: if a CONCURRENTLY build fails partway, Postgres leaves an INVALID index behind that must be dropped
# manually before re-running:
#     DROP INDEX CONCURRENTLY IF EXISTS trophy_unobtainable_idx;

from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):

    # CREATE INDEX CONCURRENTLY cannot run inside a transaction.
    atomic = False

    dependencies = [
        ('trophies', '0349_trophy_is_unobtainable'),
    ]

    operations = [
        AddIndexConcurrently(
            model_name='trophy',
            index=models.Index(condition=models.Q(('is_unobtainable', True)), fields=['game'], name='trophy_unobtainable_idx'),
        ),
    ]
