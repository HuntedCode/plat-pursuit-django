"""Assign each of the 25 jobs a Lucide icon (the visual-design pass the seed left open).
Icon names resolve through trophies.templatetags.job_icons. Idempotent (update by slug)."""
from django.db import migrations

# job slug -> Lucide icon name (see trophies/templatetags/job_icons.py for the glyphs)
ICONS = {
    # Combat
    'slayer': 'swords', 'gunslinger': 'crosshair', 'vanguard': 'shield',
    'outlaw': 'skull', 'warrior': 'sword',
    # Exploration
    'pathfinder': 'compass', 'infiltrator': 'footprints', 'cartographer': 'map',
    'mascot': 'smile', 'survivalist': 'tent',
    # Mind
    'mastermind': 'brain', 'tactician': 'flag', 'architect': 'blocks',
    'tycoon': 'coins', 'card-shark': 'spade',
    # Heart
    'mage': 'wand-sparkles', 'champion': 'medal', 'librarian': 'library',
    'jester': 'drama', 'exorcist': 'ghost',
    # Finesse
    'gamer': 'gamepad-2', 'driver': 'gauge', 'athlete': 'dumbbell',
    'maestro': 'music', 'freelancer': 'briefcase',
}


def set_icons(apps, schema_editor):
    Job = apps.get_model('trophies', 'Job')
    for slug, icon in ICONS.items():
        Job.objects.filter(slug=slug).update(icon=icon)


def clear_icons(apps, schema_editor):
    Job = apps.get_model('trophies', 'Job')
    Job.objects.filter(slug__in=ICONS).update(icon='')


class Migration(migrations.Migration):
    dependencies = [
        ('trophies', '0255_contractxpgrant_source_contractxpgrant_source_id_and_more'),
    ]
    operations = [migrations.RunPython(set_icons, clear_icons)]
