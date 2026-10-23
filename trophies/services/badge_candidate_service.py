"""Find franchises and series (IGDB collections) worth a badge (`badge_candidates`).

A subject is a candidate when at least one platform edition (an active PlatformGroup: Legacy HD, Ultra HD)
would GATE two or more of its games with a LIVE contract. Not every game in the subject needs a contract; the
rule only asks that one edition has enough contracted games to be worth earning.

Games are counted in the same unit as a stage, so the count reads as stages of the badge `build_badge` makes:
  - A "game" is an ORIGIN game (`badge_stage_service.origin_key`): a cross-gen pair, a port and a remaster all
    fold into their original, as `generate_badge_stages` folds them into one stage. A bundle-only (episodic)
    contract is ONE game however many episode concepts it lists: the curator builds it as one bundle stage.
  - It counts on an edition only where the engine would let it gate there (`badge_engine.evaluate_stage`, the
    rule `recompute_required_stages` uses): a game on the edition's platforms that is obtainable and, on an
    edition that excludes delisted games, not delisted.
  - Membership matches `badge_coverage_service.series_candidates` (franchise: any non-excluded link;
    collection: also no spin-offs).
Only contracted versions are read, so an UNcontracted remaster on another edition does not count here even
though the built stage would hold it (the rule asks for contracted games).

Candidates rank by average unique players per contracted game, from the nightly `Concept.page_player_count` (a
game page's deduped player count). An origin with several contracted versions takes its most-played one.
"""

from collections import defaultdict
from dataclasses import dataclass

from django.db.models import Q

from trophies.models import (
    BadgeSeries, Concept, ConceptFranchise, Contract, Franchise, Game, IGDBMatch, PlatformGroup,
)
from trophies.services.badge_engine import GroupInput, StageInput, evaluate_stage
from trophies.services.badge_orchestrator import _catalog_game_state
from trophies.services.badge_stage_service import _origin_queryset, origin_key
from trophies.services.contract_service import contract_by_concept_map

MIN_EDITION_GAMES = 2


@dataclass
class BadgeCandidate:
    """One franchise or series that clears the bar. `edition_counts` is {PlatformGroup.key: contracted games
    that gate on that edition} (editions with none are absent); `game_count` counts every contracted game."""
    subject: Franchise
    edition_counts: dict
    game_count: int
    avg_players: float
    has_badge: bool


def active_editions():
    """The active platform groups, in display order."""
    return list(PlatformGroup.objects.filter(is_active=True).order_by('sort_order', 'name'))


# --- which subjects already have a badge --------------------------------------------
# One rule, in two shapes: `existing_badge` answers it for one subject (build_badge refuses on it) and
# `_subjects_with_badges` for the whole catalogue (badge_candidates hides on it). Keep the two in step; the
# parallel tests in test_badge_candidates and test_build_badge pin both.
# A SERIES is taken by any badge naming it; a FRANCHISE only by a franchise badge (one naming no series); a
# DEVELOPER by any badge naming it. A series badge that also names its franchise covers the series, and
# leaves the franchise free for its own sister badge (the God of War franchise vs the God of War series).


def existing_badge(franchise=None, collection=None, developer=None):
    """The BadgeSeries that already covers this subject, or None."""
    if franchise is None and collection is None and developer is None:
        raise ValueError("existing_badge needs a franchise, collection or developer.")
    if developer is not None:
        found = BadgeSeries.objects.filter(developer=developer)
    elif collection is not None:
        found = BadgeSeries.objects.filter(collection=collection)
    else:
        found = BadgeSeries.objects.filter(franchise=franchise, collection__isnull=True)
    return found.order_by('pk').first()


def _subjects_with_badges():
    """Ids of every franchise/series `existing_badge` would find a badge for."""
    rows = BadgeSeries.objects.filter(Q(franchise__isnull=False) | Q(collection__isnull=False))
    return {collection_id or franchise_id for franchise_id, collection_id in
            rows.values_list('franchise_id', 'collection_id')}


# --- candidates ----------------------------------------------------------------------


def _contracts_by_concept():
    """{concept_id: Contract} for every concept a LIVE contract covers.

    The prefilter only narrows the id list (an igdb-keyed contract's anchored, trusted concepts, plus the
    members of a bundle-only contract's bundles). `contract_by_concept_map` makes the actual call, so the
    precedence rules (the igdb home beats a bundle) live in one place.
    """
    live = Contract.objects.filter(is_live=True)
    concept_ids = (
        Concept.objects.filter(
            Q(anchor_migration_completed_at__isnull=False,
              igdb_match__status__in=IGDBMatch.TRUSTED_STATUSES,
              igdb_match__igdb_id__in=live.filter(igdb_id__isnull=False).values('igdb_id'))
            | Q(contract_bundles__contract__in=live.filter(igdb_id__isnull=True))
        )
        .values_list('id', flat=True)
        .distinct()
    )
    return contract_by_concept_map(concept_ids, prefetch_jobs=False)


def _game_states_by_concept(concept_ids):
    """{concept_id: [GameState]} carrying the catalog facts gating reads."""
    states = defaultdict(list)
    games = Game.objects.filter(concept_id__in=concept_ids).only(
        'id', 'concept_id', 'title_platform', 'is_obtainable', 'is_delisted',
    )
    for game in games:
        states[game.concept_id].append(_catalog_game_state(game))
    return states


def _origin(concept, contract):
    """The stage-sized game a contracted concept belongs to. A bundle-only contract has no igdb id to fold
    its episodes on (they rarely share a family), so the contract itself is the game."""
    return ('contract', contract.pk) if contract.igdb_id is None else origin_key(concept)


def _gates(states, group):
    """Whether a stage holding these games would gate on `group`, by the engine's own rule. The stage number
    only has to be non-zero (stage 0 never gates); evaluate_stage reads nothing else from it."""
    return evaluate_stage(StageInput(1, tuple(states)), group).gates


def badge_candidates(include_existing=False):
    """Franchises and series with at least MIN_EDITION_GAMES live-contracted games gating on one edition, best
    attended first. A subject that already has a badge is left out unless `include_existing`."""
    contract_of = _contracts_by_concept()
    if not contract_of:
        return []
    concept_ids = list(contract_of)
    origin_of = {
        concept.pk: _origin(concept, contract_of[concept.pk])
        for concept in _origin_queryset().filter(id__in=concept_ids)
    }
    players_of = dict(Concept.objects.filter(id__in=concept_ids).values_list('id', 'page_player_count'))
    states_of = _game_states_by_concept(concept_ids)

    links = (
        ConceptFranchise.objects.filter(concept_id__in=concept_ids, is_excluded=False)
        .filter(Q(franchise__source_type='franchise') | Q(franchise__source_type='collection', is_spinoff=False))
        .values_list('franchise_id', 'concept_id')
    )
    # {subject_id: {origin: [GameState]}} and {subject_id: {origin: players}}
    origin_games = defaultdict(lambda: defaultdict(list))
    origin_players = defaultdict(lambda: defaultdict(int))
    for subject_id, concept_id in links:
        origin = origin_of[concept_id]
        origin_games[subject_id][origin].extend(states_of[concept_id])
        origin_players[subject_id][origin] = max(origin_players[subject_id][origin], players_of[concept_id])

    groups = [(e.key, GroupInput(frozenset(e.platforms), e.exclude_delisted)) for e in active_editions()]
    edition_counts = {}
    for subject_id, origins in origin_games.items():
        counts = {}
        for key, group in groups:
            gating = sum(1 for states in origins.values() if _gates(states, group))
            if gating:
                counts[key] = gating
        if any(count >= MIN_EDITION_GAMES for count in counts.values()):
            edition_counts[subject_id] = counts

    with_badge = _subjects_with_badges()
    candidates = []
    for subject in Franchise.objects.filter(id__in=list(edition_counts)):
        has_badge = subject.pk in with_badge
        if has_badge and not include_existing:
            continue
        players = origin_players[subject.pk]
        candidates.append(BadgeCandidate(
            subject=subject,
            edition_counts=edition_counts[subject.pk],
            game_count=len(players),
            avg_players=sum(players.values()) / len(players),
            has_badge=has_badge,
        ))
    candidates.sort(key=lambda c: (-c.avg_players, c.subject.name.lower(), c.subject.pk))
    return candidates
