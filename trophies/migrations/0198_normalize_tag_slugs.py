"""Normalize Genre/Theme/GameEngine slugs that were stored with URL-unsafe
characters (most commonly parentheses from IGDB slugs like
"ctg-(core-technology-group)"). Django's default ``<slug>`` URL converter
only allows ``[-a-zA-Z0-9_]``, so those rows would 500 when their detail
page URL was reversed.

Idempotent: already-clean slugs pass through ``slugify`` unchanged.
Collision handling: if the cleaned slug collides with another row of the
same model, disambiguate with the igdb_id suffix (matches the
``_create_concept_franchises`` slug-collision recovery pattern).
"""

from django.db import migrations
from django.utils.text import slugify


def _clean_slugs_for(model):
    seen_slugs = set(model.objects.values_list('slug', flat=True))
    updates = []
    for row in model.objects.all():
        cleaned = slugify(row.slug) or slugify(row.name)
        if not cleaned:
            continue
        if cleaned == row.slug:
            continue
        if cleaned in seen_slugs:
            cleaned = f'{cleaned}-{row.igdb_id}'
        seen_slugs.discard(row.slug)
        seen_slugs.add(cleaned)
        row.slug = cleaned
        updates.append(row)
    if updates:
        model.objects.bulk_update(updates, ['slug'])


def forwards(apps, schema_editor):
    Genre = apps.get_model('trophies', 'Genre')
    Theme = apps.get_model('trophies', 'Theme')
    GameEngine = apps.get_model('trophies', 'GameEngine')
    _clean_slugs_for(Genre)
    _clean_slugs_for(Theme)
    _clean_slugs_for(GameEngine)


class Migration(migrations.Migration):

    dependencies = [
        ('trophies', '0197_fix_franchise_igdb_id_uniqueness'),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
