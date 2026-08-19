"""Make the standing-store board indexes PARTIAL on the board's own population.

The payoff for 0308's `is_linked` column, and the same fix migration 0307 applied to `Profile` -- now
reaching the five boards that read standing tables rather than reading `Profile` directly. 0307's measured
numbers on a synthetic 300k-profile / 50k-linked shape:

    trophy_rank   16.0 ms, planner abandons the index, seq-scans a  ->  3.9 ms, index only
    board_count   10.0 ms, parallel seq scan of 300k                ->  4.2 ms, index only

Each index gains BOTH halves of its board's membership rule (`is_linked` AND `total_xp > 0`) as the
condition, and `profile` as the tail. The tail is not decoration: `badge_leaderboards` numbers a page by
SLOT and computes a rank by COUNTING everyone ahead, and those two agree only because every board's
ordering ends in a unique key. Putting that key in the index is what lets the rank COUNT be index-only
instead of a sort.

WHICH STORES, AND WHY NOT ALL SIX. Only the three whose board reads are NOT already narrowed by a leading
column:

  ProfileBadgeStanding   Badge Points -- ordered by total_xp across the whole table
  ProfileCareerStanding  Career XP    -- likewise
  ProfileJobXP           at the time, `job_board_counts` grouped across EVERY job on each `/jobs/`
                         render. That caller is gone (2026-08: the catalogue card dropped its hunter
                         count), but the index stands on the job BOARD itself, which orders by
                         `total_xp` within one job and pages through it

`SeriesBadgeStanding`, `ProfileEditionStanding` and `UserGroupBadge` are read per entity (`series_slug`,
`platform_group_key`, `group_badge`), so their existing indexes already restrict to one entity's rows
before `is_linked` is considered, and a heap filter over that is cheap. They carry the column for
CORRECTNESS -- `_linked()` reads it on every store -- and if one of those boards ever goes hot the index
is a one-line follow-up rather than another backfill.

`ProfileBadgeStanding.total_xp` and `ProfileCareerStanding.total_xp` keep their plain `db_index`: it still
serves non-board reads, and dropping a field-level index means altering the field, which is churn for no
gain.

CONCURRENTLY + `atomic = False`, exactly as 0307/0257/0260/0262 do, so the rebuild does not write-lock
tables the sync workers are writing.

NOTE: if a CONCURRENTLY build fails partway, Postgres leaves an INVALID index behind that must be dropped
by hand before re-running:

    DROP INDEX CONCURRENTLY IF EXISTS pbs_board_idx;
    DROP INDEX CONCURRENTLY IF EXISTS pbs_country_xp_idx;
    DROP INDEX CONCURRENTLY IF EXISTS pcs_board_idx;
    DROP INDEX CONCURRENTLY IF EXISTS pcs_country_xp_idx;
    DROP INDEX CONCURRENTLY IF EXISTS profilejobxp_job_xp_idx;
    DROP INDEX CONCURRENTLY IF EXISTS pjx_job_cc_xp_idx;

Verify afterwards that all six exist and are valid:

    SELECT indexrelid::regclass, indisvalid FROM pg_index WHERE indexrelid::regclass::text IN
      ('pbs_board_idx','pbs_country_xp_idx','pcs_board_idx','pcs_country_xp_idx',
       'profilejobxp_job_xp_idx','pjx_job_cc_xp_idx');
"""

from django.contrib.postgres.operations import AddIndexConcurrently, RemoveIndexConcurrently
from django.db import migrations, models
from django.db.models import Q

#: Both halves of the membership rule the boards apply. `is_linked` is the population gate; `> 0` is the
#: board's own rule (a standing row exists at zero XP, and no board shows those).
_BOARD = Q(is_linked=True, total_xp__gt=0)


class Migration(migrations.Migration):

    # CREATE/DROP INDEX CONCURRENTLY cannot run inside a transaction.
    atomic = False

    dependencies = [
        ("trophies", "0308_profile_mirrors_is_linked"),
    ]

    operations = [
        # Drop first: three of the replacements reuse the same names, and Postgres will not allow a
        # duplicate.
        RemoveIndexConcurrently(model_name="profilebadgestanding", name="pbs_country_xp_idx"),
        RemoveIndexConcurrently(model_name="profilecareerstanding", name="pcs_country_xp_idx"),
        RemoveIndexConcurrently(model_name="profilejobxp", name="profilejobxp_job_xp_idx"),
        RemoveIndexConcurrently(model_name="profilejobxp", name="pjx_job_cc_xp_idx"),

        AddIndexConcurrently(
            model_name="profilebadgestanding",
            index=models.Index(fields=["-total_xp", "profile"], name="pbs_board_idx", condition=_BOARD),
        ),
        AddIndexConcurrently(
            model_name="profilebadgestanding",
            index=models.Index(fields=["country_code", "-total_xp", "profile"],
                               name="pbs_country_xp_idx", condition=_BOARD),
        ),
        AddIndexConcurrently(
            model_name="profilecareerstanding",
            index=models.Index(fields=["-total_xp", "profile"], name="pcs_board_idx", condition=_BOARD),
        ),
        AddIndexConcurrently(
            model_name="profilecareerstanding",
            index=models.Index(fields=["country_code", "-total_xp", "profile"],
                               name="pcs_country_xp_idx", condition=_BOARD),
        ),
        AddIndexConcurrently(
            model_name="profilejobxp",
            index=models.Index(fields=["job", "-total_xp", "profile"],
                               name="profilejobxp_job_xp_idx", condition=_BOARD),
        ),
        AddIndexConcurrently(
            model_name="profilejobxp",
            index=models.Index(fields=["job", "country_code", "-total_xp", "profile"],
                               name="pjx_job_cc_xp_idx", condition=_BOARD),
        ),
    ]
