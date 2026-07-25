# Adds pg_playtime_idx to ProfileGame, backing the whole-game playtime board (most-played first).
#
# Built with AddIndexConcurrently (+ atomic = False) so CREATE INDEX does not write-lock ProfileGame
# (~844K rows) while sync workers are updating it. PARTIAL (WHERE play_duration IS NOT NULL) so only rows
# with a PSN-reported duration are indexed -- the ~24% with no reported time never appear on the board and
# stay out of the index. Same pattern as 0260.
#
# NOTE: if a CONCURRENTLY build fails partway, Postgres leaves an INVALID index behind that must be dropped
# manually before re-running:
#     DROP INDEX CONCURRENTLY IF EXISTS pg_playtime_idx;

from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):

    # CREATE INDEX CONCURRENTLY cannot run inside a transaction.
    atomic = False

    dependencies = [
        ("trophies", "0261_profiletrophygroup"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="profilegame",
            index=models.Index(
                condition=models.Q(("play_duration__isnull", False)),
                fields=["game", "-play_duration", "profile"],
                name="pg_playtime_idx",
            ),
        ),
    ]
