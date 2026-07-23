# Composite index backing the per-game leaderboard (rank by completion, ties broken by who reached it
# first). The ordering is: game_id equality, then progress DESC, then earliest most_recent_trophy_date,
# then profile_id as a unique final key so the order is TOTAL -- see the model Meta for why that matters.
#
# Without it the planner walks profilegame_progress_idx backward to satisfy ORDER BY progress DESC and
# filters out ~458K non-matching rows to return 20, measured at ~290 ms / ~250 MB of buffers on beta for
# a game with only 1,421 players. This turns that into a direct index scan.
#
# Built with AddIndexConcurrently (+ atomic = False) so CREATE INDEX does not write-lock ProfileGame
# (~833K rows, ~436 MB with indexes) while the sync workers are updating it. Same pattern as 0257.
#
# NOTE: if a CONCURRENTLY build fails partway, Postgres leaves an INVALID index behind that must be
# dropped manually before re-running:
#     DROP INDEX CONCURRENTLY IF EXISTS pg_game_leaderboard_idx;

from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):

    # CREATE INDEX CONCURRENTLY cannot run inside a transaction.
    atomic = False

    dependencies = [
        ("trophies", "0259_contracts_igdb_keyed"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="profilegame",
            index=models.Index(
                fields=["game", "-progress", "most_recent_trophy_date", "profile"],
                name="pg_game_leaderboard_idx",
            ),
        ),
    ]
