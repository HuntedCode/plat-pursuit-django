"""Which contracts a hunter may put in a given slot, and which of those they have already finished.

Three questions, all per-hunter and therefore all answered in the DATABASE. Per-user aggregation in
Python is the OOM pattern this project has paid for repeatedly (see the rule in CLAUDE.md), and a
hunter opening a slot picker is a whale as often as not.

1. `eligible_contracts` -- the pool for a slot. One rule, both types.
2. `hatch_is_open` -- is that pool down to `HATCH_THRESHOLD` or fewer, so already-completed contracts
   become selectable? A COUNT, taken when a picker opens, never for 26 slots on a page render.
3. `completion_dates` / `importable_ids` -- WHEN the hunter completed a contract, for the first-run
   history importer. Batched across a page of candidates in three bounded queries.

WHY THE DATE IS NOT `EarnedContract.*_reached_at`, which is the obvious place to look: those stamp
`timezone.now()` at DETECTION time (`contract_service.mark_contract_reached`), not when the hunter
earned anything. A contract staged today and published next month stamps them for everyone who
finished it years ago, and the weekly full sweep in `process_contracts` does the same. Anchoring the
importer's fairness rule on them would hand slots to hunters who did nothing. So the date comes from
trophy data, which records when the work actually happened.
"""
from django.db.models import Exists, Min, OuterRef

from challenges.models import CHALLENGE_TYPE_AZ, HATCH_THRESHOLD
from trophies.models import (
    Concept,
    Contract,
    EarnedContract,
    EarnedTrophy,
    IGDBMatch,
    ProfileGame,
)


def eligible_contracts(profile, challenge, key):
    """Live contracts this hunter could still complete for `challenge`'s `key` slot.

    THE ONLY EXCLUSIONS ARE THE TWO THAT HAVE TO BE. The old system refused any game a hunter was
    already 50% through and expanded that refusal across Concept and GameFamily siblings; that rule is
    gone (owner's call, 2026-09-26). What remains:

    - a contract already used elsewhere in THIS run, so one game cannot fill two slots; and
    - a contract this hunter has already COMPLETED, because a slot filled with one would land complete
      instantly and the run would finish itself.

    The second is exactly what `hatch_is_open` lifts when supply is too thin to respect it.

    Note the A-Z filter's side effect, stated because it is easy to mistake for an oversight: a
    contract whose name starts with a DIGIT matches no letter prefix, so the 12 such contracts in the
    live pool are unusable for A-Z. Not a special case in the code -- a consequence of prefix matching
    that nothing needs to handle, but that somebody auditing the pool should know.
    """
    pool = Contract.objects.filter(is_live=True)

    if challenge.challenge_type == CHALLENGE_TYPE_AZ:
        # `istartswith` on an unindexed 255-char column. Deliberate at this catalogue size: a sequential
        # scan of a few thousand rows is sub-millisecond, and the index that WOULD serve this is a
        # functional one on `UPPER(name)` (a plain `varchar_pattern_ops` index cannot serve the
        # case-insensitive form). Worth adding if the pool reaches five figures; not before.
        pool = pool.filter(name__istartswith=key)
    else:
        # Exactly one job row can match one slug, so this cannot duplicate a contract.
        pool = pool.filter(jobs__slug=key)

    used = challenge.slots.exclude(contract_slug='').values_list('contract_slug', flat=True)
    pool = pool.exclude(slug__in=used)

    return pool.exclude(Exists(_completed_by(profile)))


def completed_contracts(profile, challenge, key):
    """The already-completed contracts `eligible_contracts` refuses, for when the hatch lifts that.

    Same shape minus the completion exclusion and plus its inverse, rather than a flag on
    `eligible_contracts`: the two callers want disjoint sets and a boolean parameter that flips a
    predicate is how they end up sharing a bug.
    """
    pool = Contract.objects.filter(is_live=True)
    if challenge.challenge_type == CHALLENGE_TYPE_AZ:
        pool = pool.filter(name__istartswith=key)
    else:
        pool = pool.filter(jobs__slug=key)

    used = challenge.slots.exclude(contract_slug='').values_list('contract_slug', flat=True)
    return pool.exclude(slug__in=used).filter(Exists(_completed_by(profile)))


def _completed_by(profile):
    """Correlated subquery: has this hunter completed the outer contract?

    `EarnedContract` existing IS completion, and that is exact rather than convenient: the row is only
    created once a tier is reached (`mark_contract_reached` returns early otherwise), and either tier
    counts because platinum is not required. So there is no completion logic here to drift from the
    contract engine's.
    """
    return EarnedContract.objects.filter(profile=profile, contract=OuterRef('pk'))


def hatch_is_open(profile, challenge, key):
    """Is this slot's pool down to `HATCH_THRESHOLD` or fewer, lifting the completed-contract rule?

    A COUNT in the database, called when one slot's picker opens. Never mapped over all 26 slots for a
    page render: that would be 26 counts per view, and the page does not need to know.
    """
    return eligible_contracts(profile, challenge, key).count() <= HATCH_THRESHOLD


# ── the importer's date ───────────────────────────────────────────────────────────────────────────

def completion_dates(profile, contracts):
    """{contract_id: datetime} of when this hunter FIRST completed each of `contracts`.

    Batched deliberately: a slot picker shows a page of candidates and needs to mark which are
    importable, so a per-contract query would be 20 round trips that each look cheap. Three bounded
    queries instead, all filtered by profile AND by a bounded concept set.

    THE EARLIEST qualifying moment, not the latest, and that is the strict reading on purpose. A hunter
    who platted in 2019 and cleaned up DLC in 2024 completed the game in 2019; taking the later date
    would let one recent DLC trophy re-date a pre-join platinum into importable territory.

    Two sources, mirroring the two tiers `contract_service._detect_tiers` reads:

    - the platinum trophy's own `earned_date_time`, which is exact; and
    - for a 100% completion, `ProfileGame.most_recent_trophy_date`. That is the completion moment
      precisely BECAUSE the row is at `progress=100`: everything defined on the game is earned, so the
      newest trophy is the one that finished it. If DLC lands later, `detect_dlc_and_refresh` drops
      progress below 100 and the row stops qualifying at all.

    A contract the hunter has not completed is absent from the result rather than mapped to None.
    """
    contracts = list(contracts)
    if not contracts:
        return {}

    concepts_by_contract = _member_concepts_by_contract(contracts)
    all_concept_ids = {cid for ids in concepts_by_contract.values() for cid in ids}
    if not all_concept_ids:
        return {}

    plat_dates = dict(
        EarnedTrophy.objects
        .filter(profile=profile, earned=True, trophy__trophy_type='platinum',
                trophy__game__concept_id__in=all_concept_ids,
                earned_date_time__isnull=False)
        .values_list('trophy__game__concept_id')
        .annotate(first=Min('earned_date_time'))
        .values_list('trophy__game__concept_id', 'first')
    )
    full_dates = dict(
        ProfileGame.objects
        .filter(profile=profile, progress=100, game__concept_id__in=all_concept_ids,
                most_recent_trophy_date__isnull=False)
        .values_list('game__concept_id')
        .annotate(first=Min('most_recent_trophy_date'))
        .values_list('game__concept_id', 'first')
    )

    out = {}
    for contract_id, concept_ids in concepts_by_contract.items():
        moments = [d for cid in concept_ids
                   for d in (plat_dates.get(cid), full_dates.get(cid)) if d is not None]
        if moments:
            out[contract_id] = min(moments)
    return out


def importable_ids(profile, contracts, joined_at):
    """Ids of `contracts` this hunter completed AFTER `joined_at`, i.e. importable on a first run.

    `joined_at` is `CustomUser.date_joined`; `Profile` carries no creation timestamp. That predates
    PSN linking, which makes this the generous reading of "after you joined" -- deliberately, since the
    alternative punishes somebody for the gap between signing up and linking.
    """
    if joined_at is None:
        return set()
    return {cid for cid, when in completion_dates(profile, contracts).items() if when > joined_at}


def _member_concepts_by_contract(contracts):
    """{contract_id: {concept_id, ...}} in two queries, however many contracts.

    Membership is DERIVED, so this is the same rule `contract_service.contract_by_concept_map` applies,
    read the other way round: anchored concepts whose TRUSTED match carries the contract's raw igdb id,
    plus any concept riding along in one of its bundles. `Contract.member_concept_ids()` answers this
    per contract and would be one query each.
    """
    by_igdb = {c.igdb_id: c.id for c in contracts if c.igdb_id is not None}
    out = {c.id: set() for c in contracts}

    if by_igdb:
        rows = (
            Concept.objects
            .filter(anchor_migration_completed_at__isnull=False,
                    igdb_match__status__in=IGDBMatch.TRUSTED_STATUSES,
                    igdb_match__igdb_id__in=by_igdb.keys())
            .values_list('igdb_match__igdb_id', 'id')
        )
        for igdb_id, concept_id in rows:
            out[by_igdb[igdb_id]].add(concept_id)

    # The episodic case: a contract whose members come from `ContractBundle.concepts` rather than from
    # an igdb id. Included because `_detect_tiers` counts them, so leaving them out would report an
    # episodic contract as never completed and quietly make it un-importable.
    bundle_rows = (
        Contract.objects
        .filter(id__in=[c.id for c in contracts], bundles__concepts__isnull=False)
        .values_list('id', 'bundles__concepts__id')
    )
    for contract_id, concept_id in bundle_rows:
        if concept_id is not None:
            out[contract_id].add(concept_id)

    return out
