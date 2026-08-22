"""Backfill the role split and the worn-mark denorm.

Every existing is_staff user starts as 'admin' (the pre-split meaning) -- the user demotes
moderators by hand in the Django admin afterwards. Then every profile's display_mark is
computed once through the same resolver the runtime writers use.
"""
from django.db import migrations


def forwards(apps, schema_editor):
    CustomUser = apps.get_model('users', 'CustomUser')
    Profile = apps.get_model('trophies', 'Profile')

    CustomUser.objects.filter(is_staff=True).update(role='admin')

    # Historical models lack methods, so the resolver's precedence is restated as a closed set
    # of bulk UPDATEs (staff > mod > worn supporter level) -- one statement per outcome instead
    # of a per-profile save loop, which at whale scale sat inside the deploy's migrate step.
    from django.db.models import Q

    from users.constants import LADDER_SLUGS, LEGACY_TIER_LEVEL_MAP

    linked = Profile.objects.exclude(user__isnull=True)
    service = Q(user__role='admin') | Q(user__is_staff=True)

    linked.filter(service).update(display_mark='staff')
    linked.filter(user__role='moderator').exclude(service).update(display_mark='mod')
    for slug in LADDER_SLUGS:
        wearing = [slug] + [legacy for legacy, worn in LEGACY_TIER_LEVEL_MAP.items()
                            if worn == slug]
        (linked.filter(user_is_premium=True, user__premium_tier__in=wearing)
               .exclude(service).exclude(user__role='moderator')
               .update(display_mark=slug))
    # Everyone else keeps the schema default ''.


def backwards(apps, schema_editor):
    pass  # the fields themselves are dropped by the schema migrations


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0022_role_and_display_mark'),
        ('trophies', '0315_role_and_display_mark'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
