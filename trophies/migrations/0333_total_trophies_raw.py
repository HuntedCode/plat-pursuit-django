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

THE BACKFILL IS EXACT. Every earned trophy is exactly one of bronze/silver/gold/platinum, and those four
counters are already unfiltered -- so the new value is their SUM, and the fill is one arithmetic UPDATE
over columns that already exist. No aggregate over `EarnedTrophy`, no chunking, no deploy-checklist row.

It is NARROWED to profiles that actually hold a trophy. An unfiltered `UPDATE` would rewrite all ~300k
rows of a 48-column table -- taking a row lock on every one, doubling the table's dead tuples right
before an index build has to scan it, and blocking the concurrent `Profile.objects.filter(pk=...)
.update(...)` that the `EarnedTrophy` signals issue during sync. About 250k of those rows are profiles
whose four counters are all zero, where the computed value equals the column default and the write buys
nothing. Excluding them is exactly equivalent and cuts the write set to the linked population.

THE INDEX SWAP LIVES IN 0334, deliberately not here. `CREATE INDEX CONCURRENTLY` needs `atomic = False`,
and under `atomic = False` Django commits each operation separately but only records the migration as
applied after the LAST one. So a CONCURRENTLY build that died partway -- the failure this migration is
most likely to hit -- would leave `migrate` wanting to replay from operation 0, where `AddField` fails
with "column already exists". The operator would be stuck hand-dropping a freshly backfilled column at
the worst possible moment. Split, the backfill is transactional and never has to be redone, and the index
migration re-runs cleanly after the documented cleanup.
"""

from django.db import migrations, models
from django.db.models import F


def fill(apps, schema_editor):
    """One UPDATE, over the profiles that hold a trophy. Exact by construction -- every earned trophy is
    exactly one of the four types, and a profile with none of them is already correct at the default."""
    Profile = apps.get_model('trophies', 'Profile')
    (Profile.objects
     .exclude(total_bronzes=0, total_silvers=0, total_golds=0, total_plats=0)
     .update(total_trophies_raw=(
         F('total_bronzes') + F('total_silvers') + F('total_golds') + F('total_plats'))))


def unfill(apps, schema_editor):
    """Nothing to undo: the column goes with the reverse of the AddField above."""


class Migration(migrations.Migration):

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
    ]
