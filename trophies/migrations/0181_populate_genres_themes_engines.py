"""Data migration: parse raw_response from IGDBMatch records to populate
normalized Genre, Theme, GameEngine tables and their Concept through models."""

import logging
from django.db import migrations, IntegrityError
from django.utils.text import slugify

logger = logging.getLogger(__name__)


def populate_normalized_tags(apps, schema_editor):
    IGDBMatch = apps.get_model('trophies', 'IGDBMatch')
    Genre = apps.get_model('trophies', 'Genre')
    Theme = apps.get_model('trophies', 'Theme')
    GameEngine = apps.get_model('trophies', 'GameEngine')
    ConceptGenre = apps.get_model('trophies', 'ConceptGenre')
    ConceptTheme = apps.get_model('trophies', 'ConceptTheme')
    ConceptEngine = apps.get_model('trophies', 'ConceptEngine')

    # Caches to avoid repeated get_or_create calls
    genre_cache = {}    # igdb_id -> Genre pk
    theme_cache = {}    # igdb_id -> Theme pk
    engine_cache = {}   # igdb_id -> GameEngine pk

    matches = IGDBMatch.objects.filter(
        raw_response__isnull=False,
        status__in=['auto_accepted', 'accepted'],
    ).select_related('concept').iterator(chunk_size=500)

    genres_created = 0
    themes_created = 0
    engines_created = 0
    links_created = 0

    for match in matches:
        raw = match.raw_response
        if not raw or not isinstance(raw, dict):
            continue

        concept = match.concept
        if not concept:
            continue

        # --- Genres ---
        for genre_data in raw.get('genres', []):
            igdb_id = genre_data.get('id')
            name = genre_data.get('name', '')
            slug = genre_data.get('slug', '')
            if not igdb_id or not name:
                continue

            if igdb_id not in genre_cache:
                try:
                    obj, created = Genre.objects.get_or_create(
                        igdb_id=igdb_id,
                        defaults={'name': name, 'slug': slug or slugify(name)},
                    )
                except IntegrityError:
                    obj = Genre.objects.filter(slug=slug or slugify(name)).first()
                    if not obj:
                        continue
                    created = False
                genre_cache[igdb_id] = obj.pk
                if created:
                    genres_created += 1

            _, created = ConceptGenre.objects.get_or_create(
                concept=concept, genre_id=genre_cache[igdb_id],
            )
            if created:
                links_created += 1

        # --- Themes ---
        for theme_data in raw.get('themes', []):
            igdb_id = theme_data.get('id')
            name = theme_data.get('name', '')
            slug = theme_data.get('slug', '')
            if not igdb_id or not name:
                continue

            if igdb_id not in theme_cache:
                try:
                    obj, created = Theme.objects.get_or_create(
                        igdb_id=igdb_id,
                        defaults={'name': name, 'slug': slug or slugify(name)},
                    )
                except IntegrityError:
                    obj = Theme.objects.filter(slug=slug or slugify(name)).first()
                    if not obj:
                        continue
                    created = False
                theme_cache[igdb_id] = obj.pk
                if created:
                    themes_created += 1

            _, created = ConceptTheme.objects.get_or_create(
                concept=concept, theme_id=theme_cache[igdb_id],
            )
            if created:
                links_created += 1

        # --- Engines ---
        for engine_data in raw.get('game_engines', []):
            igdb_id = engine_data.get('id')
            name = engine_data.get('name', '')
            slug = engine_data.get('slug', '')
            if not igdb_id or not name:
                continue

            if igdb_id not in engine_cache:
                try:
                    obj, created = GameEngine.objects.get_or_create(
                        igdb_id=igdb_id,
                        defaults={'name': name, 'slug': slug or slugify(name)},
                    )
                except IntegrityError:
                    obj = GameEngine.objects.filter(slug=slug or slugify(name)).first()
                    if not obj:
                        continue
                    created = False
                engine_cache[igdb_id] = obj.pk
                if created:
                    engines_created += 1

            _, created = ConceptEngine.objects.get_or_create(
                concept=concept, engine_id=engine_cache[igdb_id],
            )
            if created:
                links_created += 1

    logger.info(
        "Normalized IGDB tags: %d genres, %d themes, %d engines created; %d links total.",
        genres_created, themes_created, engines_created, links_created,
    )


def reverse_noop(apps, schema_editor):
    """Reverse is handled by dropping the tables in the schema migration."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("trophies", "0180_genre_theme_engine_normalization"),
    ]

    operations = [
        migrations.RunPython(populate_normalized_tags, reverse_noop),
    ]
