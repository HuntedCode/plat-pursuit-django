"""The last of the six board stores gets its `country_code` mirror, and its country-sliced index.

`UserGroupBadge` was the one store a board reads that carried no country mirror. That was not a decision
anyone recorded: it is the badge EARN-LIFECYCLE table, written by `badge_apply`, and it predates the Lane
B standing stores that established the pattern -- so it got read as a board without ever being reframed
as one. The rebuild spec's stated intent was that country slices every board.

WHAT THIS BUYS, precisely: a country-scoped `earners_rank`. "4th in your country to earn this" reads
better on a medallion back than "#847 worldwide", and `earners_rank` is already rendered in four places.
It does NOT buy a sliceable list -- `earners_rows` has no production caller, so the earners board is a
stat rather than a surface.

The index is `(group_badge, country_code, earned_at)`: always-filtered key, then the slice, then the
sort -- the same column order every other board's country index uses. It is built CONCURRENTLY alongside
the AddField because `UserGroupBadge` is written by every badge award.

The backfill is set-based, like 0308's. Without it every existing row reads `''`, and a hunter's
country-sliced earn rank would silently exclude every badge they earned before this deploy.

NOT partial on `is_linked`, unlike 0309's three: the leading `group_badge` already narrows this to one
badge's holders, so the flag is a cheap heap filter over a small set. Same reasoning that left the other
per-entity stores on plain indexes.
"""

import django.contrib.postgres.operations as pg
from django.db import migrations, models

_BACKFILL = (
    "UPDATE trophies_usergroupbadge u SET country_code = COALESCE(p.country_code, '') "
    "FROM trophies_profile p WHERE u.profile_id = p.id "
    "AND u.country_code IS DISTINCT FROM COALESCE(p.country_code, '');"
)


class Migration(migrations.Migration):

    # CREATE INDEX CONCURRENTLY cannot run inside a transaction.
    atomic = False

    dependencies = [
        ("trophies", "0309_partial_board_indexes_on_standings"),
    ]

    operations = [
        migrations.AddField(
            model_name="usergroupbadge",
            name="country_code",
            field=models.CharField(blank=True, db_index=True, default="", max_length=5),
        ),
        # Backfill BEFORE the composite index, so the build sees final values rather than a table of ''.
        migrations.RunSQL(_BACKFILL, migrations.RunSQL.noop),
        pg.AddIndexConcurrently(
            model_name="usergroupbadge",
            index=models.Index(fields=["group_badge", "country_code", "earned_at"],
                               name="ugb_badge_cc_earned_idx"),
        ),
    ]
