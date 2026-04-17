from django.db import migrations


def seed_platinum_cases(apps, schema_editor):
    """Seed ProfileShowcase entries for every profile that has a UserTrophySelection.

    Gives existing users continuity: their configured trophy case stays visible
    after the showcase system ships. New users start with no showcases until
    they configure them via the Phase B editor.
    """
    UserTrophySelection = apps.get_model('trophies', 'UserTrophySelection')
    ProfileShowcase = apps.get_model('trophies', 'ProfileShowcase')

    profile_ids = list(
        UserTrophySelection.objects
        .values_list('profile_id', flat=True)
        .distinct()
    )
    if not profile_ids:
        return

    rows = [
        ProfileShowcase(
            profile_id=pid,
            showcase_type='platinum_case',
            sort_order=1,
            is_active=True,
            config={},
        )
        for pid in profile_ids
    ]
    ProfileShowcase.objects.bulk_create(rows, ignore_conflicts=True)


def reverse_seed(apps, schema_editor):
    """Remove the seeded platinum_case showcases."""
    ProfileShowcase = apps.get_model('trophies', 'ProfileShowcase')
    ProfileShowcase.objects.filter(showcase_type='platinum_case').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('trophies', '0201_profileshowcase'),
    ]

    operations = [
        migrations.RunPython(seed_platinum_cases, reverse_seed),
    ]
