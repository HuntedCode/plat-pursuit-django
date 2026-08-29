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


# ─── Game-level observations (trophy_titles / title_stats) ────────────────────────────────────────
#
# Same contract as concept capture above: behind the same kill switch, never raises, savepointed.
# But APPEND-ON-CHANGE rather than latest-value -- see the PSNTitleObservation docstring for why a
# latest-value sidecar cannot answer the question this table exists for.

import hashlib
import json
from datetime import timedelta

from django.utils import timezone

from trophies.models import Game, PSNTitleObservation


def _observation_content(source, fields):
    """The canonical stored tuple and its hash. The hash covers EXACTLY what is stored, so two
    payloads that differ only in per-user data (progress, earned counts) hash identically and
    collapse into one row -- which is what keeps this table at ~one row per game, not one per sync."""
    # The hash covers source + fields; the returned dict is fields ONLY, so callers can pass
    # `source=` explicitly without a duplicate-kwarg TypeError in the bulk constructor -- which the
    # never-raises guard would swallow into a silent "0 captured". Found by test, kept as a comment.
    canonical = json.dumps({'source': source, **fields}, sort_keys=True, ensure_ascii=False, default=str)
    return fields, hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def _title_fields(trophy_title):
    """Title-level fields ONLY. progress / earned_trophies / hidden_flag / last_updated_datetime are
    per-user and must never land in a game-level table."""
    defined = trophy_title.defined_trophies
    return {
        'title_name_raw': trophy_title.title_name or '',
        'title_detail': trophy_title.title_detail or '',
        # Query string stripped before hashing: trophy icons are path-addressed today, but a CDN
        # cache-buster would otherwise mint a new row per change of token, for every game at once.
        'title_icon_url': (trophy_title.title_icon_url or '').split('?')[0],
        'np_service_name': trophy_title.np_service_name or '',
        'trophy_set_version': trophy_title.trophy_set_version or '',
        'title_platform': sorted(p.value for p in (trophy_title.title_platform or [])),
        'has_trophy_groups': trophy_title.has_trophy_groups,
        'defined_trophies': {
            'bronze': defined.bronze, 'silver': defined.silver,
            'gold': defined.gold, 'platinum': defined.platinum,
        } if defined else {},
        # np_title_id is DELIBERATELY absent: psnawp's trophy_titles paginator hardcodes it to
        # None (only trophy_titles_for_title populates it, and those payloads never reach this
        # capture), so including it stored '' on 100% of rows -- the exact wrong-key signature
        # audit_psn_capture exists to flag. title_stats rows carry the real one.
    }


def _stats_fields(title_stats):
    """title_stats' independent view of the same title. name/image/category are title-level;
    play_count / durations / first+last played are per-user and excluded."""
    return {
        'title_name_raw': title_stats.name or '',
        'title_icon_url': (title_stats.image_url or '').split('?')[0],
        'np_title_id': title_stats.title_id or '',
        'stats_category': title_stats.category.value if title_stats.category else '',
    }


def _record_observation(np_communication_id, game, source, fields):
    if not settings.PSN_METADATA_CAPTURE_ENABLED:
        return None
    if not np_communication_id:
        return None
    content, content_hash = _observation_content(source, fields)
    try:
        with transaction.atomic():
            row, _created = PSNTitleObservation.objects.update_or_create(
                np_communication_id=np_communication_id,
                content_hash=content_hash,
                defaults={'game': game, 'source': source, **content},
            )
        return row
    except Exception:
        logger.warning(
            'PSN title observation failed for %s (%s)', np_communication_id, source, exc_info=True,
        )
        return None


def capture_title_stats_observation(game, title_stats):
    """Record title_stats' independent name/art/category for this title. Previously discarded on
    arrival at `update_profile_game_with_title_stats`."""
    if game is None or title_stats is None:
        return None
    try:
        fields = _stats_fields(title_stats)
    except Exception:
        logger.warning('PSN title observation failed for %s (title_stats payload)',
                       game.np_communication_id, exc_info=True)
        return None
    return _record_observation(game.np_communication_id, game, 'title_stats', fields)


def capture_title_page_bulk(trophy_titles):
    """Fast-path capture: page 1 of trophy_titles is fetched on EVERY sync (it builds the
    fingerprint) and was discarded when the fingerprint matched. That page is the only channel that
    can see a pure rename -- a rename changes neither trophy counts nor game count, so it never
    breaks the fingerprint and never triggers the slow-path walk that the per-game capture rides on.

    Bounded work regardless of page size (~4 queries for 400 titles): one Game lookup, one
    existing-hash lookup, one bulk insert, one last_seen bump. NEVER RAISES -- this runs inline in
    the sync orchestrator ahead of the fast-path return.
    """
    if not settings.PSN_METADATA_CAPTURE_ENABLED:
        return 0
    if not trophy_titles:
        return 0
    try:
        wanted = {}
        for tt in trophy_titles:
            # Per-title guard: one malformed entry must cost ONLY itself. A single try around the
            # whole loop silently lost the entire 400-title page to one bad payload, reported as
            # "0 captured" -- while the single-row path promised the opposite.
            try:
                np_id = (tt.np_communication_id or '').strip()
                if not np_id:
                    continue
                content, content_hash = _observation_content('trophy_titles', _title_fields(tt))
                wanted[(np_id, content_hash)] = content
            except Exception:
                logger.warning('PSN title observation skipped one malformed page entry', exc_info=True)
                continue
        if not wanted:
            return 0

        np_ids = {k[0] for k in wanted}
        # Two integers per game, not 400 full rows: Game has 35 columns including TEXT and JSON,
        # and this runs on every fast-path sync (the CLAUDE.md per-user-queryset rule, applied to
        # a catalogue queryset that is just as hot).
        games = dict(
            Game.objects.filter(np_communication_id__in=np_ids)
            .values_list('np_communication_id', 'id')
        )

        # source-filtered: title_stats rows share the np_communication_id but can never match a
        # trophy_titles hash, so without the filter ~40% of the fetched rows were pure waste.
        existing = {}
        for pk, np_id, chash, seen in PSNTitleObservation.objects.filter(
            np_communication_id__in=np_ids, source='trophy_titles',
        ).values_list('pk', 'np_communication_id', 'content_hash', 'last_seen_at'):
            existing[(np_id, chash)] = (pk, seen)

        # Damped bump: last_seen_at is provenance (nothing reads it below day granularity), and an
        # undamped bump UPDATEd up to 400 rows on EVERY fast-path sync -- 300-500k dead tuples/day
        # against a ~100k-row table, turning the previously write-free fast path into the table's
        # heaviest writer. With the cutoff the UPDATE is empty on almost every sync.
        stale_cutoff = timezone.now() - timedelta(hours=24)
        to_bump = [
            pk for k in wanted if k in existing
            for pk, seen in [existing[k]] if seen < stale_cutoff
        ]
        to_insert = [
            PSNTitleObservation(
                np_communication_id=np_id, content_hash=chash,
                game_id=games.get(np_id), source='trophy_titles', **content,
            )
            for (np_id, chash), content in wanted.items()
            if (np_id, chash) not in existing and np_id in games
        ]
        # The savepoint carries the same do-not-delete rationale as _record_observation's: today
        # the orchestrator runs in autocommit, but the day it gets wrapped in atomic() a swallowed
        # failure here would abort the CALLER's transaction and kill its next query far from here.
        with transaction.atomic():
            if to_insert:
                # ignore_conflicts: two workers fast-pathing overlapping libraries race on the
                # unique constraint; the loser's row already exists, which is the outcome we wanted.
                PSNTitleObservation.objects.bulk_create(to_insert, ignore_conflicts=True)
            if to_bump:
                PSNTitleObservation.objects.filter(pk__in=to_bump).update(last_seen_at=timezone.now())
        return len(to_insert)
    except Exception:
        logger.warning('PSN title page bulk capture failed', exc_info=True)
        return 0
