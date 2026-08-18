"""Partial indexes for the PER-ENTITY boards, and two dead indexes dropped.

WHY 0309 SKIPPED THESE. It made the three whole-table board indexes partial and deliberately left the
per-entity ones (series, edition, earners) alone, reasoning that a leading key -- `series_slug`,
`platform_group_key`, `group_badge` -- already narrows those reads to one entity's rows, so evaluating
`is_linked` from the heap over that handful is cheap.

WHY THAT NO LONGER HOLDS. It assumed PAGINATION. These boards are moving to virtual scrolling, where a
reader can be at row 30,000 of a popular series and the scan walks the index fetching `is_linked` per
candidate on the way -- the exact shape 0307 measured:

    trophy_rank   16.0 ms, planner abandons the index, seq-scans a  ->  3.9 ms, index only
                  48-column table -- on EVERY authenticated page view

So the reasoning was right for the design it was written against, and the design changed under it.

THE `profile` TAIL is not padding. `badge_leaderboards` numbers a page by SLOT and computes a rank by
COUNTING everyone ahead, and those two agree only because each board's ordering ends in a unique key.
Putting that key in the index lets the rank COUNT be index-only instead of a sort.

TWO INDEXES DROPPED, not replaced: `sbs_series_xp_idx` and `sbs_series_cc_xp_idx` ordered by `-xp` for
`series_xp_rows`, a per-series XP board deleted in the 2026-08 audit for having no caller. The indexes
outlived the board and were pure write cost on a table every badge evaluation writes. Nothing reads that
ordering -- `sbs_series_board_idx` leads with the same `series_slug`, so the filter half is still served.

CONCURRENTLY + `atomic = False`, as 0307 / 0309 / 0310 do, so the rebuild does not write-lock tables the
sync workers and the badge evaluation are writing.

NOTE: if a CONCURRENTLY build fails partway, Postgres leaves an INVALID index behind that must be dropped
by hand before re-running:

    DROP INDEX CONCURRENTLY IF EXISTS sbs_series_board_idx;
    DROP INDEX CONCURRENTLY IF EXISTS sbs_series_cc_board_idx;
    DROP INDEX CONCURRENTLY IF EXISTS pes_ed_xp_idx;
    DROP INDEX CONCURRENTLY IF EXISTS pes_ed_cc_xp_idx;
    DROP INDEX CONCURRENTLY IF EXISTS ugb_badge_earned_idx;
    DROP INDEX CONCURRENTLY IF EXISTS ugb_badge_cc_earned_idx;

Verify afterwards that all six exist and are valid (and that the two dropped ones are gone):

    SELECT indexrelid::regclass, indisvalid FROM pg_index WHERE indexrelid::regclass::text IN
      ('sbs_series_board_idx','sbs_series_cc_board_idx','pes_ed_xp_idx','pes_ed_cc_xp_idx',
       'ugb_badge_earned_idx','ugb_badge_cc_earned_idx','sbs_series_xp_idx','sbs_series_cc_xp_idx');
"""

from django.contrib.postgres.operations import AddIndexConcurrently, RemoveIndexConcurrently
from django.db import migrations, models
from django.db.models import Q

#: The per-entity boards apply `is_linked` alone; the edition board additionally applies the `> 0`
#: membership rule its `xp_rows` read carries. Earners has no quantity -- holding the badge IS the rule.
_LINKED = Q(is_linked=True)
_LINKED_XP = Q(is_linked=True, total_xp__gt=0)


class Migration(migrations.Migration):

    # CREATE/DROP INDEX CONCURRENTLY cannot run inside a transaction.
    atomic = False

    dependencies = [
        ("trophies", "0310_earners_country_mirror"),
    ]

    operations = [
        # Dead: their board was deleted and they were never replaced.
        RemoveIndexConcurrently(model_name="seriesbadgestanding", name="sbs_series_xp_idx"),
        RemoveIndexConcurrently(model_name="seriesbadgestanding", name="sbs_series_cc_xp_idx"),

        # Drop-then-add, because every replacement reuses its own name.
        RemoveIndexConcurrently(model_name="seriesbadgestanding", name="sbs_series_board_idx"),
        RemoveIndexConcurrently(model_name="seriesbadgestanding", name="sbs_series_cc_board_idx"),
        RemoveIndexConcurrently(model_name="profileeditionstanding", name="pes_ed_xp_idx"),
        RemoveIndexConcurrently(model_name="profileeditionstanding", name="pes_ed_cc_xp_idx"),
        RemoveIndexConcurrently(model_name="usergroupbadge", name="ugb_badge_earned_idx"),
        RemoveIndexConcurrently(model_name="usergroupbadge", name="ugb_badge_cc_earned_idx"),

        AddIndexConcurrently(
            model_name="seriesbadgestanding",
            index=models.Index(fields=["series_slug", "-progress_bp", "advanced_at", "profile"],
                               name="sbs_series_board_idx", condition=_LINKED),
        ),
        AddIndexConcurrently(
            model_name="seriesbadgestanding",
            index=models.Index(fields=["series_slug", "country_code", "-progress_bp", "advanced_at", "profile"],
                               name="sbs_series_cc_board_idx", condition=_LINKED),
        ),
        AddIndexConcurrently(
            model_name="profileeditionstanding",
            index=models.Index(fields=["platform_group_key", "-total_xp", "profile"],
                               name="pes_ed_xp_idx", condition=_LINKED_XP),
        ),
        AddIndexConcurrently(
            model_name="profileeditionstanding",
            index=models.Index(fields=["platform_group_key", "country_code", "-total_xp", "profile"],
                               name="pes_ed_cc_xp_idx", condition=_LINKED_XP),
        ),
        AddIndexConcurrently(
            model_name="usergroupbadge",
            index=models.Index(fields=["group_badge", "earned_at", "profile"],
                               name="ugb_badge_earned_idx", condition=_LINKED),
        ),
        AddIndexConcurrently(
            model_name="usergroupbadge",
            index=models.Index(fields=["group_badge", "country_code", "earned_at", "profile"],
                               name="ugb_badge_cc_earned_idx", condition=_LINKED),
        ),
    ]
