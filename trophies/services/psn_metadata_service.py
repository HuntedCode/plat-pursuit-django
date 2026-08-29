"""Capture PSN's own concept metadata alongside whatever we derive from it.

ONE ENTRY POINT, called from every place that holds a PSN `get_details` response:

  1. `PsnApiService.create_concept_from_details`   -- a Game's first PSN concept
  2. `PsnApiService.update_concept_english_fields` -- the English refresh path
  3. `token_keeper._job_sync_title_id`, first-anchor branch  -- payload was discarded here
  4. `token_keeper._job_sync_title_id`, already-anchored branch -- and here

Sites 3 and 4 are the ones that matter, and they are two arms of the same `if` in
`_job_sync_title_id` (NOT `_do_sync_trophies`, which fetches trophies and never sees a details
response -- earlier drafts of this file named the wrong function, and that mislabelling is how site 4
got missed the first time). Both arms hold the full details object; arm 3 rescued `bg_url` and arm 4
rescued nothing. Name, publisher, genres, subgenres, descriptions, content rating and media were all
in memory and thrown away because the Game had landed on an IGDB-anchored Concept.

SCOPE, so nobody expects more than this delivers: `_job_sync_title_id` runs only for title_ids that
did NOT match a known game (`_walk_title_stats`), so this captures going forward for new and
unresolved titles. It does NOT backfill concepts already anchored and matched -- that needs a
deliberate walk with its own PSN API budget.

ADDITIVE. This never writes a Concept column, which is what makes the change safe to deploy without
touching a single page: nothing that renders reads these tables yet.
"""
import logging

from django.conf import settings
from django.db import transaction

from trophies.models import PSNConceptData, PSNRawPayload

logger = logging.getLogger(__name__)

#: Keys the parsed sidecar lifts out of the response. Everything else survives in PSNRawPayload.
_DESCRIPTION_TYPES = {'SHORT': 'short', 'LONG': 'long'}


def _media(details):
    """Keep BOTH media blocks, unflattened, under names that say what they are.

    PSN sends media in two places and `token_keeper._extract_media` reads both, checking
    `defaultProduct.media` FIRST and treating each as a dict of `{'images': [...], 'videos': [...]}`.
    Two traps this avoids:

      1. `details['media']` is a DICT, not a list. Storing `details.get('media') or []` puts a dict in
         a column whose `or []` fallback claims it is a list, so the column's type varies by row.
      2. `all_media` is already a name with a meaning in this codebase -- the FLATTENED, deduped,
         sorted list that `_extract_media` returns and `Concept.media` stores. Reusing it for a raw
         nested dict would hand a future reader a shape mismatch under a familiar name.

    Deliberately NOT flattened through `_extract_media`: that helper drops everything except `type`
    and `url`, and preserving what PSN sent is the entire point of this table.
    """
    return {
        'root': details.get('media') or {},
        'default_product': (details.get('defaultProduct') or {}).get('media') or {},
    }


def _descriptions(details):
    """PSN sends descriptions as a typed list; store them keyed the way Concept already does."""
    out = {}
    for entry in details.get('descriptions') or []:
        try:
            key = _DESCRIPTION_TYPES.get(entry.get('type'))
            if key:
                out[key] = entry.get('desc', '')
        except AttributeError:
            continue          # a malformed entry must not cost the rest of the payload
    return out


def capture_psn_concept_data(concept, details, *, country='', language=''):
    """Record PSN's payload for `concept`. Returns the PSNConceptData row, or None if there was
    nothing to record.

    `country`/`language` are the storefront that ANSWERED, not what we asked for, and they are part
    of the row's identity: PSN returns a different name and rating per region for the same concept
    id, so a US response and a JP response are two rows, not one row written twice. Callers get this
    from `_get_details_with_region_fallback`, which knows which attempt succeeded.

    NEVER RAISES. This is called from inside the sync job path, and it is purely additive capture --
    a failure here must not cost a hunter their sync. It logs at WARNING rather than swallowing
    quietly, because this codebase has spent real time on failures that reported success.

    The import above is deliberately at MODULE level, not inside the guard: a missing module is a
    deploy error and must be loud, which is the distinction `_job_sync_complete` learned the hard way
    when a deleted service's import sat inside a broad `except`.
    """
    if not settings.PSN_METADATA_CAPTURE_ENABLED:
        return None

    if concept is None or not details:
        return None

    psn_id = details.get('id')
    if not psn_id:
        # No PSN identity in the response: nothing to key a row on. Not an error -- the region
        # fallback can return a sparse payload.
        return None

    try:
        # The atomic() is a SAVEPOINT, not belt-and-braces. Today no caller wraps us, so on failure
        # update_or_create's own atomic rolls back cleanly and the swallow below is honest. The day
        # someone wraps the sync job for batching, a swallowed error inside THEIR transaction would
        # leave it aborted and kill the caller's next query with TransactionManagementError -- a sync
        # failing for a reason that looks nothing like this file. This keeps the swallow honest under
        # both call graphs, so do not delete it as redundant.
        with transaction.atomic():
            row, _created = PSNConceptData.objects.update_or_create(
                psn_concept_id=str(psn_id),
                country=country or '',
                defaults={
                    'concept': concept,
                    'name': details.get('name') or '',
                    'name_en': details.get('nameEn') or '',
                    'publisher_name': details.get('publisherName') or '',
                    'genres': details.get('genres') or [],
                    'subgenres': details.get('subGenres') or [],
                    'descriptions': _descriptions(details),
                    'content_rating': details.get('contentRating') or {},
                    'media': _media(details),
                    'language': language or '',
                },
            )
            PSNRawPayload.objects.update_or_create(psn_data=row, defaults={'payload': details})
        return row
    except Exception:
        logger.warning(
            'PSN metadata capture failed for concept %s (psn id %s)',
            getattr(concept, 'concept_id', None), psn_id, exc_info=True,
        )
        return None
