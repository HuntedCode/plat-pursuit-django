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
    game_plat_ids = _psn_platforms_to_igdb(game.title_platform or [])
    igdb_plat_ids = _extract_igdb_platform_ids(igdb_data)
    overlap = game_plat_ids & igdb_plat_ids
    if game_plat_ids and igdb_plat_ids and not overlap:
        flag_reasons.append('platform_overlap_insufficient')

    # Release-year proximity. Concept-level signal because Game itself
    # doesn't carry a release date. The Concept may be wrong here (that's
    # why we're migrating) so this is a soft check, not a hard filter.
    release_year_gap = None
    try:
        concept_year = (
            game.concept.release_date.year
            if game.concept_id and game.concept and game.concept.release_date
            else None
        )
    except AttributeError:
        concept_year = None
    igdb_ts = igdb_data.get('first_release_date')
    igdb_year = None
    if isinstance(igdb_ts, (int, float)) and igdb_ts:
        try:
            from datetime import datetime, timezone as dt_tz
            igdb_year = datetime.fromtimestamp(igdb_ts, tz=dt_tz.utc).year
        except (ValueError, OSError):
            igdb_year = None
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
