# Starts celebration moments for every hunter who is ALREADY here, so the deploy does not hand them the
# first-sync welcome summary meant for new hunters (trophies/services/moment_service.py). That is every profile
# with a site account, whatever its sync state ('synced', 'error', or a veteran's refresh running at deploy),
# EXCEPT one whose FIRST sync is still running: syncing, and holding no badge yet (badges are awarded at the
# end of a sync, so a veteran mid-refresh with badges is stamped). That one gets its welcome when it finishes.
# A veteran mid-refresh with no badges at all gets a welcome too, which is harmless. Profiles without a site
# account are left null: nobody can watch their moments.
#
# One UPDATE over profiles with an account (a small share of the table), no rows loaded into Python.

from django.db import migrations
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone


def start_moments_for_existing_hunters(apps, schema_editor):
    Profile = apps.get_model('trophies', 'Profile')
    UserGroupBadge = apps.get_model('trophies', 'UserGroupBadge')
    has_badge = Exists(UserGroupBadge.objects.filter(profile_id=OuterRef('pk')))
    Profile.objects.filter(user__isnull=False, moments_since__isnull=True).exclude(
        Q(sync_status='syncing') & ~Q(has_badge),
    ).update(moments_since=timezone.now())


class Migration(migrations.Migration):

    dependencies = [
        ('trophies', '0351_moments'),
    ]

    operations = [
        migrations.RunPython(start_moments_for_existing_hunters, migrations.RunPython.noop),
    ]
