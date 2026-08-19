"""The per-series board ranks on BADGE POINTS, so its two indexes follow the key it actually sorts by.

WHAT CHANGED ABOVE THE DATABASE. Badge detail's Ranks board ordered on `progress_bp` -- the furthest-along
EDITION's fraction. That made its default "All editions" view a board that ignored every edition except
each hunter's best one, which is not a question anybody asks. `xp` on this table is already the series'
points SUMMED across editions (`badge_xp.compute_series_standings` sums it before the row is ever
written), so the board now ranks on that and the row shows points instead of a stage tally.

NO DATA MIGRATION. `xp` has always been populated; only the ordering reads a different column.

WHY REPLACE RATHER THAN ADD. These two indexes existed solely for the `-progress_bp` board ordering. The
other `progress_bp` orderings in the codebase -- `collection_service` and `monthly_recap_service` -- are
PROFILE-scoped (`filter(profile=profile, ...)`), so they never used an index that leads with
`series_slug`. Keeping the old pair would be pure write cost on a table every badge evaluation writes.

THE `advanced_at, profile` TAIL is not padding, and it matters MORE here than it did before.
`badge_leaderboards` numbers a window by SLOT and computes a rank by COUNTING everyone ahead, and those
two agree only because the ordering is total. Points tie in large groups by their nature -- everyone who
has cleared the same stages of a series holds the same total -- so the tail is doing the ordering for most
of the board, not just resolving the occasional collision.

NOT INDEXED, deliberately -- AND SUPERSEDED BY 0313, which gave that board a store of its own
(`SeriesEditionStanding`). Read the rest of this paragraph as the reasoning that led there, not as
current behaviour: at the time, the per-EDITION board (`_series_edition_qs`) sorted on a `group_xp` JSONB
expression, which no btree here can serve beyond the `series_slug` narrowing. One badge's chasers is a
bounded set that Postgres sorts in `work_mem`. If a very popular series shows up in `profile_render`, the
fix is an expression index per edition key, or a real per-(series, edition) standing row -- not a Python
sort.

CONCURRENTLY + `atomic = False`, as 0307 / 0309 / 0310 / 0311 do, so the rebuild does not write-lock a
table the sync workers and the badge evaluation are writing.

NOTE: if a CONCURRENTLY build fails partway, Postgres leaves an INVALID index behind that must be dropped
by hand before re-running:

    DROP INDEX CONCURRENTLY IF EXISTS sbs_series_board_idx;
    DROP INDEX CONCURRENTLY IF EXISTS sbs_series_cc_board_idx;

Verify afterwards that both exist, are valid, and are keyed on `xp`:

    SELECT indexrelid::regclass, indisvalid, pg_get_indexdef(indexrelid)
      FROM pg_index
     WHERE indexrelid::regclass::text IN ('sbs_series_board_idx', 'sbs_series_cc_board_idx');
"""

from django.contrib.postgres.operations import AddIndexConcurrently, RemoveIndexConcurrently
from django.db import migrations, models
from django.db.models import Q

#: Same partial condition the pair already carried -- every board population is `is_linked`-gated
#: (`badge_leaderboards._linked`), so the index should not carry rows no board can return.
_LINKED = Q(is_linked=True)


class Migration(migrations.Migration):

    # CREATE/DROP INDEX CONCURRENTLY cannot run inside a transaction.
    atomic = False

    dependencies = [
        ("trophies", "0311_partial_indexes_for_scrolled_boards"),
    ]

    operations = [
        # Drop-then-add, because each replacement reuses its own name.
        RemoveIndexConcurrently(model_name="seriesbadgestanding", name="sbs_series_board_idx"),
        RemoveIndexConcurrently(model_name="seriesbadgestanding", name="sbs_series_cc_board_idx"),

        AddIndexConcurrently(
            model_name="seriesbadgestanding",
            index=models.Index(fields=["series_slug", "-xp", "advanced_at", "profile"],
                               name="sbs_series_board_idx", condition=_LINKED),
        ),
        AddIndexConcurrently(
            model_name="seriesbadgestanding",
            index=models.Index(fields=["series_slug", "country_code", "-xp", "advanced_at", "profile"],
                               name="sbs_series_cc_board_idx", condition=_LINKED),
        ),
    ]
