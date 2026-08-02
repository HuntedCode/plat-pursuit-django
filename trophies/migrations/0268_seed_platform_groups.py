"""Seed the two starting PlatformGroups: Legacy HD (PS3/Vita) and Ultra HD (PS4/PS5).

Reference data for the badge rebuild. Idempotent (get_or_create by key) so it's safe to re-run. The
`platforms` arrays and art keys are admin-editable afterward; a future PS6 tier is a new row, not a migration.
See docs/design/rebuild/badge-backend-rebuild.md.
"""
from django.db import migrations


GROUPS = [
    {
        'key': 'legacy-hd', 'name': 'Legacy HD', 'platforms': ['PS3', 'PSVITA'],
        'exclude_delisted': False,  # delisting is a no-op here -> immune to the PS3/Vita store closure
        'sort_order': 1,
    },
    {
        'key': 'ultra-hd', 'name': 'Ultra HD', 'platforms': ['PS4', 'PS5'],
        'exclude_delisted': True,   # delisted games don't gate (they still satisfy) -> less friction
        'sort_order': 2,
    },
]


def seed(apps, schema_editor):
    PlatformGroup = apps.get_model('trophies', 'PlatformGroup')
    for g in GROUPS:
        PlatformGroup.objects.get_or_create(
            key=g['key'],
            defaults={
                'name': g['name'], 'platforms': g['platforms'],
                'exclude_delisted': g['exclude_delisted'], 'sort_order': g['sort_order'],
            },
        )


def unseed(apps, schema_editor):
    PlatformGroup = apps.get_model('trophies', 'PlatformGroup')
    PlatformGroup.objects.filter(key__in=[g['key'] for g in GROUPS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('trophies', '0267_platformgroup_badgeseries_groupbadge_usergroupbadge_and_more'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
