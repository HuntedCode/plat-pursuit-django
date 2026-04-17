"""Re-enrich existing ``GameEngine`` rows from cached ``IGDBMatch.raw_response``
and prune extra ``ConceptEngine`` links down to the first engine per concept.

Context: IGDB conflates runtime engines with dev tools — Sagebrush's
``game_engines`` array is ``[Unity, Audacity, Photoshop, Blender]``. Our new
ingestion rule keeps only the first entry (real engine) and drops the rest.
This migration applies the same rule to historical data and backfills the
richer fields we added in 0199 (description, logo_image_id, company links).

Idempotent: re-running produces no changes.
"""

import logging

from django.db import migrations

logger = logging.getLogger(__name__)


def forwards(apps, schema_editor):
    IGDBMatch = apps.get_model('trophies', 'IGDBMatch')
    GameEngine = apps.get_model('trophies', 'GameEngine')
    ConceptEngine = apps.get_model('trophies', 'ConceptEngine')
    Company = apps.get_model('trophies', 'Company')
    EngineCompany = apps.get_model('trophies', 'EngineCompany')

    # Build a cache of GameEngine rows by igdb_id for cheap lookups.
    engine_by_igdb_id = {e.igdb_id: e for e in GameEngine.objects.all()}
    company_by_igdb_id = {c.igdb_id: c for c in Company.objects.all()}

    # Track which EngineCompany links already exist so we don't re-create.
    existing_engine_companies = set(
        EngineCompany.objects.values_list('engine_id', 'company_id')
    )

    engines_backfilled = 0
    concepts_pruned = 0
    concepts_retained = 0
    concepts_no_link = 0
    engine_companies_created = 0

    matches = IGDBMatch.objects.filter(
        raw_response__isnull=False,
    ).select_related('concept').iterator(chunk_size=500)

    for match in matches:
        raw = match.raw_response
        if not raw or not isinstance(raw, dict):
            continue

        concept = match.concept
        if not concept:
            continue

        engines_list = raw.get('game_engines') or []
        if not engines_list or not isinstance(engines_list, list):
            # No engines in raw_response — just drop any existing links to
            # keep the one-engine invariant (there might be stale links from
            # earlier enrichments).
            deleted_count, _ = ConceptEngine.objects.filter(concept=concept).delete()
            if deleted_count:
                concepts_pruned += 1
            continue

        first = engines_list[0]
        if not isinstance(first, dict):
            continue

        first_igdb_id = first.get('id')
        if not first_igdb_id:
            continue

        # Backfill the GameEngine row's new fields (description, logo) from
        # raw_response when it's the first entry for a concept. We only
        # populate EMPTY fields so admin-curated values stay intact.
        engine = engine_by_igdb_id.get(first_igdb_id)
        if engine:
            updates = {}
            description = first.get('description') or ''
            logo_data = first.get('logo') or {}
            logo_image_id = logo_data.get('image_id') or '' if isinstance(logo_data, dict) else ''

            if description and not engine.description:
                updates['description'] = description
            if logo_image_id and not engine.logo_image_id:
                updates['logo_image_id'] = logo_image_id

            if updates:
                for k, v in updates.items():
                    setattr(engine, k, v)
                engine.save(update_fields=list(updates.keys()))
                engines_backfilled += 1

            # EngineCompany links from the first engine's companies array.
            company_ids = first.get('companies') or []
            if isinstance(company_ids, list):
                for cid in company_ids:
                    company = company_by_igdb_id.get(cid)
                    if not company:
                        continue
                    key = (engine.id, company.id)
                    if key in existing_engine_companies:
                        continue
                    EngineCompany.objects.create(engine=engine, company=company)
                    existing_engine_companies.add(key)
                    engine_companies_created += 1

        # Prune ConceptEngine links: keep only the one matching first_igdb_id.
        current_links = ConceptEngine.objects.filter(concept=concept).select_related('engine')
        desired_link_exists = False
        ids_to_delete = []
        for link in current_links:
            if link.engine and link.engine.igdb_id == first_igdb_id:
                desired_link_exists = True
            else:
                ids_to_delete.append(link.id)

        if ids_to_delete:
            ConceptEngine.objects.filter(id__in=ids_to_delete).delete()
            concepts_pruned += 1

        if desired_link_exists:
            concepts_retained += 1
        elif engine:
            # The desired link doesn't exist yet but we have the engine row —
            # create the link so this concept points at its true engine.
            ConceptEngine.objects.get_or_create(concept=concept, engine=engine)
            concepts_retained += 1
        else:
            concepts_no_link += 1

    logger.info(
        'Engine enrichment complete: '
        '%d engines backfilled, %d concepts pruned, %d concepts retained, '
        '%d concepts with no engine row yet, %d EngineCompany links created.',
        engines_backfilled, concepts_pruned, concepts_retained,
        concepts_no_link, engine_companies_created,
    )


class Migration(migrations.Migration):

    dependencies = [
        ('trophies', '0199_engine_description_logo_companies'),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
