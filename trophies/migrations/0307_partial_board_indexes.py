# Make the two Trophies-board indexes PARTIAL on the board's own population.
#
# `badge_leaderboards.trophy_store()` reads `is_linked=True, total_trophies__gt=0`, and neither predicate
# was in either index, so `is_linked` became a heap filter on every row the scan touched. Measured on a
# synthetic 300k-profile / 50k-linked shape (the ratio matters -- scout accounts and catalogue profiles
# are unlinked):
#
#   trophy_rows page 500   49.7 ms, 149,308 index entries walked to yield 25,000  ->  2.6 ms, 98 buffers
#   board_count('trophies') 10.0 ms, parallel seq scan of 300k                    ->  4.2 ms, index only
#   trophy_rank            16.0 ms, planner abandons the index, seq-scans a       ->  3.9 ms, index only
#                          48-column table -- on EVERY authenticated page view
#
# The partial index is also physically smaller, since it holds only the ranked population.
#
# Built with Add/RemoveIndexConcurrently (+ atomic = False), same pattern as 0257 / 0260 / 0262, so the
# rebuild does not write-lock `Profile` -- the single most contended table in the schema, written by every
# sync worker.
#
# NOTE: if a CONCURRENTLY build fails partway, Postgres leaves an INVALID index behind that must be
# dropped by hand before re-running:
#     DROP INDEX CONCURRENTLY IF EXISTS profile_board_idx;
#     DROP INDEX CONCURRENTLY IF EXISTS profile_board_cc_idx;

from django.contrib.postgres.operations import AddIndexConcurrently, RemoveIndexConcurrently
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):

    # CREATE/DROP INDEX CONCURRENTLY cannot run inside a transaction.
    atomic = False

    dependencies = [
        ("trophies", "0306_user_group_badge_created_at"),
    ]

    operations = [
        # Drop first: the replacements reuse the same names, and Postgres will not allow a duplicate.
        RemoveIndexConcurrently(model_name="profile", name="profile_board_idx"),
        RemoveIndexConcurrently(model_name="profile", name="profile_board_cc_idx"),
        AddIndexConcurrently(
            model_name="profile",
            index=models.Index(
                fields=["-total_plats", "-total_trophies", "id"],
                name="profile_board_idx",
                condition=Q(is_linked=True, total_trophies__gt=0),
            ),
        ),
        AddIndexConcurrently(
            model_name="profile",
            index=models.Index(
                fields=["country_code", "-total_plats", "-total_trophies", "id"],
                name="profile_board_cc_idx",
                condition=Q(is_linked=True, total_trophies__gt=0),
            ),
        ),
    ]
