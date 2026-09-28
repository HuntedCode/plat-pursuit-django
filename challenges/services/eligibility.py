"""Which contracts a hunter may put in a given slot, and which of those they have already finished.

Everything here is per-hunter and therefore answered in the DATABASE. Per-user aggregation in Python is
the OOM pattern this project has paid for repeatedly (the rule in CLAUDE.md), and a hunter opening a
slot picker is a whale as often as not.

ONE DEFINITION OF "FITS THIS SLOT", and that matters more than it looks. `_shape` is the only place
that knows a letter slot wants a name prefix and a job slot wants a job, and every caller goes through
it. An earlier version spelled the A-Z rule twice -- once as a queryset filter (`name__istartswith`) and
once in Python (`name.upper().startswith(...)`) -- and the two DISAGREE.

Both sides fold with UPPER: Django compiles `istartswith` on Postgres to
`UPPER(name::text) LIKE UPPER('X%')`. The divergence is that Python's `str.upper()` does FULL Unicode
case mapping, one character to many, while libc's `upper()` is per-character. Measured against this
project's own cluster (PG 15, libc `en_US.utf8`):

    'final...' spelled with an fi ligature  ->  Python 'FINAL' (4 chars become 5), pool NO, gate YES
    'sseta...' spelled with a sharp s       ->  Python 'SSETA' (4 chars become 5), pool NO, gate YES

ONE DIRECTION, not two: the Python gate accepts names the pool never holds. An earlier draft of this
comment claimed the fold was `lower()`, claimed both directions, and offered a dotted capital I as an
example -- which does NOT diverge, because U+0130 uppercases to itself in Python and in libc alike. The
measurement had been taken against `lower()` as a proxy for what the ORM emits, and the proxy was wrong.

The surviving direction is the quieter and worse one. A contract the gate accepts but the pool never
held is a contract `hatch_is_open` cannot count, so the hatch can open while supply that the gate would
have taken sits unseen.

THE SLOT'S OWN OCCUPANT IS NOT "USED". `_slot_pool` excludes contracts already placed in OTHER squares of
the run, never the one being filled -- a slot's pool has to be the same size whether that slot is empty or
being reassigned. Getting this wrong understated the pool by one on every reassignment, which tipped
`hatch_is_open` one contract early and handed out a permanently locked, XP-bearing square the rule did not
allow.

WHY THE IMPORTER'S DATE IS NOT `EarnedContract.*_reached_at`: those stamp `timezone.now()` at DETECTION
time (`contract_service.mark_contract_reached`), not when the hunter earned anything. A contract staged
today and published next month stamps them for everyone who finished it years ago, and the weekly full
sweep in `process_contracts` does the same. So the date comes from trophy data, which records when the
work actually happened.
"""
from django.db.models import Exists, Min, OuterRef
from django.db.models.functions import Substr, Upper

from challenges.models import AZ_LETTERS, CHALLENGE_TYPE_AZ, HATCH_THRESHOLD
from trophies.models import (
    Concept,
    Contract,
    EarnedContract,
    EarnedTrophy,
    IGDBMatch,
    ProfileGame,
    Trophy,
)


def live_contracts():
    """Every contract a hunter could be offered, before any slot or hunter narrows it.

    One line, and it exists so `is_live=True` is written once. The contract-first search needs the same
    floor `_shape` starts from but none of its slot shape -- a search spans the whole catalogue and then
    asks `fitting_keys` which slots each result suits. Without this the search would have respelled the
    published check, which is the flag most likely to grow a second condition (a region gate, a staged
    rollout) and least likely to have it applied in two places at once.
    """
    return Contract.objects.filter(is_live=True)


def _shape(challenge, key):
    """Live contracts of the right SHAPE for the `key` slot. The one definition, used by everything."""
    pool = live_contracts()
    if challenge.challenge_type == CHALLENGE_TYPE_AZ:
        # `istartswith` on an unindexed 255-char column. Deliberate at this catalogue size: a scan of a
        # few thousand rows is sub-millisecond, and the index that would serve it is a FUNCTIONAL one on
        # `UPPER(name)` (a `varchar_pattern_ops` index cannot serve the case-insensitive form). Worth
        # adding if the pool reaches five figures; not before.
        #
        # A contract whose name starts with a DIGIT matches no letter prefix, so such contracts are
        # unusable for A-Z. Not a special case anywhere in the code -- a consequence of prefix matching
        # that nothing has to handle, but that somebody auditing the pool should know about.
        return pool.filter(name__istartswith=key)
    # Exactly one job row can match one slug, so this cannot duplicate a contract.
    return pool.filter(jobs__slug=key)


def fits_slot(challenge, key, contract):
    """Could `contract` go in the `key` slot at all, ignoring who is asking?

    Delegates to `_shape` rather than re-expressing the rule in Python, which is the fix for the
    case-folding divergence the module docstring describes. Costs one indexed query and buys the
    guarantee that the gate and the pool can never disagree about a contract.
    """
    return _shape(challenge, key).filter(pk=contract.pk).exists()


def fitting_keys(challenge, contract):
    """Which of this run's slot keys could `contract` go in? The contract-first picker's question.

    `fits_slot` answers the SLOT-FIRST question ("does this contract fit key K?") in one query, so the
    obvious way to answer this one is to call it for every key -- 26 queries to fill in one square. This
    answers it in one, and the two shapes are different QUESTIONS rather than a second copy of the rule:

    - **A-Z**: a contract can only ever fit ONE letter, the first character of its name. So the answer is
      that character, uppercased -- and it is uppercased BY POSTGRES, using the same `UPPER` the pool's
      `istartswith` compiles to.

      THE REASON IS DRIFT, NOT A KNOWN DISAGREEMENT, and the distinction matters because the first draft
      of this docstring claimed the latter. It said a Python `name[0].upper()` "would re-introduce the
      divergence" this module records, on the theory that Python does full Unicode case mapping where
      libc's fold is per-character. That theory is real but it does not reach this comparison, and the
      measurement says so: probed against this Postgres, `\N{LATIN SMALL LETTER LONG S}` and dotless
      `\N{LATIN SMALL LETTER DOTLESS I}` fold to S and I in BOTH, and every character where Python
      yields two letters (fi and fl ligatures, sharp s, the st ligature) is left alone by Postgres -- so
      both answers are "not a single A-Z letter" and `istartswith` agrees. There is no case in the live
      catalogue, or in any case I could construct, where the two differ.

      What is still true is narrower and sufficient: that agreement is a property of today's Python and
      today's Postgres collation, not a guarantee either owes us. Asking Postgres means this function and
      the pool CANNOT drift apart, whatever an ICU upgrade does to folding, for the cost of one query.
      The property test below asserts the equivalence over exactly those awkward characters, so a future
      divergence fails a test instead of silently offering a square that `assign` refuses.

      A name starting with a digit or a non-ASCII letter (`Okami` with a macron is the live example)
      yields a key no A-Z run has, so it fits nothing -- which falls out of this rather than needing a
      branch, exactly as it does in `_shape`.

    - **jobs**: a contract fits every job it carries, up to six. One query through the M2M.

    Returns a set of keys, unfiltered by whether those slots are EMPTY or already used -- the caller
    knows its own slots and does not need a query to ask about them. `challenge_service.assign` remains
    the only authority on whether a placement is allowed; this decides what to OFFER.

    Pinned against `fits_slot` by a property test over the whole catalogue, which is what makes "two
    questions, one rule" a fact rather than an intention.
    """
    return fitting_keys_for(challenge, [contract]).get(contract.id, set())


def fitting_keys_for(challenge, contracts):
    """{contract_id: set of keys} for a whole page, in ONE query. The form the picker actually needs.

    THE SINGLE FORM WAS THE MISTAKE, and it is worth recording which way round. `fitting_keys` exists
    because mapping `fits_slot` over 26 keys is 26 queries -- and then the contract-first panel called
    `fitting_keys` once per search result, which is 24 queries for a page: the same N+1, one level up,
    introduced by the fix for it. Bulk is the real shape; one contract is the special case.

    A-Z is one annotated read over the whole page. `jobs` is one read of the M2M, which yields a row per
    (contract, job) pair and a single row with a NULL slug for a contract carrying no jobs -- dropped
    below, because "no jobs" means "fits no job slot".

    `live_contracts()` is the floor in both branches, so an unpublished contract maps to an empty set
    rather than being absent -- callers can then treat "not in the mapping" and "fits nothing" alike.
    """
    ids = [c.id for c in contracts]
    if not ids:
        return {}

    if challenge.challenge_type == CHALLENGE_TYPE_AZ:
        letters = set(AZ_LETTERS)
        initials = dict(
            live_contracts()
            .filter(pk__in=ids)
            .annotate(initial=Upper(Substr('name', 1, 1)))
            .values_list('pk', 'initial')
        )
        return {cid: ({initials[cid]} if initials.get(cid) in letters else set()) for cid in ids}

    out = {cid: set() for cid in ids}
    for contract_id, slug in (live_contracts()
                              .filter(pk__in=ids)
                              .values_list('pk', 'jobs__slug')):
        if slug is not None:
            out[contract_id].add(slug)
    return out


def _slot_pool(challenge, key):
    """`_shape`, minus contracts already placed in the run's OTHER squares.

    `exclude(key=key)` is the load-bearing half: see the module docstring. Without it a reassignment sees
    a pool one smaller than an empty slot would, and the hatch opens early.
    """
    # THE LIVE FK for the same reason `challenge_service.assign`'s duplicate guard uses it: a staff slug
    # edit leaves the snapshot stale, and a pool that stops excluding an already-placed game offers it
    # again. `contract_id__isnull=False` rather than `exclude(contract_slug='')` so the two agree exactly
    # -- a slot whose contract row was deleted has a slug and no FK, and it cannot be re-offered anyway
    # because `assign` requires a live contract.
    used = (
        challenge.slots
        .filter(contract_id__isnull=False)
        .exclude(key=key)
        .values_list('contract_id', flat=True)
    )
    return _shape(challenge, key).exclude(pk__in=used)


def eligible_contracts(profile, challenge, key):
    """The contracts this hunter could still COMPLETE for this slot -- the picker's pool.

    THE ONLY EXCLUSIONS ARE THE TWO THAT HAVE TO BE. The old system refused any game a hunter was already
    50% through and expanded that refusal across Concept and GameFamily siblings; that rule is gone
    (owner's call, 2026-09-26). What remains: a contract already in another square of this run, and a
    contract this hunter has already completed -- the latter because a slot filled with one would land
    complete instantly and the run would finish itself. `hatch_is_open` is what lifts the second when
    supply is too thin to respect it.
    """
    return _slot_pool(challenge, key).filter(~Exists(_completed_by(profile)))


def completed_contracts(profile, challenge, key):
    """The already-completed contracts `eligible_contracts` refuses -- what the hatch offers instead.

    The same pool with the opposite final predicate, which is why both are three lines over a shared
    `_slot_pool` rather than one function with a boolean. Two callers wanting disjoint sets is exactly the
    shape a flag turns into a shared bug, and duplicating the pool is how the self-exclusion fix would
    have had to be made twice.
    """
    return _slot_pool(challenge, key).filter(Exists(_completed_by(profile)))


def completed_contract_ids(profile, contracts):
    """Which of `contracts` this hunter has already completed, as a set of ids, in one query.

    THE SAME FACT AS `_completed_by`, IN THE OTHER FORM. That one is a correlated subquery, which is what
    a pool query needs so the filter happens in the database over an unbounded set. This one answers the
    same question about a BOUNDED list already in memory -- the picker's page of results -- where an
    `__in` lookup is one indexed query and an annotation would mean re-running the pool.

    Two forms of one fact rather than two rules, and the fact is `EarnedContract` existing IS completion:
    the row appears only once a tier is reached and either tier counts, because platinum is not required.
    Neither form contains completion logic that could drift from the contract engine's.
    """
    ids = [c.id for c in contracts]
    if not ids:
        return set()
    return set(
        EarnedContract.objects
        .filter(profile=profile, contract_id__in=ids)
        .values_list('contract_id', flat=True)
    )


def _completed_by(profile):
    """Correlated subquery: has this hunter completed the outer contract?

    `EarnedContract` existing IS completion, and that is exact rather than convenient: the row is only
    created once a tier is reached (`mark_contract_reached` returns before creating it otherwise), and
    either tier counts because platinum is not required. So there is no completion logic here to drift
    from the contract engine's.
    """
    return EarnedContract.objects.filter(profile=profile, contract=OuterRef('pk'))


def hatch_is_open(profile, challenge, key):
    """Is this slot's pool down to `HATCH_THRESHOLD` or fewer, lifting the completed-contract rule?

    A COUNT in the database, asked about ONE slot: by `challenge_service.assign` and by the picker's
    catch-up block, which reaches it through `catchup_offers`. Never mapped over a whole run for a page
    render -- that would be 26 counts per view, and the page does not need to know.
    """
    return eligible_contracts(profile, challenge, key).count() <= HATCH_THRESHOLD


# ── the importer's date ───────────────────────────────────────────────────────────────────────────

def completion_dates(profile, contracts):
    """{contract_id: datetime} of when this hunter FIRST completed each of `contracts`.

    FIVE bounded queries however many contracts are passed, because a slot picker shows a page of
    candidates and needs to mark which are importable -- per-contract would be twenty round trips that
    each look cheap. Two to resolve membership, one catalogue-bounded trophy lookup, two aggregates.

    THE EARLIEST qualifying moment, not the latest, and that is the strict reading on purpose. A hunter
    who platted in 2019 and cleaned up DLC in 2024 completed the game in 2019; taking the later date would
    let one recent DLC trophy re-date a pre-join platinum into importable territory.

    Two sources, one per tier `contract_service._detect_tiers` reads:

    - the platinum trophy's own `earned_date_time`, which is exact; and
    - for a 100% completion, `ProfileGame.most_recent_trophy_date`. That is the completion moment
      precisely BECAUSE the row is at `progress=100`: everything defined on the game is earned, so the
      newest trophy is the one that finished it. If DLC lands later, `detect_dlc_and_refresh` drops
      progress below 100 and the row stops qualifying at all.

    WHERE THIS DELIBERATELY DIVERGES FROM `_detect_tiers`, stated because it is a real difference rather
    than an oversight: for an EPISODIC contract (members via `ContractBundle` rather than an igdb id)
    `_detect_tiers` requires EVERY concept in the bundle, while this takes the earliest date across ANY of
    them. So a bundle finished after the hunter joined, one of whose episodes was platted before, is dated
    pre-join and reads as un-importable. That errs toward REFUSING, which is the safe direction, and
    episodic contracts are a documented niche (`job-board-contracts.md` lists their other gaps). Taking
    the max within each bundle would be more correct, and is the fix if it ever matters.

    A contract the hunter has not completed is absent from the result rather than mapped to None.
    """
    contracts = list(contracts)
    if not contracts:
        return {}

    concepts_by_contract = member_concepts_by_contract(contracts)
    all_concept_ids = {cid for ids in concepts_by_contract.values() for cid in ids}
    if not all_concept_ids:
        return {}

    # SMALL SIDE FIRST, and this is whale-safety rather than micro-optimisation -- the rule
    # `contract_service._detect_tiers` states in capitals, and which an earlier draft of this function
    # broke. Filtering `EarnedTrophy` by `trophy__game__concept_id__in=...` lets the planner start from
    # `(profile, earned)` and apply the join as a filter, which on a 250,000-trophy hunter walks every one
    # of those rows. Resolving the platinum trophy ids first is catalogue-bounded (a handful of rows) and
    # turns the aggregate into a seek on the `(profile_id, trophy_id)` index.
    platinum_ids = list(
        Trophy.objects
        .filter(game__concept_id__in=all_concept_ids, trophy_type='platinum')
        .values_list('id', flat=True)
    )
    plat_dates = {}
    if platinum_ids:
        plat_dates = dict(
            EarnedTrophy.objects
            .filter(profile=profile, earned=True, trophy_id__in=platinum_ids,
                    earned_date_time__isnull=False)
            .values_list('trophy__game__concept_id')
            .annotate(first=Min('earned_date_time'))
            .values_list('trophy__game__concept_id', 'first')
        )

    # NOT the same small-side treatment, deliberately: `ProfileGame` is one row per GAME rather than per
    # trophy, so the profile-filtered side is already bounded by a hunter's library rather than by their
    # trophy count. `_detect_tiers` reads it exactly this way for the same reason.
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
def importable_dates(profile, contracts, joined_at):
    """{contract_id: datetime} for the importable ones -- the same set, with the dates kept.

    THE DATES WERE ALREADY COMPUTED and were being discarded. `completion_dates` costs five queries over
    trophy data, and the picker needs to SHOW the date beside each importable offer, so it asked a second
    time: ten queries where five do, on the tables the whale rule is about. What used to be a separate
    `importable_ids` is now this function's keys.
    """
    if joined_at is None:
        return {}
    return {cid: when for cid, when in completion_dates(profile, contracts).items() if when > joined_at}


def member_concepts_by_contract(contracts):
    """{contract_id: {concept_id, ...}} in two queries, however many contracts.

    Membership is DERIVED, so this applies the same rule as `contract_service.contract_by_concept_map`
    read the other way round: anchored concepts whose TRUSTED match carries the contract's raw igdb id,
    plus any concept riding along in one of its bundles.

    `Contract.member_concept_ids()` answers only the FIRST half -- its own docstring says it is "empty for
    an admin/episodic Contract whose concepts come only from its bundles" -- which is why the bundle query
    below exists rather than this being a loop over that method.
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

    # The episodic case, included because `_detect_tiers` counts them: leaving them out would report an
    # episodic contract as never completed and silently make it un-importable.
    bundle_rows = (
        Contract.objects
        .filter(id__in=[c.id for c in contracts], bundles__concepts__isnull=False)
        .values_list('id', 'bundles__concepts__id')
    )
    for contract_id, concept_id in bundle_rows:
        if concept_id is not None:
            out[contract_id].add(concept_id)

    return out
