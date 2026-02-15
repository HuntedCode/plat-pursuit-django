import logging
import re
from collections import defaultdict

from django.db.models import Count

from trophies.models import Concept, GameFamily, GameFamilyProposal, Trophy

logger = logging.getLogger("psn_api")

# Common suffixes stripped during normalization
REMASTER_SUFFIXES = [
    'remastered', 'hd', 'definitive edition', 'game of the year edition',
    'goty edition', "director's cut", 'directors cut', 'complete edition',
    'enhanced edition', 'collection', 'bundle', 'remake',
    'special edition', 'ultimate edition', 'deluxe edition',
    'anniversary edition', 'legendary edition',
]

# Pre-compiled regexes for title normalization
_TM_RE = re.compile(r'[™®]|(\bTM\b)|(\(R\))')
_SUFFIX_PATTERNS = [
    re.compile(rf'\s*[-:–]?\s*{re.escape(suffix)}\s*$', flags=re.IGNORECASE)
    for suffix in REMASTER_SUFFIXES
]
_PLATFORM_SUFFIX_RE = re.compile(
    r'\s*\([^)]*(?:PS\d|PS\s?Vita|PSVITA|PS\s?VR|Unknown)[^)]*\)\s*$',
    flags=re.IGNORECASE,
)


def normalize_game_title(title):
    """Strip common remaster/edition suffixes and normalize for comparison."""
    title = title.lower().strip()
    title = _TM_RE.sub('', title).strip()
    for pattern in _SUFFIX_PATTERNS:
        title = pattern.sub('', title)
    title = _PLATFORM_SUFFIX_RE.sub('', title)
    return title.strip()


def get_trophy_fingerprint(concept):
    """Structural fingerprint: trophy count by type + group count. Language-agnostic."""
    trophies = Trophy.objects.filter(game__concept=concept)
    if not trophies.exists():
        return None
    type_counts = {}
    for entry in trophies.values('trophy_type').annotate(count=Count('id')):
        type_counts[entry['trophy_type']] = entry['count']
    group_count = trophies.values('trophy_group_id').distinct().count()
    return {
        'types': type_counts,
        'groups': group_count,
        'total': sum(type_counts.values()),
    }


def calculate_trophy_name_overlap(concept_a, concept_b):
    """Compare trophy names between two concepts. Returns 0.0-1.0 or None if no data."""
    names_a = set(
        Trophy.objects.filter(game__concept=concept_a)
        .values_list('trophy_name', flat=True)
    )
    names_b = set(
        Trophy.objects.filter(game__concept=concept_b)
        .values_list('trophy_name', flat=True)
    )
    if not names_a or not names_b:
        return None
    intersection = names_a & names_b
    return len(intersection) / min(len(names_a), len(names_b))


def fingerprints_match(fp_a, fp_b):
    """Check if two structural fingerprints are identical."""
    if fp_a is None or fp_b is None:
        return False
    return fp_a['types'] == fp_b['types'] and fp_a['groups'] == fp_b['groups']


def _strip_platform_suffix(title):
    """Remove trailing platform markers like (PS4), (PS3, PS5), (PS4, PS VR) from a title.

    Preserves original casing unlike normalize_game_title().
    """
    return _PLATFORM_SUFFIX_RE.sub('', title).strip()


def _get_canonical_name(concepts, precomputed=None):
    """Pick the best canonical name from a set of concepts.

    Prefer real concepts (non-PP_) over stubs, then prefer the one with the
    most games as canonical. Strips platform suffixes since families are
    cross-platform by nature.
    """
    real = [c for c in concepts if not c.concept_id.startswith('PP_')]
    pool = real if real else list(concepts)
    if precomputed:
        best = max(pool, key=lambda c: precomputed['concept_game_count'].get(c.id, 0))
    else:
        best = max(pool, key=lambda c: c.games.count())
    return _strip_platform_suffix(best.unified_title)


def _calculate_confidence_and_reason(concept_a, concept_b, name_match_type='exact', precomputed=None):
    """Calculate confidence score between two concepts, returning (confidence, reason, signals)."""
    signals = {
        'name_match': name_match_type,
        'trophy_name_overlap': None,
        'fingerprint_match': None,
    }

    if precomputed:
        # Use pre-computed trophy data (in-memory lookups, no DB queries)
        names_a = precomputed['trophy_names'].get(concept_a.id, set())
        names_b = precomputed['trophy_names'].get(concept_b.id, set())
        if names_a and names_b:
            intersection = names_a & names_b
            name_overlap = len(intersection) / min(len(names_a), len(names_b))
        else:
            name_overlap = None

        fp_a = precomputed['trophy_fingerprints'].get(concept_a.id)
        fp_b = precomputed['trophy_fingerprints'].get(concept_b.id)
    else:
        name_overlap = calculate_trophy_name_overlap(concept_a, concept_b)
        fp_a = get_trophy_fingerprint(concept_a)
        fp_b = get_trophy_fingerprint(concept_b)

    fp_match = fingerprints_match(fp_a, fp_b)

    signals['trophy_name_overlap'] = name_overlap
    signals['fingerprint_match'] = fp_match

    reasons = []
    confidence = 0.0

    # High confidence paths
    if name_match_type == 'exact' and name_overlap is not None and name_overlap >= 0.5:
        confidence = 0.95
        reasons.append(f"Exact title match + {name_overlap:.0%} trophy name overlap")

    elif name_match_type == 'exact' and fp_match:
        confidence = 0.90
        reasons.append("Exact title match + identical trophy fingerprint")

    # Fuzzy name match — escalate to high confidence if trophy data confirms
    elif name_match_type == 'fuzzy' and name_overlap is not None and name_overlap >= 0.5:
        confidence = 0.90
        reasons.append(f"Fuzzy title match + {name_overlap:.0%} trophy name overlap")

    elif name_match_type == 'fuzzy' and fp_match:
        confidence = 0.88
        reasons.append("Fuzzy title match + identical trophy fingerprint")

    # Medium confidence paths
    elif name_match_type == 'fuzzy':
        if name_overlap is not None and name_overlap >= 0.3:
            confidence = 0.70
            reasons.append(f"Fuzzy title match + {name_overlap:.0%} trophy name overlap")
        else:
            confidence = 0.55
            reasons.append("Fuzzy title match (suffix stripped)")

    elif name_match_type == 'exact':
        confidence = 0.60
        if name_overlap is None:
            reasons.append("Exact title match but no trophy data for overlap check")
        else:
            reasons.append(f"Exact title match (trophy name overlap: {name_overlap:.0%})")

    reason = '; '.join(reasons) if reasons else "No strong signals"
    return confidence, reason, signals


def _precompute_data(all_concepts):
    """Run bulk queries upfront and return lookup dictionaries.

    Replaces per-concept DB queries with in-memory lookups for:
    - Game counts per concept
    - Trophy names and structural fingerprints
    - Pending proposal concept sets
    """
    # 1. Game counts — from already-prefetched games (no DB hit)
    concept_game_count = {c.id: len(c.games.all()) for c in all_concepts}

    # 2. All trophy data — single bulk query
    trophy_names = defaultdict(set)
    trophy_type_counts = defaultdict(lambda: defaultdict(int))
    trophy_group_ids = defaultdict(set)

    for concept_id, name, trophy_type, group_id in (
        Trophy.objects.filter(game__concept__isnull=False)
        .values_list('game__concept_id', 'trophy_name', 'trophy_type', 'trophy_group_id')
        .iterator()
    ):
        trophy_names[concept_id].add(name)
        trophy_type_counts[concept_id][trophy_type] += 1
        trophy_group_ids[concept_id].add(group_id)

    trophy_fingerprints = {}
    for concept_id, type_counts in trophy_type_counts.items():
        types = dict(type_counts)
        trophy_fingerprints[concept_id] = {
            'types': types,
            'groups': len(trophy_group_ids[concept_id]),
            'total': sum(types.values()),
        }

    # 3. Existing proposals — prefetch M2M in 2 queries
    # Track both pending and rejected to avoid re-proposing rejected matches
    existing_proposals_set = set()
    for proposal in (
        GameFamilyProposal.objects.filter(status__in=['pending', 'rejected'])
        .prefetch_related('concepts')
    ):
        concept_ids = frozenset(c.id for c in proposal.concepts.all())
        existing_proposals_set.add(concept_ids)

    return {
        'concept_game_count': concept_game_count,
        'trophy_names': dict(trophy_names),
        'trophy_fingerprints': trophy_fingerprints,
        'existing_proposals_set': existing_proposals_set,
    }


def find_matches(dry_run=False, auto_only=False, verbose=False, stdout=None):
    """Find and create GameFamily groupings.

    Groups concepts whose normalized titles match, using trophy data
    (name overlap, structural fingerprint) to determine confidence.
    Requires at least a title or trophy name similarity to flag a match.

    Args:
        dry_run: Print actions without creating anything
        auto_only: Only process high-confidence matches
        verbose: Print detailed reasoning
        stdout: Optional write function (e.g. management command's self.stdout.write)
                for verbose output. Falls back to logger.info if not provided.

    Returns:
        dict with counts: auto_created, proposals_created, skipped
    """
    def _log(msg):
        if stdout:
            stdout(msg)
        else:
            logger.info(msg)

    stats = {'auto_created': 0, 'proposals_created': 0, 'skipped': 0, 'total_concepts': 0}

    # Get all concepts with their games prefetched
    all_concepts = list(
        Concept.objects.prefetch_related('games').all()
    )
    stats['total_concepts'] = len(all_concepts)

    # Pre-compute all lookup data in bulk (~4 queries total)
    precomputed = _precompute_data(all_concepts)

    # Track which concepts have been matched in this run
    matched_concept_ids = set()

    # ── Pass 1: Name-based grouping ──
    # Group concepts by normalized title (from their games)
    title_groups = defaultdict(set)
    concept_titles = {}  # concept_id -> set of normalized titles

    for concept in all_concepts:
        if concept.family_id is not None:
            continue  # Already in a family

        titles = set()
        for game in concept.games.all():
            normalized = normalize_game_title(game.title_name)
            if normalized:
                titles.add(normalized)
                title_groups[normalized].add(concept.id)
        # Also check unified_title
        normalized = normalize_game_title(concept.unified_title)
        if normalized:
            titles.add(normalized)
            title_groups[normalized].add(concept.id)
        concept_titles[concept.id] = titles

    # Process name groups
    concept_map = {c.id: c for c in all_concepts}
    processed_groups = set()

    for normalized_title, concept_ids in title_groups.items():
        if len(concept_ids) < 2:
            continue

        # Sort for deterministic processing
        group_key = frozenset(concept_ids)
        if group_key in processed_groups:
            continue
        processed_groups.add(group_key)

        concepts_in_group = [concept_map[cid] for cid in concept_ids if cid in concept_map]

        # Filter out already-matched and already-in-family
        concepts_in_group = [
            c for c in concepts_in_group
            if c.id not in matched_concept_ids and c.family_id is None
        ]
        if len(concepts_in_group) < 2:
            continue

        # Determine match type: check if titles are exact or fuzzy match
        # (fuzzy = original titles differ but normalize to the same thing)
        # Strip platform suffixes before comparing so "Game (PS4)" and "Game (PS5)"
        # are correctly classified as exact matches rather than fuzzy.
        raw_titles = set()
        for c in concepts_in_group:
            for g in c.games.all():
                raw_titles.add(_strip_platform_suffix(g.title_name).lower().strip())
            raw_titles.add(_strip_platform_suffix(c.unified_title).lower().strip())

        name_match_type = 'exact' if len(raw_titles) == 1 else 'fuzzy'

        # Calculate pairwise confidence, use the best pair's confidence for the group
        best_confidence = 0.0
        best_reason = ""
        best_signals = {}

        for i, ca in enumerate(concepts_in_group):
            for cb in concepts_in_group[i + 1:]:
                conf, reason, signals = _calculate_confidence_and_reason(
                    ca, cb, name_match_type, precomputed
                )
                if conf > best_confidence:
                    best_confidence = conf
                    best_reason = reason
                    best_signals = signals

        canonical_name = _get_canonical_name(concepts_in_group, precomputed)

        if best_confidence >= 0.85:
            # High confidence — auto-create
            if verbose:
                _log(
                    f"[AUTO] '{canonical_name}' — {len(concepts_in_group)} concepts, "
                    f"confidence={best_confidence:.0%}: {best_reason}"
                )
            if not dry_run:
                family = GameFamily.objects.create(
                    canonical_name=canonical_name,
                    is_verified=False,
                )
                group_ids = [c.id for c in concepts_in_group]
                Concept.objects.filter(id__in=group_ids).update(family=family)
                for c in concepts_in_group:
                    c.family = family
                    c.family_id = family.id
                matched_concept_ids.update(group_ids)
            stats['auto_created'] += 1

        elif best_confidence >= 0.5 and not auto_only:
            # Medium confidence — proposal
            concept_id_set = frozenset(c.id for c in concepts_in_group)
            if concept_id_set not in precomputed['existing_proposals_set']:
                if verbose:
                    _log(
                        f"[PROPOSAL] '{canonical_name}' — {len(concepts_in_group)} concepts, "
                        f"confidence={best_confidence:.0%}: {best_reason}"
                    )
                if not dry_run:
                    proposal = GameFamilyProposal.objects.create(
                        proposed_name=canonical_name,
                        confidence=best_confidence,
                        match_reason=best_reason,
                        match_signals=best_signals,
                    )
                    proposal.concepts.set(concepts_in_group)
                    # Track newly created proposal so Pass 1 doesn't duplicate it
                    precomputed['existing_proposals_set'].add(concept_id_set)
                matched_concept_ids.update(c.id for c in concepts_in_group)
                stats['proposals_created'] += 1
            else:
                stats['skipped'] += 1

    return stats


def diagnose_concept(concept_id, top_n=10, stdout=None):
    """Compare a single concept against all others and return the top matches.

    Read-only diagnostic — does not create or modify any data.

    Args:
        concept_id: The Concept.concept_id string (e.g. 'PPSA01234')
        top_n: Number of top matches to return
        stdout: Write function for output

    Returns:
        dict with 'target' info and 'matches' list, or None if concept not found
    """
    def _out(msg=''):
        if stdout:
            stdout(msg)

    # Look up target concept
    try:
        target = Concept.objects.prefetch_related('games').get(concept_id=concept_id)
    except Concept.DoesNotExist:
        return None

    # Load all concepts and precompute data
    all_concepts = list(Concept.objects.prefetch_related('games').all())
    precomputed = _precompute_data(all_concepts)

    # Build target info header
    game_titles = [g.title_name for g in target.games.all()]
    fp = precomputed['trophy_fingerprints'].get(target.id)
    trophy_names_count = len(precomputed['trophy_names'].get(target.id, set()))

    _out(f'Diagnosing concept: {target.concept_id} — "{target.unified_title}"')
    _out(f'  Games: {", ".join(game_titles) if game_titles else "(none)"}')
    _out(f'  Family: {target.family.canonical_name if target.family else "None"}')
    if fp:
        type_parts = []
        for t in ['platinum', 'gold', 'silver', 'bronze']:
            count = fp['types'].get(t, 0)
            if count:
                type_parts.append(f'{count} {t}')
        _out(f'  Trophies: {fp["total"]} ({", ".join(type_parts)}) | {fp["groups"]} groups')
    else:
        _out('  Trophies: (no trophy data)')
    _out(f'  Trophy names: {trophy_names_count} unique names')

    # Get target's normalized titles for name match type detection
    target_normalized = set()
    for game in target.games.all():
        normalized = normalize_game_title(game.title_name)
        if normalized:
            target_normalized.add(normalized)
    normalized = normalize_game_title(target.unified_title)
    if normalized:
        target_normalized.add(normalized)

    target_raw = set()
    for game in target.games.all():
        target_raw.add(_strip_platform_suffix(game.title_name).lower().strip())
    target_raw.add(_strip_platform_suffix(target.unified_title).lower().strip())

    # Compare against every other concept
    results = []
    for other in all_concepts:
        if other.id == target.id:
            continue

        # Determine name match type
        other_normalized = set()
        for game in other.games.all():
            n = normalize_game_title(game.title_name)
            if n:
                other_normalized.add(n)
        n = normalize_game_title(other.unified_title)
        if n:
            other_normalized.add(n)

        title_overlap = target_normalized & other_normalized
        if title_overlap:
            # Titles normalize to the same thing — check if raw titles match
            other_raw = set()
            for game in other.games.all():
                other_raw.add(_strip_platform_suffix(game.title_name).lower().strip())
            other_raw.add(_strip_platform_suffix(other.unified_title).lower().strip())

            # If any raw title matches, it's exact; otherwise fuzzy
            name_match_type = 'exact' if target_raw & other_raw else 'fuzzy'
        else:
            name_match_type = 'none'

        confidence, reason, signals = _calculate_confidence_and_reason(
            target, other, name_match_type, precomputed
        )

        if confidence > 0:
            results.append({
                'concept_id': other.concept_id,
                'unified_title': other.unified_title,
                'confidence': confidence,
                'reason': reason,
                'signals': signals,
                'family': other.family.canonical_name if other.family else None,
                'family_id': other.family_id,
            })

    # Sort by confidence descending
    results.sort(key=lambda r: r['confidence'], reverse=True)
    top_results = results[:top_n]

    # Print results
    _out(f'\nTop {min(top_n, len(results))} closest matches (of {len(results)} with any signal):')

    if not top_results:
        _out('  (no matches found)')
    else:
        for i, match in enumerate(top_results, 1):
            _out('─' * 60)
            _out(
                f' #{i:<3} {match["confidence"]:.0%} | {match["concept_id"]} — '
                f'"{match["unified_title"]}"'
            )

            name_sig = match['signals']['name_match']
            trophy_overlap = match['signals']['trophy_name_overlap']
            fp_match = match['signals']['fingerprint_match']

            trophy_str = f'{trophy_overlap:.0%}' if trophy_overlap is not None else 'n/a'
            fp_str = 'yes' if fp_match else 'no'

            _out(
                f'         | Name: {name_sig} | Trophy names: {trophy_str} '
                f'| Fingerprint: {fp_str}'
            )
            _out(f'         | Reason: {match["reason"]}')
            family_str = f'{match["family"]}' if match['family'] else 'None'
            _out(f'         | Family: {family_str}')

    return {'target': target, 'matches': top_results}
