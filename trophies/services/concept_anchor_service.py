"""Concept anchor service: gate primitives for the IGDB-anchored Concept model.

Provides the building blocks used by the `anchor_concepts` management command
(and the steady-state sync pipeline) to decide whether a Game can auto-join a
canonical-IGDB-id-anchored Concept, or whether the placement needs staff review.

Three primitives:

- `trophy_fingerprint(game)`: a short hash of a Game's trophy metrics. Used to
  detect divergence when comparing trophy lists that IGDB-resolve to the same
  canonical id (e.g., Death Stranding base vs Director's Cut — same family,
  different fingerprints → separate Concepts).

- `compare_trophy_metrics(game_a, game_b)`: detailed comparison of two Games'
  trophy metrics with named flag reasons when divergence is found. Used at
  concept-join time to decide whether the candidate Game's metrics match the
  target Concept's existing Games.

- `identity_cross_check(game, igdb_data, confidence)`: soft-signal checks on an
  IGDB match result itself (title similarity, platform overlap, release-date
  proximity). Returns sub-scores plus a list of flag reasons.

The downstream migration command composes these into a final anchor verdict;
this module deliberately does not encode the orchestration so the same
primitives can be reused by live sync and admin review tooling.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Optional

from trophies.services.igdb_service import IGDBService

logger = logging.getLogger(__name__)


# VR-host platform mapping. IGDB lists VR titles only on PSVR/PSVR2 platforms
# while PSN reports the host hardware (PS4/PS5). Identity cross-check must
# treat overlap symmetrically: an IGDB PSVR match implies PS4 overlap, and an
# IGDB PSVR2 match implies PS5 overlap.
IGDB_PSVR_PLATFORM_ID = 165
IGDB_PSVR2_PLATFORM_ID = 390

# Platform string -> IGDB platform id mapping (PSN-side strings to IGDB ids).
# Mirrors the source-of-truth mapping in `IGDBService.PLAT_TO_IGDB_ID` but kept
# local to avoid a circular dependency surface.
_PSN_PLATFORM_TO_IGDB = {
    'PS1': 7, 'PS2': 8, 'PS3': 9, 'PS4': 48, 'PS5': 167,
    'PSP': 38, 'PSVITA': 46,
    'PSVR': IGDB_PSVR_PLATFORM_ID, 'PSVR2': IGDB_PSVR2_PLATFORM_ID,
}

# Modern-first priority for picking the Game's "representative" platform when
# the title is on multiple. Used by the platform-aware release-date check so
# we compare against IGDB's date for the actual platform PSN is reporting,
# not IGDB's global earliest (which can be a 12-years-prior original release
# on a different platform — Red Dead Revolver PS2 vs. PS4 re-release).
_PLATFORM_PRIORITY = ('PS5', 'PS4', 'PS3', 'PSVITA', 'PSP', 'PS2', 'PS1')

# Reverse map: IGDB id -> its VR-host implication when present.
_VR_TO_HOST = {
    IGDB_PSVR_PLATFORM_ID: 48,    # PSVR implies PS4
    IGDB_PSVR2_PLATFORM_ID: 167,  # PSVR2 implies PS5
}


# Thresholds for the soft-signal checks. Tunable from a single place so we can
# adjust review-queue volume after seeing live data.
TITLE_SIMILARITY_FLAG_THRESHOLD = 0.50
MATCH_CONFIDENCE_FLAG_THRESHOLD = 0.60
RELEASE_DATE_PROXIMITY_FLAG_YEARS = 5


def trophy_fingerprint(game) -> str:
    """Short stable hash of a Game's trophy metrics.

    Captures the metrics that distinguish whether two trophy lists are the
    same Pursuer-facing game: per-tier counts and trophy group count.

    Two Games with identical trophy structure produce the same fingerprint —
    that's the signal for "these can safely share a Concept." Different
    fingerprints flag the join for review.

    Returns:
        str: 16-char hex digest. Stable across runs (no random salt).
    """
    counts = game.defined_trophies or {}
    parts = [
        int(counts.get('platinum') or 0),
        int(counts.get('gold') or 0),
        int(counts.get('silver') or 0),
        int(counts.get('bronze') or 0),
        int(game.trophy_groups.count() if game.pk else 0),
    ]
    payload = json.dumps(parts, separators=(',', ':')).encode('utf-8')
    return hashlib.sha1(payload).hexdigest()[:16]


def compare_trophy_metrics(game_a, game_b) -> dict:
    """Compare two Games' trophy metrics and report any divergence.

    Used at concept-join time: when a candidate Game wants to join an existing
    Concept anchored at the same canonical IGDB id, compare its metrics against
    one of the Concept's existing Games. Divergence flags the join for review.

    Returns:
        dict with keys:
            - fingerprint_a, fingerprint_b: the two fingerprints
            - matches: bool, True iff fingerprints are equal
            - flag_reasons: list of flag-reason strings (subset of
              ConceptJoinReview.FLAG_REASON_CHOICES) describing divergences;
              empty when matches=True.
            - diff: dict mapping metric name to (a_value, b_value) for any
              metric that differs.
    """
    counts_a = game_a.defined_trophies or {}
    counts_b = game_b.defined_trophies or {}
    groups_a = game_a.trophy_groups.count() if game_a.pk else 0
    groups_b = game_b.trophy_groups.count() if game_b.pk else 0

    diff = {}
    flag_reasons = []

    plat_a = int(counts_a.get('platinum') or 0)
    plat_b = int(counts_b.get('platinum') or 0)
    if plat_a != plat_b:
        diff['platinum'] = (plat_a, plat_b)
        flag_reasons.append('platinum_status_diverged')

    total_a = sum(int(counts_a.get(k) or 0) for k in ('platinum', 'gold', 'silver', 'bronze'))
    total_b = sum(int(counts_b.get(k) or 0) for k in ('platinum', 'gold', 'silver', 'bronze'))
    if total_a != total_b:
        diff['total'] = (total_a, total_b)
        if 'platinum_status_diverged' not in flag_reasons:
            flag_reasons.append('trophy_count_mismatch')

    if groups_a != groups_b:
        diff['trophy_groups'] = (groups_a, groups_b)
        flag_reasons.append('trophy_group_count_diff')

    fp_a = trophy_fingerprint(game_a)
    fp_b = trophy_fingerprint(game_b)
    return {
        'fingerprint_a': fp_a,
        'fingerprint_b': fp_b,
        'matches': fp_a == fp_b,
        'flag_reasons': flag_reasons,
        'diff': diff,
    }


def _extract_igdb_platform_ids(igdb_data: dict) -> set:
    """Pull the set of IGDB platform ids from an IGDB game payload, with VR host expansion."""
    result = set()
    for entry in igdb_data.get('platforms', []) or []:
        if isinstance(entry, int):
            pid = entry
        elif isinstance(entry, dict):
            pid = entry.get('id')
        else:
            pid = None
        if not pid:
            continue
        result.add(pid)
        host = _VR_TO_HOST.get(pid)
        if host:
            result.add(host)
    return result


def _psn_platforms_to_igdb(psn_platforms) -> set:
    return {
        _PSN_PLATFORM_TO_IGDB[p]
        for p in (psn_platforms or [])
        if p in _PSN_PLATFORM_TO_IGDB
    }


def _igdb_year_for_game_platforms(game, igdb_data) -> Optional[int]:
    """Pick the IGDB release year that best represents the Game's PSN platform.

    Walks the Game's `title_platform` list in modern-first priority order and
    returns the IGDB release year for the first platform that has both:
      * a PSN→IGDB mapping
      * a matching `release_dates` entry in the IGDB payload

    Returns None when there's no overlap — caller should treat that as "no
    date signal" rather than flagging. This prevents false-positive
    release_date_gap_excessive flags for re-releases on newer platforms
    where IGDB has multi-platform release_dates and PSN reports the newer.

    Multiple region/status entries for the same platform are collapsed to
    the earliest date for that platform (release_dates can carry alpha,
    beta, region-specific stages — we want the platform's "earliest known
    actual release").
    """
    from datetime import datetime, timezone as dt_tz

    game_platforms = set(game.title_platform or [])
    if not game_platforms:
        return None

    # Build IGDB platform_id -> earliest_date_ts map from release_dates.
    by_plat: dict = {}
    for entry in igdb_data.get('release_dates', []) or []:
        if not isinstance(entry, dict):
            continue
        plat = entry.get('platform')
        date = entry.get('date')
        if not plat or not date:
            continue
        if plat not in by_plat or date < by_plat[plat]:
            by_plat[plat] = date

    if not by_plat:
        return None

    for psn_p in _PLATFORM_PRIORITY:
        if psn_p not in game_platforms:
            continue
        igdb_plat_id = _PSN_PLATFORM_TO_IGDB.get(psn_p)
        if not igdb_plat_id:
            continue
        ts = by_plat.get(igdb_plat_id)
        if ts:
            try:
                return datetime.fromtimestamp(ts, tz=dt_tz.utc).year
            except (ValueError, OSError):
                pass
    return None


def identity_cross_check(
    game,
    igdb_data: dict,
    confidence: Optional[float] = None,
    trophy_group_title: Optional[str] = None,
) -> dict:
    """Soft-signal checks on a Game's proposed IGDB match.

    Independent of the IGDB match's own confidence score: validates that the
    Game's PSN-side identity is consistent with the IGDB game we'd be linking
    it to. Each check populates a sub-score; together they decide whether the
    proposal is clean or needs review.

    Args:
        game: the candidate Game.
        igdb_data: the IGDB game payload that was matched.
        confidence: the matcher's confidence score (0..1) if available.
        trophy_group_title: the title we matched against; falls back to
            extracting from `game` when not provided.

    Returns:
        dict with keys:
            - title_similarity: float 0..1
            - title_compared_against: the IGDB name (or alt/loc name)
              that produced the highest similarity
            - platform_overlap: bool
            - platform_overlap_count: int
            - release_year_gap: int or None (years between concept release
              and IGDB first release; None if either is unknown)
            - flag_reasons: list of strings from FLAG_REASON_CHOICES
    """
    flag_reasons = []
    trophy_title = trophy_group_title or IGDBService._extract_trophy_group_title(game)

    # Title similarity vs IGDB's best matching name (primary, alt, or
    # localization). Reuses the same fuzzy match the scorer uses so the
    # value is comparable across the codebase.
    igdb_name = igdb_data.get('name', '') or ''
    best_ratio = IGDBService._fuzzy_title_match(trophy_title or '', igdb_name) if trophy_title else 0.0
    best_name = igdb_name
    for alt in igdb_data.get('alternative_names', []) or []:
        alt_name = alt.get('name', '') if isinstance(alt, dict) else ''
        if not alt_name:
            continue
        ratio = IGDBService._fuzzy_title_match(trophy_title or '', alt_name)
        if ratio > best_ratio:
            best_ratio = ratio
            best_name = alt_name
    for loc in igdb_data.get('game_localizations', []) or []:
        loc_name = loc.get('name', '') if isinstance(loc, dict) else ''
        if not loc_name:
            continue
        ratio = IGDBService._fuzzy_title_match(trophy_title or '', loc_name)
        if ratio > best_ratio:
            best_ratio = ratio
            best_name = loc_name

    if best_ratio < TITLE_SIMILARITY_FLAG_THRESHOLD:
        flag_reasons.append('identity_title_dissimilar')

    # Platform overlap: compare game.title_platform against IGDB platforms
    # with VR-host expansion. A VR-only IGDB entry overlaps with the
    # PS4/PS5-only Game it implies.
    #
    # Two distinct failure modes, two distinct flags:
    #   * IGDB lists platforms but none overlap PSN's → the match is
    #     probably the wrong game (or a wrong-platform sibling).
    #   * IGDB lists NO platforms at all → we couldn't verify overlap.
    #     Common for shovelware / obscure titles whose IGDB pages are
    #     thin. The platform-blind match strategy (`name_no_platform`)
    #     surfaces these on name alone, so staff need to know the platform
    #     was never confirmed — distinct from a contradiction.
    game_plat_ids = _psn_platforms_to_igdb(game.title_platform or [])
    igdb_plat_ids = _extract_igdb_platform_ids(igdb_data)
    overlap = game_plat_ids & igdb_plat_ids
    if game_plat_ids and igdb_plat_ids and not overlap:
        flag_reasons.append('platform_overlap_insufficient')
    elif game_plat_ids and not igdb_plat_ids:
        flag_reasons.append('platform_overlap_unverified')

    # Release-year proximity. Concept-level signal because Game itself
    # doesn't carry a release date. The Concept may be wrong here (that's
    # why we're migrating) so this is a soft check, not a hard filter.
    #
    # Platform-aware: we look up IGDB's release date for the Game's most
    # modern PSN platform, not IGDB's global earliest. Without this, a
    # PS4 re-release of an old game (Red Dead Revolver: PS2 2004 / PS4
    # 2016) flags excessively against IGDB's 2004 PS2 date when PSN's
    # date is 2016.
    release_year_gap = None
    try:
        concept_year = (
            game.concept.release_date.year
            if game.concept_id and game.concept and game.concept.release_date
            else None
        )
    except AttributeError:
        concept_year = None
    igdb_year = _igdb_year_for_game_platforms(game, igdb_data)
    if concept_year and igdb_year:
        release_year_gap = abs(concept_year - igdb_year)
        if release_year_gap > RELEASE_DATE_PROXIMITY_FLAG_YEARS:
            flag_reasons.append('release_date_gap_excessive')

    # Confidence floor: separate signal because the matcher's confidence
    # already gates auto-accept upstream, but for migration we want a
    # stricter belt-and-suspenders threshold so weak matches still flag
    # for human review even when auto_accept_threshold would let them by.
    if confidence is not None and confidence < MATCH_CONFIDENCE_FLAG_THRESHOLD:
        flag_reasons.append('low_match_confidence')

    return {
        'title_similarity': round(best_ratio, 3),
        'title_compared_against': best_name,
        'platform_overlap': bool(overlap),
        'platform_overlap_count': len(overlap),
        'release_year_gap': release_year_gap,
        'flag_reasons': flag_reasons,
    }


def try_anchor_new_game(game):
    """Live-sync entry point: try to anchor a brand-new Game at its canonical Concept.

    Three outcomes:

      1. Clean canonical match — finds or creates the IGDB-anchored Concept
         at `concept_id = str(canonical_igdb_id)`, refreshes its IGDBMatch
         against canonical data (which captures media + Tier 1 fields via
         `process_match`), and assigns it to the Game. Returns the target.

      2. Match found but identity cross-check flagged it (low confidence,
         title mismatch, platform overlap missing, release-year drift, or
         a `concept_id_collision` where the bare-integer slot is already
         taken by an unrelated Concept) — writes a `ConceptJoinReview` so
         staff can resolve, then returns None for the caller to fall back
         to its existing PSN/stub placement.

      3. No IGDB match at all — returns None silently. No review created
         because there's no actionable IGDB candidate to record.

    Designed to be safe to call from live sync's placement paths: never
    raises, swallows internal errors and returns None on any failure so
    the caller's fallback always runs.

    Args:
        game: a Game instance that has no Concept FK assigned yet. (Already-
            placed Games short-circuit immediately to None — the bulk
            `anchor_concepts` command is the right tool for re-evaluating
            those, not live sync.)

    Returns:
        Concept on clean anchor; None on any non-clean-anchor outcome.
    """
    from django.db import transaction
    from django.utils import timezone

    from trophies.models import Concept
    from trophies.services.igdb_service import IGDBService

    if not game.pk or game.concept_id:
        return None

    try:
        match = IGDBService.match_game(game)
    except Exception:
        logger.exception(
            f'try_anchor_new_game: match_game failed for game pk={game.pk}'
        )
        return None

    if not match:
        return None

    cross = identity_cross_check(
        game, match['igdb_data'],
        confidence=match['confidence'],
        trophy_group_title=match['trophy_group_title'],
    )

    if cross['flag_reasons']:
        # Match found but identity check flagged it. Surface to staff via
        # the review queue, then fall back so caller can place the Game
        # the legacy way for now.
        _write_sync_review(game, match, cross, extra_flags=())
        return None

    canonical_id = match['canonical_igdb_id']
    concept_id_str = str(canonical_id)

    try:
        with transaction.atomic():
            target = Concept.objects.filter(concept_id=concept_id_str).first()
            canonical_data = None

            if target:
                # Existing Concept owns this PK. Verify it really anchors at
                # the same canonical id; if not it's a collision and we bail
                # to the review queue + caller fallback.
                existing_match = getattr(target, 'igdb_match', None)
                if existing_match and existing_match.igdb_id:
                    existing_canonical = IGDBService._resolve_canonical_igdb_id(
                        existing_match.raw_response or {}, existing_match.igdb_id
                    )
                    if existing_canonical != canonical_id:
                        _write_sync_review(
                            game, match, cross,
                            extra_flags=('concept_id_collision',),
                        )
                        return None
            else:
                canonical_data = IGDBService.fetch_full_game_data(canonical_id)
                if not canonical_data:
                    return None
                target = Concept.objects.create(
                    concept_id=concept_id_str,
                    unified_title=canonical_data.get('name', ''),
                )

            # Refresh target's IGDBMatch against canonical (captures media
            # + Tier 1). Idempotent — process_match update_or_creates.
            # Reuse fetch from above when present.
            if canonical_data is None:
                canonical_data = IGDBService.fetch_full_game_data(canonical_id)
            if canonical_data:
                IGDBService.process_match(
                    target, canonical_data, confidence=1.0, method='manual',
                )

            target.anchor_migration_completed_at = timezone.now()
            target.save(update_fields=['anchor_migration_completed_at'])

            game.add_concept(target)
            return target
    except Exception:
        logger.exception(
            f'try_anchor_new_game: anchoring failed for game pk={game.pk}'
        )
        return None


def _write_sync_review(game, match, cross, extra_flags=()):
    """Write a ConceptJoinReview entry from live sync's match-but-flagged path.

    Preserves staff-resolved reviews (never overwrites a non-pending status).
    Internal helper used only by `try_anchor_new_game`.
    """
    from trophies.models import ConceptJoinReview

    flag_reasons = list(cross.get('flag_reasons', []))
    for fr in extra_flags:
        if fr not in flag_reasons:
            flag_reasons.append(fr)
    flag_reasons = [
        fr for fr in flag_reasons
        if fr in ConceptJoinReview.FLAG_REASON_CHOICES
    ]

    # Don't clobber a staff-resolved review (approved/rejected/deferred).
    existing = ConceptJoinReview.objects.filter(game=game).first()
    if existing and existing.status != 'pending':
        return

    identity_data = {k: v for k, v in cross.items() if k != 'flag_reasons'}
    identity_data.update({
        'match_confidence': match['confidence'],
        'match_method': match['match_method'],
        'raw_igdb_id': match['raw_igdb_id'],
        'trophy_group_title': match['trophy_group_title'],
        'source': 'live_sync',
    })

    try:
        ConceptJoinReview.objects.update_or_create(
            game=game,
            defaults={
                'proposed_canonical_igdb_id': match['canonical_igdb_id'] or 0,
                'proposed_raw_igdb_id': match['raw_igdb_id'],
                'flag_reasons': flag_reasons,
                'trophy_fingerprint': trophy_fingerprint(game),
                'identity_check_data': identity_data,
                'status': 'pending',
            },
        )
    except Exception:
        logger.exception(
            f'_write_sync_review: failed to write review for game pk={game.pk}'
        )


def anchor_concept_to_canonical(source_concept, canonical_igdb_id, *, user=None) -> dict:
    """Manually anchor a deferred source Concept's Games to a staff-provided
    canonical IGDB id.

    Bypasses `match_game` (the matcher couldn't find a hit on its own — that's
    why this Concept is deferred). Everything downstream of matching runs as
    normal: canonical resolution, target Concept find-or-create, IGDBMatch
    refresh (captures media), trophy-fingerprint vetting per Game,
    identity cross-check per Game. Clean Games move via `Game.add_concept`
    (force=True bypasses concept_lock); flagged Games get a
    `ConceptJoinReview` entry the same way the migration does.

    Args:
        source_concept: the deferred Concept whose Games should be anchored.
        canonical_igdb_id: the IGDB id staff believes is correct. Will be run
            through `_resolve_canonical_igdb_id` in case staff provided a
            Director's Cut / Standalone Expansion id that should collapse to
            its parent.
        user: the staff user driving the action (for audit on any reviews).

    Returns:
        dict with keys:
            * ok (bool): True if the work succeeded structurally
            * error (str | None): error message if ok=False
            * target_concept (Concept | None): the anchored target
            * resolved_canonical_id (int | None): the actual canonical id used
              (may differ from input if collapsed via parent_game)
            * moved_count (int)
            * flagged_count (int)
            * flagged_games (list[(game, flag_reasons)]): for summary messaging
            * collision (bool): True if existing Concept at str(canonical) had
              a different anchor; flagged everything for review and bailed
    """
    from django.db import IntegrityError, transaction
    from django.utils import timezone

    from trophies.models import Concept, ConceptJoinReview
    from trophies.services.igdb_service import IGDBService

    result = {
        'ok': False,
        'error': None,
        'target_concept': None,
        'resolved_canonical_id': None,
        'moved_count': 0,
        'flagged_count': 0,
        'flagged_games': [],
        'collision': False,
    }

    if not source_concept or not source_concept.pk:
        result['error'] = 'Source Concept missing or unsaved'
        return result
    try:
        provided_igdb_id = int(canonical_igdb_id)
    except (TypeError, ValueError):
        result['error'] = f'Invalid IGDB id: {canonical_igdb_id!r}'
        return result

    # Fetch RAW IGDB data for the version staff provided. Each Concept in a
    # family represents one specific IGDB version, so this raw data (cover,
    # summary, companies, screenshots, etc.) is what we'll write to the
    # target Concept's IGDBMatch.
    raw_data = IGDBService.fetch_full_game_data(provided_igdb_id)
    if not raw_data:
        result['error'] = f'IGDB returned no data for id {provided_igdb_id}'
        return result

    # Resolve canonical for the FAMILY link (canonical resolution collapses
    # Director's Cut / Standalone Expansion / Expanded Game / Port / Remake
    # ids into their parent for family grouping). The target Concept itself
    # still represents the SPECIFIC version (raw id), not canonical.
    resolved_canonical = IGDBService._resolve_canonical_igdb_id(
        raw_data, provided_igdb_id
    )
    result['resolved_canonical_id'] = resolved_canonical

    with transaction.atomic():
        # Stamp last_attempt_at on source so the admin's "Attempted but not
        # anchored" filter reflects this manual run.
        Concept.objects.filter(pk=source_concept.pk).update(
            anchor_migration_last_attempt_at=timezone.now()
        )

        # Find existing per-version Concept in the family (its IGDBMatch
        # already at provided_igdb_id), or determine which slot to allocate.
        raw_map = build_family_raw_igdb_map(
            resolved_canonical, exclude_concept_pk=source_concept.pk,
        )
        target = raw_map.get(provided_igdb_id)

        if target is None:
            # No Concept anchors this raw version yet. Default slot is
            # str(provided_igdb_id) — concept_id directly maps to the IGDB
            # version this Concept represents. Duplicates (same raw, different
            # trophy lists — rare, e.g., Our World Is Ended) get
            # str(provided_igdb_id)-N via allocate_sibling_concept_id.
            target_concept_id = str(provided_igdb_id)
            existing = Concept.objects.filter(concept_id=target_concept_id).first()
            if existing:
                existing_match = getattr(existing, 'igdb_match', None)
                if existing_match and existing_match.igdb_id:
                    existing_canonical = IGDBService._resolve_canonical_igdb_id(
                        existing_match.raw_response or {}, existing_match.igdb_id
                    )
                    if existing_canonical != resolved_canonical:
                        result['collision'] = True
                        result['error'] = (
                            f'Concept_id {target_concept_id!r} already exists and '
                            f'resolves to canonical {existing_canonical}, not '
                            f'{resolved_canonical}. Resolve manually.'
                        )
                        return result
                    # Same family. If existing's match is for a DIFFERENT raw,
                    # don't trample — allocate a same-raw-suffix slot for our
                    # new Concept (the "Our World Is Ended" pattern).
                    if existing_match.igdb_id != provided_igdb_id:
                        target_concept_id = allocate_sibling_concept_id(provided_igdb_id)
                        existing = None
                    else:
                        target = existing  # Same raw — reuse
                else:
                    # Existing slot has no IGDBMatch — safe to reuse.
                    target = existing

            if target is None:
                try:
                    with transaction.atomic():
                        target = Concept.objects.create(
                            concept_id=target_concept_id,
                            unified_title=raw_data.get('name', ''),
                        )
                except IntegrityError:
                    target = Concept.objects.filter(
                        concept_id=target_concept_id
                    ).first()
                    if target is None:
                        result['error'] = (
                            f'Failed to create or fetch Concept at concept_id='
                            f'{target_concept_id!r} after race.'
                        )
                        return result

        result['target_concept'] = target

        # Refresh target's IGDBMatch with the RAW (version-specific) data so
        # this Concept's cover / summary / companies / etc. reflect THIS
        # version. process_match's auto_accepted path also rebuilds the
        # ConceptCompany / ConceptGenre / etc. through-rows and links Concept
        # to GameFamily via canonical resolution.
        IGDBService.process_match(
            target, raw_data, confidence=1.0, method='manual',
        )

        # Vetting per Game (cross_check + fingerprint vs target's reference).
        games = list(source_concept.games.all())
        existing_target_games = list(
            target.games.exclude(pk__in=[g.pk for g in games])
        ) if target.pk else []
        reference_game = existing_target_games[0] if existing_target_games else None

        moved_count = 0
        flagged_count = 0
        anchored_a_game = False
        for game in games:
            trophy_title = IGDBService._extract_trophy_group_title(game)
            cross = identity_cross_check(
                game, raw_data,
                confidence=None,  # no matcher confidence on manual anchor
                trophy_group_title=trophy_title,
            )
            cross_flags = list(cross['flag_reasons'])

            fingerprint_flags = []
            if reference_game:
                metric = compare_trophy_metrics(game, reference_game)
                fingerprint_flags = metric['flag_reasons']

            flag_reasons = cross_flags + [
                fr for fr in fingerprint_flags if fr not in cross_flags
            ]
            game_fp = trophy_fingerprint(game)

            if flag_reasons:
                existing_review = ConceptJoinReview.objects.filter(game=game).first()
                if existing_review and existing_review.status != 'pending':
                    pass  # Preserve resolved reviews; don't clobber.
                else:
                    identity_data = {k: v for k, v in cross.items() if k != 'flag_reasons'}
                    identity_data['trophy_group_title'] = trophy_title
                    identity_data['source'] = 'manual_anchor'
                    ConceptJoinReview.objects.update_or_create(
                        game=game,
                        defaults={
                            'proposed_canonical_igdb_id': resolved_canonical,
                            # In manual anchor, staff-provided id IS the raw.
                            'proposed_raw_igdb_id': provided_igdb_id,
                            'proposed_concept': target,
                            'flag_reasons': [
                                fr for fr in flag_reasons
                                if fr in ConceptJoinReview.FLAG_REASON_CHOICES
                            ],
                            'trophy_fingerprint': game_fp,
                            'identity_check_data': identity_data,
                            'status': 'pending',
                        },
                    )
                flagged_count += 1
                result['flagged_games'].append((game, flag_reasons))
            else:
                game.add_concept(target, force=True)
                moved_count += 1
                anchored_a_game = True
                if reference_game is None:
                    reference_game = game

        if anchored_a_game:
            target.anchor_migration_completed_at = timezone.now()
            target.save(update_fields=['anchor_migration_completed_at'])

        # Defensive: Game.add_concept's absorb-cascade SHOULD have deleted
        # the source when its last Game moved out, but a real-world report
        # showed source surviving with 0 games. Belt-and-suspenders cleanup.
        try:
            refreshed_source = Concept.objects.get(pk=source_concept.pk)
        except Concept.DoesNotExist:
            refreshed_source = None
        if refreshed_source and refreshed_source.games.count() == 0:
            logger.info(
                f'anchor_concept_to_canonical: source Concept '
                f'{refreshed_source.concept_id!r} (pk={refreshed_source.pk}) '
                f'survived add_concept cascade with 0 games — running '
                f'defensive absorb + delete.'
            )
            target.absorb(refreshed_source)
            refreshed_source.delete()

        result['moved_count'] = moved_count
        result['flagged_count'] = flagged_count
        result['ok'] = True
        return result


def build_family_raw_igdb_map(canonical_id, exclude_concept_pk=None) -> dict:
    """Map `raw_igdb_id (IGDBMatch.igdb_id) → Concept` for the canonical family.

    Each Concept in the canonical IGDB family represents ONE specific IGDB
    version (PS3 original = id 1009, Remastered = id 5328, Part I = id 220104,
    all in family for canonical 1009). The Concept's `IGDBMatch.igdb_id`
    holds the raw IGDB id of the version it represents. This map is the
    primary routing key for migrating Games into the right per-version
    Concept.

    Args:
        canonical_id: the canonical IGDB id of the family.
        exclude_concept_pk: optional Concept pk to skip when building the map.

    Returns:
        dict[int, Concept]: maps each raw IGDB id present in the family to
            the Concept that anchors that version. First-seen-by-pk-order
            wins on conflicts (legacy data may have multiple Concepts with
            the same IGDBMatch.igdb_id from before the per-version split).
    """
    from trophies.models import Concept
    from django.db.models import Q

    base = str(canonical_id)
    family_concepts = Concept.objects.filter(
        Q(concept_id=base) | Q(concept_id__regex=rf'^{re.escape(base)}-\d+$')
    )
    if exclude_concept_pk is not None:
        family_concepts = family_concepts.exclude(pk=exclude_concept_pk)
    family_concepts = family_concepts.order_by('pk').select_related('igdb_match')

    raw_map = {}
    for concept in family_concepts:
        match = getattr(concept, 'igdb_match', None)
        if match and match.igdb_id and match.igdb_id not in raw_map:
            raw_map[match.igdb_id] = concept
    return raw_map


def build_family_fingerprint_map(canonical_id, exclude_concept_pk=None) -> dict:
    """Map `trophy_fingerprint(game) → Concept` for all Games in the canonical
    IGDB family (primary `str(canonical_id)` + siblings `str(canonical_id)-N`).

    Used by the migration and manual-anchor paths to route incoming Games to
    the right sibling within the family instead of always landing them at the
    primary and creating redundant siblings during review approval.

    Example: a Family with primary 'X' (PS4, fingerprint A) and sibling 'X-2'
    (Vita-A, fingerprint B). An incoming Vita-B game with fingerprint B gets
    routed to 'X-2' (joining its trophy-equivalent sibling) instead of
    landing at 'X' and getting flagged for a third sibling.

    Args:
        canonical_id: the canonical IGDB id of the family.
        exclude_concept_pk: optional Concept pk to skip when building the map.
            Used by manual-anchor when the source Concept itself is already in
            the family (edge case where staff manually anchors an already-
            anchored sibling to a different canonical) — without exclusion,
            the source's Games would map to themselves and the routing would
            silently no-op.

    Returns:
        dict[str, Concept]: first-seen-by-pk-order Concept wins per fingerprint.
            Empty when the family has no Concepts or no Games yet.
    """
    from trophies.models import Concept
    from django.db.models import Q

    base = str(canonical_id)
    family_concepts = Concept.objects.filter(
        Q(concept_id=base) | Q(concept_id__regex=rf'^{re.escape(base)}-\d+$')
    )
    if exclude_concept_pk is not None:
        family_concepts = family_concepts.exclude(pk=exclude_concept_pk)
    # Deterministic order so first-seen-wins per fingerprint is stable across
    # runs. Primary (concept_id=base) generally has lower pk than siblings
    # created later, so this also tends to prefer primary over sibling when
    # both happen to share a Game-fingerprint.
    family_concepts = family_concepts.order_by('pk').prefetch_related('games')

    fp_map = {}
    for concept in family_concepts:
        for game in concept.games.all():
            fp = trophy_fingerprint(game)
            if fp not in fp_map:
                fp_map[fp] = concept
    return fp_map


def allocate_sibling_concept_id(canonical_id) -> str:
    """Return the next free `"{canonical_id}-N"` slot for a sibling Concept.

    Sibling Concepts share a canonical IGDB id (and therefore a GameFamily)
    but have materially different trophy contents — the case where two
    trophy lists exist for what IGDB considers one game, and we've decided
    to keep them in separate Concepts under the same Family.

    Allocation rule:
      * The primary Concept owns the bare-integer slot `"{canonical_id}"`.
      * Siblings get `"{canonical_id}-2"`, `"{canonical_id}-3"`, etc.
      * The lowest unused suffix (starting at 2) is returned.

    Race window: between this query and the caller's `Concept.objects.create`,
    another process could grab the same slot. The caller should wrap creation
    in a retry loop that re-allocates on `IntegrityError`.

    Args:
        canonical_id: the canonical IGDB id this Family is anchored on.

    Returns:
        str: a concept_id string free at the time of this call.
    """
    from trophies.models import Concept

    base = str(canonical_id)
    pattern = rf'^{re.escape(base)}-\d+$'
    used = set()
    for cid in Concept.objects.filter(
        concept_id__regex=pattern
    ).values_list('concept_id', flat=True):
        try:
            n = int(cid.rsplit('-', 1)[-1])
            used.add(n)
        except (ValueError, IndexError):
            pass
    n = 2
    while n in used:
        n += 1
    return f'{base}-{n}'
