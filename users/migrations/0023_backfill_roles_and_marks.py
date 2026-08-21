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

    # Historical models lack methods, so the resolver is inlined against the same constants.
    from users.constants import LADDER_SLUGS, LEGACY_TIER_LEVEL_MAP

    profiles = Profile.objects.select_related('user').exclude(user__isnull=True)
    for profile in profiles.iterator(chunk_size=500):
        user = profile.user
        if user.role == 'admin' or user.is_staff:
            mark = 'staff'
        elif user.role == 'moderator':
            mark = 'mod'
        elif profile.user_is_premium and user.premium_tier:
            mark = (user.premium_tier if user.premium_tier in LADDER_SLUGS
                    else LEGACY_TIER_LEVEL_MAP.get(user.premium_tier, '')) or ''
        else:
            mark = ''
        if profile.display_mark != mark:
            profile.display_mark = mark
            profile.save(update_fields=['display_mark'])


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
