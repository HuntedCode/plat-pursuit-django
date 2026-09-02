# Order the platform groups so Ultra HD (modern PS4/PS5, most users) is the default/first tab on the badge
# detail page; Legacy HD follows. Gaps leave room for a future PS6 group to slot ahead. Admin-editable.
from django.db import migrations


def set_order(apps, schema_editor):
    PlatformGroup = apps.get_model('trophies', 'PlatformGroup')
    PlatformGroup.objects.filter(key='ultra-hd').update(sort_order=10)
    PlatformGroup.objects.filter(key='legacy-hd').update(sort_order=20)


def clear_order(apps, schema_editor):
    PlatformGroup = apps.get_model('trophies', 'PlatformGroup')
    PlatformGroup.objects.filter(key__in=['ultra-hd', 'legacy-hd']).update(sort_order=0)


class Migration(migrations.Migration):

    dependencies = [
        ('trophies', '0274_merge_badge_reroot'),
    ]

    operations = [
        migrations.RunPython(set_order, clear_order),
    ]
