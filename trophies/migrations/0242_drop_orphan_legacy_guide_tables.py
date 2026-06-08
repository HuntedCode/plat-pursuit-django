"""Drop orphaned legacy tables left behind by removed models.

The pre-Roadmap "Guide" system (Guide, GuideSection, GuideImage, GuideRating,
GuideTag + the guide<->tag M2M) and the standalone AuthorTrust model were
removed from the codebase, but a migration-history squash/renumber left their
physical tables behind in older databases. With no Django model mapping to
them, the deletion collector never cascades through their foreign keys, so
deleting a referenced Game raised an IntegrityError on the dangling
`trophies_guide.game_id` constraint.

These tables are unknown to Django (confirmed via introspection: present in the
DB, absent from every model's db_table and M2M through-table). `DROP TABLE IF
EXISTS ... CASCADE` makes this a no-op on databases where they were already
gone, so it is safe across every environment. The reverse is a deliberate
no-op: the models no longer exist, so there is nothing to recreate.
"""
from django.db import migrations


ORPHAN_TABLES = [
    'trophies_guide_tags',   # M2M through table; drop before its endpoints
    'trophies_guidesection',
    'trophies_guideimage',
    'trophies_guiderating',
    'trophies_guidetag',
    'trophies_guide',
    'trophies_authortrust',
]

DROP_SQL = '\n'.join(
    f'DROP TABLE IF EXISTS "{table}" CASCADE;' for table in ORPHAN_TABLES
)


class Migration(migrations.Migration):

    dependencies = [
        ('trophies', '0241_developer_reputation'),
    ]

    operations = [
        migrations.RunSQL(sql=DROP_SQL, reverse_sql=migrations.RunSQL.noop),
    ]
