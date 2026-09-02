# Game.monthly_earners_count -- the Browse Games "Trending" signal.
#
# Trending used to ORDER BY a filtered Count over ProfileGame, which meant aggregating the join across
# the ENTIRE filtered catalogue before pagination. This column is that same count (owners whose
# most_recent_trophy_date falls inside 30 days), maintained by recalc_earn_rates as one more filtered
# Count on the ProfileGame GROUP BY it already runs.
#
# The index is built with AddIndexConcurrently (+ atomic = False), matching 0257: a plain CREATE INDEX
# takes an ACCESS EXCLUSIVE lock and blocks writes to trophies_game for the whole build, and this table
# is at least as large as the Concept table that precedent was written for.
#
# AddField is safe as-is: a constant default is metadata-only on modern Postgres, so it does not
# rewrite the table. The column lands at 0 everywhere, which is why the deploy checklist (row J) runs
# the backfill immediately rather than waiting for the budget-capped nightly cron -- until then
# Trending falls through to its `-played_count` secondary key.

from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):

    # CREATE INDEX CONCURRENTLY cannot run inside a transaction.
    atomic = False

    dependencies = [
        ("trophies", "0284_merge_anon_render_cost"),
    ]

    operations = [
        migrations.AddField(
            model_name="game",
            name="monthly_earners_count",
            field=models.PositiveIntegerField(
                default=0,
                help_text="Denormalized count of profiles that earned a trophy in this game in the last 30 days.",
            ),
        ),
        AddIndexConcurrently(
            model_name="game",
            index=models.Index(
                fields=["monthly_earners_count"], name="game_monthly_earners_idx"
            ),
        ),
    ]
