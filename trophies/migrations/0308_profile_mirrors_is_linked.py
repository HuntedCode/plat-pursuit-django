"""Mirror `Profile.is_linked` onto every store a board reads, and backfill it.

WHY A COLUMN AND NOT A JOIN. `badge_leaderboards._linked()` gates every board's population on
`is_linked`, and until now did so by joining `Profile`. The join is by primary key, so the planner reads
`is_linked` out of the heap of a 48-column table -- once per candidate row, on public uncached pages.
Migration 0307 fixed exactly this for the Trophies board by making its indexes PARTIAL on the predicate,
and measured it:

    trophy_rank   16.0 ms, planner abandons the index, seq-scans a  ->  3.9 ms, index only
                  48-column table -- on EVERY authenticated page view
    board_count   10.0 ms, parallel seq scan of 300k                ->  4.2 ms, index only

0307 could only reach Trophies, which reads `Profile` directly. The other five boards read standing
tables, where the predicate was not available to put in an index. This column makes it available; 0309
builds the partial indexes.

Same reasoning as the `country_code` mirror these sit beside, and the same two-path freshness rule: every
recompute seam stamps it on the rows it writes, and `signals._propagate_profile_flags_to_standings`
catches the edge those miss -- a hunter VERIFYING, which moves the value with no recompute behind it.

THE BACKFILL IS NOT OPTIONAL. The column defaults to False and `_linked()` reads it directly, so between
the AddField and the backfill EVERY BOARD ON THE SITE IS EMPTY. It is set-based `UPDATE ... FROM`, one
statement per table: CLAUDE.md's note that `.update()` cannot take `F('fk__field')` rules out the obvious
ORM spelling, and a correlated `Subquery` per row is the slow shape on tables this size.

Kept SEPARATE from 0309's index builds because those must run `CONCURRENTLY` (`atomic = False`), and a
backfill that cannot roll back alongside its own schema change is the worse trade.

`UserGroupBadge` is in the list and carries NO `country_code`, unlike the other five. That is not a
decision anyone recorded -- the earners board is simply the one board with no country slice
(`earners_rows` / `earners_rank` take no `country`), and `UserGroupBadge` predates the Lane B standing
stores that established the mirror pattern. `is_linked` is different in kind and belongs on all six: a
country slice is a filter the reader opts into, while this is the population rule every board applies
unconditionally.
"""

from django.db import migrations, models

#: Every store a board reads. Order is irrelevant; the list is spelled out rather than derived so the
#: SQL below is greppable from the table name alone.
_STORES = [
    'trophies_profilebadgestanding',
    'trophies_profilecareerstanding',
    'trophies_profileeditionstanding',
    'trophies_seriesbadgestanding',
    'trophies_profilejobxp',
    'trophies_usergroupbadge',
]

_BACKFILL = '\n'.join(
    'UPDATE {t} s SET is_linked = p.is_linked '
    'FROM trophies_profile p WHERE s.profile_id = p.id;'.format(t=t)
    for t in _STORES
)

#: A pre-existing `country_code` bug, repaired here because this is the migration already touching the
#: table. ProfileJobXP rows were created with `country_code = ''` at BOTH creation sites, and the
#: propagation signal fires only when a Profile's country CHANGES -- so a hunter whose country never moved
#: after their first XP grant has been invisible to the country-sliced job board since the column landed.
#: The write sites now stamp it on create; this repairs the rows already out there.
_REPAIR_JOB_COUNTRY = (
    "UPDATE trophies_profilejobxp s SET country_code = COALESCE(p.country_code, '') "
    "FROM trophies_profile p WHERE s.profile_id = p.id "
    "AND s.country_code IS DISTINCT FROM COALESCE(p.country_code, '');"
)


class Migration(migrations.Migration):

    dependencies = [
        ("trophies", "0307_partial_board_indexes"),
    ]

    operations = [
        migrations.AddField(
            model_name="profilebadgestanding",
            name="is_linked",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="profilecareerstanding",
            name="is_linked",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="profileeditionstanding",
            name="is_linked",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="profilejobxp",
            name="is_linked",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="seriesbadgestanding",
            name="is_linked",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="usergroupbadge",
            name="is_linked",
            field=models.BooleanField(default=False),
        ),
        # Reverse is a no-op: the columns go away with the AddFields above, so there is nothing to undo
        # separately. The country repair is likewise not reversible -- and must not be, since the value it
        # writes is the correct one.
        migrations.RunSQL(_BACKFILL, migrations.RunSQL.noop),
        migrations.RunSQL(_REPAIR_JOB_COUNTRY, migrations.RunSQL.noop),
    ]
