"""Move the Trophies board's two partial indexes onto `total_trophies_raw`.

The board's ordering, tiebreak and membership rule moved to that column in 0333. These indexes have to
move with them or they simply stop matching -- silently, and straight back into what migration 0307
measured on a synthetic 300k-profile / 50k-linked shape:

    trophy_rank   16.0 ms, planner abandons the index and seq-scans a 48-column table  ->  3.9 ms
    board_count   10.0 ms, parallel seq scan of 300k                                   ->  4.2 ms

That `trophy_rank` read runs on EVERY authenticated page view, which is why this is a migration of its
own rather than a follow-up.

SEPARATE FROM 0333 ON PURPOSE. `CREATE INDEX CONCURRENTLY` cannot run in a transaction, so this needs
`atomic = False` -- and under `atomic = False` Django commits each operation independently but records the
migration as applied only after the last one. A build that dies partway therefore replays from operation
0 on the next `migrate`. Keeping the column and its backfill in 0333 means that replay is just these four
index operations, which are safe to repeat once the invalid index is cleaned up. Bundled together, the
replay would have hit `AddField` and failed with "column already exists", stranding the operator
mid-deploy.

IF A BUILD FAILS PARTWAY, Postgres leaves an INVALID index behind that must be dropped by hand before
re-running:

    DROP INDEX CONCURRENTLY IF EXISTS profile_board_idx;
    DROP INDEX CONCURRENTLY IF EXISTS profile_board_cc_idx;

Verify afterwards that both exist and are valid:

    SELECT indexrelid::regclass, indisvalid FROM pg_index WHERE indexrelid::regclass::text IN
      ('profile_board_idx','profile_board_cc_idx');

And re-run 0307's own check that the board reads are index-only:

    EXPLAIN (ANALYZE, BUFFERS) SELECT ... -- see docs/architecture/leaderboard-system.md
"""

from django.contrib.postgres.operations import AddIndexConcurrently, RemoveIndexConcurrently
from django.db import migrations, models
from django.db.models import Q

#: Both halves of the board's membership rule, on the UNFILTERED count. `is_linked` is the population
#: gate; `> 0` is the board's own rule. They must match `badge_leaderboards.trophy_store()` exactly or
#: the planner stops using these.
_BOARD = Q(is_linked=True, total_trophies_raw__gt=0)


class Migration(migrations.Migration):

    # CREATE/DROP INDEX CONCURRENTLY cannot run inside a transaction.
    atomic = False

    dependencies = [
        ("trophies", "0333_total_trophies_raw"),
    ]

    operations = [
        # Drop first: the replacements reuse the same names, and Postgres will not allow a duplicate.
        RemoveIndexConcurrently(model_name="profile", name="profile_board_idx"),
        RemoveIndexConcurrently(model_name="profile", name="profile_board_cc_idx"),
        AddIndexConcurrently(
            model_name="profile",
            index=models.Index(
                fields=["-total_plats", "-total_trophies_raw", "id"],
                name="profile_board_idx",
                condition=_BOARD,
            ),
        ),
        AddIndexConcurrently(
            model_name="profile",
            index=models.Index(
                fields=["country_code", "-total_plats", "-total_trophies_raw", "id"],
                name="profile_board_cc_idx",
                condition=_BOARD,
            ),
        ),
    ]
