"""`Profile.total_trophies_raw` -- an UNFILTERED trophy total, and the Trophies board moved onto it.

WHY. `Profile.total_trophies` is filter-respecting: `update_profile_trophy_counts` honours the owner's
`hide_hiddens` setting when it writes. The Trophies board sorted on `total_plats` (unfiltered) and broke
ties on that (filtered), so two hunters level on platinums were separated by a rule one of them had
configured privately. A board figure has to mean the same thing on every row.

It is also the less trustworthy of the two. Being filter-respecting is exactly what stops
`recalc_profile_counters` reconciling `total_trophies` -- a cron cannot recompute it without reading each
profile's settings -- so a missed write there persists until that hunter next syncs. The new column is
maintained like the four type counters: incrementally by the `EarnedTrophy` signals AND reconciled
nightly from ground truth.

THE BACKFILL IS EXACT AND CHEAP, which is the reason it can live in the migration rather than in a
deploy-checklist row. Every earned trophy is exactly one of bronze/silver/gold/platinum, and those four
counters are already unfiltered -- so the new value is their SUM, and the whole backfill is one
arithmetic UPDATE over columns that already exist. No aggregate over `EarnedTrophy`, no chunking, and no
window where the board reads zeros: the column is populated before the indexes that serve the board are
built.

ORDER MATTERS: add the column, fill it, THEN swap the indexes. The replacements are partial on
`total_trophies_raw > 0`, so building them against an all-zero column would build them empty and then
grow them row by row during the UPDATE.

CONCURRENTLY + `atomic = False`, exactly as 0307/0309 do, so a 300k-row `Profile` is not write-locked for
the rebuild. NOTE: if a CONCURRENTLY build fails partway, Postgres leaves an INVALID index behind that
must be dropped by hand before re-running:

    DROP INDEX CONCURRENTLY IF EXISTS profile_board_idx;
    DROP INDEX CONCURRENTLY IF EXISTS profile_board_cc_idx;

Verify afterwards that both exist and are valid:

    SELECT indexrelid::regclass, indisvalid FROM pg_index WHERE indexrelid::regclass::text IN
      ('profile_board_idx','profile_board_cc_idx');
"""

from django.contrib.postgres.operations import AddIndexConcurrently, RemoveIndexConcurrently
from django.db import migrations, models
from django.db.models import F, Q

#: The board's population: verified hunters who hold at least one trophy, on the UNFILTERED count.
_BOARD = Q(is_linked=True, total_trophies_raw__gt=0)


def fill(apps, schema_editor):
    """One UPDATE. Exact by construction -- every earned trophy is exactly one of the four types."""
    Profile = apps.get_model('trophies', 'Profile')
    Profile.objects.update(
        total_trophies_raw=F('total_bronzes') + F('total_silvers') + F('total_golds') + F('total_plats')
    )


def unfill(apps, schema_editor):
    """Nothing to undo: the column goes with the reverse of the AddField below."""


class Migration(migrations.Migration):

    # CREATE/DROP INDEX CONCURRENTLY cannot run inside a transaction.
    atomic = False

    dependencies = [
        ("trophies", "0332_profile_trophy_standing"),
    ]

    operations = [
        migrations.AddField(
            model_name="profile",
            name="total_trophies_raw",
            field=models.PositiveIntegerField(
                default=0,
                help_text="All earned trophies, ignoring hide_hiddens. The leaderboard sort figure.",
            ),
        ),
        migrations.RunPython(fill, unfill),
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
