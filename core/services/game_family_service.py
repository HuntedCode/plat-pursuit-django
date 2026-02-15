import logging
import re
from collections import defaultdict

from django.db.models import Count

from trophies.models import Concept, Game, GameFamily, GameFamilyProposal, Trophy

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


def calculate_icon_overlap(concept_a, concept_b):
    """Compare trophy icon URLs. Language-agnostic visual fingerprint. Returns 0.0-1.0 or None."""
    icons_a = set(
        Trophy.objects.filter(game__concept=concept_a, trophy_icon_url__isnull=False)
        .exclude(trophy_icon_url='')
        .values_list('trophy_icon_url', flat=True)
    )
    icons_b = set(
        Trophy.objects.filter(game__concept=concept_b, trophy_icon_url__isnull=False)
        .exclude(trophy_icon_url='')
        .values_list('trophy_icon_url', flat=True)
    )
    if not icons_a or not icons_b:
        return None
    intersection = icons_a & icons_b
    return len(intersection) / min(len(icons_a), len(icons_b))


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


def _has_concept_lock(concept):
    """Check if any game in this concept has concept_lock=True."""
    return concept.games.filter(concept_lock=True).exists()


def _concepts_already_grouped(concepts):
    """Check if all concepts are already in the same family."""
    families = {c.family_id for c in concepts if c.family_id is not None}
    if len(families) == 1:
        return True
    return False


def _proposal_exists_for(concept_ids):
    """Check if a pending proposal already covers this exact concept set."""
    pending = GameFamilyProposal.objects.filter(status='pending')
    for proposal in pending:
        proposal_ids = set(proposal.concepts.values_list('id', flat=True))
        if proposal_ids == concept_ids:
            return True
    return False


def _calculate_confidence_and_reason(concept_a, concept_b, name_match_type='exact', precomputed=None):
    """Calculate confidence score between two concepts, returning (confidence, reason, signals)."""
    signals = {
        'name_match': name_match_type,
        'trophy_name_overlap': None,
        'icon_overlap': None,
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

        icons_a = precomputed['trophy_icons'].get(concept_a.id, set())
        icons_b = precomputed['trophy_icons'].get(concept_b.id, set())
        if icons_a and icons_b:
            intersection = icons_a & icons_b
            icon_overlap = len(intersection) / min(len(icons_a), len(icons_b))
        else:
            icon_overlap = None

        fp_a = precomputed['trophy_fingerprints'].get(concept_a.id)
        fp_b = precomputed['trophy_fingerprints'].get(concept_b.id)
    else:
        name_overlap = calculate_trophy_name_overlap(concept_a, concept_b)
        icon_overlap = calculate_icon_overlap(concept_a, concept_b)
        fp_a = get_trophy_fingerprint(concept_a)
        fp_b = get_trophy_fingerprint(concept_b)

    fp_match = fingerprints_match(fp_a, fp_b)

    signals['trophy_name_overlap'] = name_overlap
    signals['icon_overlap'] = icon_overlap
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

    elif icon_overlap is not None and icon_overlap >= 0.7:
        confidence = 0.90
        reasons.append(f"Trophy icon URL overlap: {icon_overlap:.0%}")

    elif fp_match and icon_overlap is not None and icon_overlap >= 0.5:
        confidence = 0.88
        reasons.append(f"Identical trophy fingerprint + {icon_overlap:.0%} icon overlap")

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

    elif name_match_type == 'exact' and name_overlap is None:
        confidence = 0.60
        reasons.append("Exact title match but no trophy data for overlap check")

    elif fp_match and icon_overlap is None:
        confidence = 0.55
        reasons.append("Identical trophy fingerprint (no icon data)")

    elif icon_overlap is not None and 0.3 <= icon_overlap < 0.7:
        confidence = 0.55
        reasons.append(f"Partial trophy icon overlap: {icon_overlap:.0%}")

    reason = '; '.join(reasons) if reasons else "No strong signals"
    return confidence, reason, signals


def _precompute_data(all_concepts):
    """Run bulk queries upfront and return lookup dictionaries.

    Replaces per-concept DB queries with in-memory lookups for:
    - Concept lock status
    - Game counts per concept
    - Trophy names, icons, and structural fingerprints
    - Pending proposal concept sets
    """
    # 1. Concept lock status — single query
    locked_concept_ids = set(
        Game.objects.filter(concept_lock=True, concept__isnull=False)
        .values_list('concept_id', flat=True)
        .distinct()
    )

    # 2. Game counts — from already-prefetched games (no DB hit)
    concept_game_count = {c.id: len(c.games.all()) for c in all_concepts}

    # 3. All trophy data — single bulk query
    trophy_names = defaultdict(set)
    trophy_icons = defaultdict(set)
    trophy_type_counts = defaultdict(lambda: defaultdict(int))
    trophy_group_ids = defaultdict(set)

    for concept_id, name, trophy_type, icon_url, group_id in (
        Trophy.objects.filter(game__concept__isnull=False)
        .values_list('game__concept_id', 'trophy_name', 'trophy_type', 'trophy_icon_url', 'trophy_group_id')
        .iterator()
    ):
        trophy_names[concept_id].add(name)
        trophy_type_counts[concept_id][trophy_type] += 1
        trophy_group_ids[concept_id].add(group_id)
        if icon_url:
            trophy_icons[concept_id].add(icon_url)

    trophy_fingerprints = {}
    for concept_id, type_counts in trophy_type_counts.items():
        types = dict(type_counts)
        trophy_fingerprints[concept_id] = {
            'types': types,
            'groups': len(trophy_group_ids[concept_id]),
            'total': sum(types.values()),
        }

    # 4. Pending proposals — prefetch M2M in 2 queries
    pending_proposals_set = set()
    for proposal in GameFamilyProposal.objects.filter(status='pending').prefetch_related('concepts'):
        concept_ids = frozenset(c.id for c in proposal.concepts.all())
        pending_proposals_set.add(concept_ids)

    return {
        'locked_concept_ids': locked_concept_ids,
        'concept_game_count': concept_game_count,
        'trophy_names': dict(trophy_names),
        'trophy_icons': dict(trophy_icons),
        'trophy_fingerprints': trophy_fingerprints,
        'pending_proposals_set': pending_proposals_set,
    }


def find_matches(dry_run=False, auto_only=False, verbose=False):
    """Find and create GameFamily groupings.

    Pass 1: Name-based grouping (exact + fuzzy normalized titles)
    Pass 2: Trophy-based grouping (catches cross-language matches)

    Args:
        dry_run: Print actions without creating anything
        auto_only: Only process high-confidence matches
        verbose: Print detailed reasoning

    Returns:
        dict with counts: auto_created, proposals_created, skipped
    """
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
        if concept.id in precomputed['locked_concept_ids']:
            continue  # Admin locked

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

        if _concepts_already_grouped(concepts_in_group):
            stats['skipped'] += 1
            continue

        # Determine match type: check if titles are exact or fuzzy match
        # (fuzzy = original titles differ but normalize to the same thing)
        raw_titles = set()
        for c in concepts_in_group:
            for g in c.games.all():
                raw_titles.add(g.title_name.lower().strip())
            raw_titles.add(c.unified_title.lower().strip())

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
                logger.info(
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
            if concept_id_set not in precomputed['pending_proposals_set']:
                if verbose:
                    logger.info(
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
                    precomputed['pending_proposals_set'].add(concept_id_set)
                matched_concept_ids.update(c.id for c in concepts_in_group)
                stats['proposals_created'] += 1
            else:
                stats['skipped'] += 1

    # ── Pass 2: Trophy-based grouping (cross-language) ──
    # For concepts not matched in Pass 1 and not in a family
    unmatched = [
        c for c in all_concepts
        if c.id not in matched_concept_ids
        and c.family_id is None
        and c.id not in precomputed['locked_concept_ids']
    ]

    # Compare all unmatched pairs using pre-computed trophy data
    pass2_matched = set()

    for i, ca in enumerate(unmatched):
        if ca.id in pass2_matched:
            continue
        for cb in unmatched[i + 1:]:
            if cb.id in pass2_matched:
                continue

            fp_a = precomputed['trophy_fingerprints'].get(ca.id)
            fp_b = precomputed['trophy_fingerprints'].get(cb.id)
            icons_a = precomputed['trophy_icons'].get(ca.id, set())
            icons_b = precomputed['trophy_icons'].get(cb.id, set())

            # Check icon overlap
            icon_overlap = None
            if icons_a and icons_b:
                intersection = icons_a & icons_b
                icon_overlap = len(intersection) / min(len(icons_a), len(icons_b))

            fp_match = fingerprints_match(fp_a, fp_b)

            signals = {
                'name_match': 'none',
                'trophy_name_overlap': None,
                'icon_overlap': icon_overlap,
                'fingerprint_match': fp_match,
            }

            confidence = 0.0
            reasons = []

            if icon_overlap is not None and icon_overlap >= 0.7:
                confidence = 0.90
                reasons.append(f"Trophy icon URL overlap: {icon_overlap:.0%}")
            elif fp_match and icon_overlap is not None and icon_overlap >= 0.5:
                confidence = 0.88
                reasons.append(f"Identical fingerprint + {icon_overlap:.0%} icon overlap")
            elif fp_match and icon_overlap is None:
                confidence = 0.55
                reasons.append("Identical trophy fingerprint (no icon data)")
            elif icon_overlap is not None and 0.3 <= icon_overlap < 0.7:
                confidence = 0.55
                reasons.append(f"Partial trophy icon overlap: {icon_overlap:.0%}")

            if confidence < 0.5:
                continue

            reason = '; '.join(reasons)
            canonical_name = _get_canonical_name([ca, cb], precomputed)

            if confidence >= 0.85:
                if verbose:
                    logger.info(
                        f"[AUTO/P2] '{canonical_name}' — 2 concepts, "
                        f"confidence={confidence:.0%}: {reason}"
                    )
                if not dry_run:
                    family = GameFamily.objects.create(
                        canonical_name=canonical_name,
                        is_verified=False,
                    )
                    Concept.objects.filter(id__in=[ca.id, cb.id]).update(family=family)
                    ca.family = family
                    ca.family_id = family.id
                    cb.family = family
                    cb.family_id = family.id
                    pass2_matched.add(ca.id)
                    pass2_matched.add(cb.id)
                stats['auto_created'] += 1

            elif not auto_only:
                concept_id_set = frozenset({ca.id, cb.id})
                if concept_id_set not in precomputed['pending_proposals_set']:
                    if verbose:
                        logger.info(
                            f"[PROPOSAL/P2] '{canonical_name}' — 2 concepts, "
                            f"confidence={confidence:.0%}: {reason}"
                        )
                    if not dry_run:
                        proposal = GameFamilyProposal.objects.create(
                            proposed_name=canonical_name,
                            confidence=confidence,
                            match_reason=reason,
                            match_signals=signals,
                        )
                        proposal.concepts.set([ca, cb])
                        precomputed['pending_proposals_set'].add(concept_id_set)
                        pass2_matched.add(ca.id)
                        pass2_matched.add(cb.id)
                    stats['proposals_created'] += 1

    return stats
