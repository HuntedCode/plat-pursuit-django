"""A contract's game flags: "can this still be finished?" for each of the site's flag badges.

A contract completes through ANY one of its own (anchored) concepts' stacks, or through a bundle, which counts only
as a WHOLE (`contract_service._detect_tiers`). So a flag holds when no way to finish the contract has a game without
it (owner, 2026-10-10 for delisted and unobtainable; 2026-10-24 for online and shovelware, the badges' every-version
rule applied to a contract). One listed stack among the anchored concepts clears Delisted; a bundle clears it only
when every one of its concepts has a listed stack. Pooling bundle games with the rest under-warned: a bundle of one
delisted and one listed episode looked available while it could not be finished. A contract with no member games on
record says nothing, rather than "all of none". A concept with no games on record yet is UNKNOWN, not unavailable: it
never makes a flag show, because the flags name a fact about stacks that do exist.

Its own module because the picker, the board's squares (`slot_render`) and the Career board all ask it, and the
picker already imports `slot_render`.
"""
from challenges.services import eligibility
from trophies.models import SHOVELWARE_FLAGGED_STATUSES, Game
from trophies.services.flag_badges import FLAG_ORDER


def contract_flag_keys(contracts, membership=None):
    """`{contract id: flag keys}`, for every surface a contract shows on: the picker and optimizer rows, the Career
    board and a job's contracts, the new-contracts modal and a challenge's squares, so a
    contract wears the same badges wherever it is (owner, 2026-10-24). Three queries for the page, or one when the
    caller already read `membership` (`eligibility.contract_membership`) for something else, as a board does for
    its covers."""
    if not contracts:
        return {}
    if membership is None:
        membership = eligibility.contract_membership(contracts)
    else:
        # A shared read can hold more contracts than were asked about (a board's covers read every square's).
        asked = {c.id for c in contracts}
        membership = {cid: m for cid, m in membership.items() if cid in asked}
    concepts = {contract_id: m['anchored'].union(*m['bundles']) for contract_id, m in membership.items()}
    by_concept = flag_rows(concepts)
    return {contract_id: availability(membership[contract_id], by_concept,
                                       [g for concept_id in concept_ids for g in by_concept.get(concept_id, [])])
            for contract_id, concept_ids in concepts.items()}


def flag_rows(concepts_by_contract):
    """`{concept id: [(platforms, delisted, obtainable, online, shovelware), ...]}`, one query for every concept."""
    every_concept = {cid for ids in concepts_by_contract.values() for cid in ids}
    by_concept = {}
    if every_concept:
        for concept_id, platforms, delisted, obtainable, online, shovel in (
                Game.objects.filter(concept_id__in=every_concept)
                .values_list('concept_id', 'title_platform', 'is_delisted', 'is_obtainable',
                             'has_online_trophies', 'shovelware_status')):
            by_concept.setdefault(concept_id, []).append(
                (platforms, delisted, obtainable, online, shovel in SHOVELWARE_FLAGGED_STATUSES))
    return by_concept


#: For each flag, what a game must be to CLEAR it: a flag holds when no way to finish the contract has such a game.
_CLEARS = {
    'unobtainable': lambda g: g[2],
    'delisted': lambda g: not g[1],
    'online': lambda g: not g[3],
    'shovelware': lambda g: not g[4],
}


def availability(membership, by_concept, games):
    """One contract's flag keys in `FLAG_ORDER`; nothing for a contract with no member games on record."""
    if not games:
        return []
    return [key for key in FLAG_ORDER
            if key in _CLEARS and not finishable(membership, by_concept, _CLEARS[key])]


def finishable(membership, games_by_concept, ok):
    """Whether some way to finish a contract has a game `ok` accepts: any anchored concept with one, or a bundle
    whose every concept has one. See the module docstring for why a bundle only counts whole.

    A CONCEPT WITH NO GAMES ON RECORD COUNTS AS OK. Nothing about it is delisted or unobtainable; it simply has not
    synced. Counting it as a failure labelled a bundle of one listed episode and one not-yet-synced one
    "Delisted", which names a fact that is not true."""
    def has(concept_id):
        games = games_by_concept.get(concept_id)
        return not games or any(ok(g) for g in games)

    return (any(has(cid) for cid in membership['anchored'])
            or any(all(has(cid) for cid in bundle) for bundle in membership['bundles']))
