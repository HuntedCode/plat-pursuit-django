# The Spotlight's partial index, built CONCURRENTLY.
#
# Split out of 0008 rather than shipped beside the AddField, and the reason is that pairing them is
# actively worse than either alone: `ALTER TABLE ... ADD COLUMN` takes ACCESS EXCLUSIVE and an atomic
# migration holds it until commit, so a plain `CREATE INDEX` in the same transaction builds its index
# while reads are still locked out. Apart they are a metadata update and a concurrent build, and
# neither blocks the browse page.
#
# Built with AddIndexConcurrently (+ atomic = False), the same pattern as 0257 / 0260 / 0262 / 0307,
# so the index does not write-lock a table the site reads on every browse render.
#
# At deploy time this index covers ZERO rows -- its predicate requires `featured_at IS NOT NULL` and
# nothing is featured yet -- so the build is effectively instant. It is CONCURRENTLY anyway, because
# "this table is small right now" is the reasoning that produces the outage two years later, and the
# cost of being right here is one line.
#
# NOTE: if a CONCURRENTLY build fails partway, Postgres leaves an INVALID index behind that must be
# dropped by hand before re-running:
#
#     DROP INDEX CONCURRENTLY IF EXISTS glst_featured_idx;
from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):

    # CREATE INDEX CONCURRENTLY cannot run inside a transaction.
    atomic = False

    dependencies = [
        ("gamelists", "0008_spotlight"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="gamelist",
            # The predicate mirrors `GameListQuerySet.featured()` exactly -- `public()` (which is
            # `is_deleted=False, is_public=True`) plus the featured test. A partial index whose
            # condition does not match the query's WHERE clause is one Postgres will not use.
            index=models.Index(
                condition=Q(
                    ("featured_at__isnull", False),
                    ("is_deleted", False),
                    ("is_public", True),
                ),
                fields=["-featured_at"],
                name="glst_featured_idx",
            ),
        ),
    ]
