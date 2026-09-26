"""Job drift: where a Contract's curated job profile disagrees with what its member
concepts' IGDB genres/themes detect TODAY.

The problem it measures: `Contract.jobs` is set once (staged by
`evaluate_contract_candidates`, or by the admin's "Suggest jobs" action) and never revisited,
while the IGDB data underneath it keeps moving -- `enrich_from_igdb --refresh` re-pulls
genres/themes weekly. A game whose IGDB page was thin at staging time gets `{freelancer}` and
keeps it after the page fills in. Nothing in the engine notices, and the XP already banked on
that contract stays pinned to the jobs it was paid into (`ContractXPGrant.job`), so the drift
compounds silently with every claim.

READ-ONLY, and deliberately so. This module answers "how far has the catalogue drifted, and
what XP is riding on it"; it does not repair anything. Re-pointing banked grants is a
subtractive operation on a hunter's levels and belongs behind the same staff-triggered,
preview-by-default discipline as `reconcile_contracts` -- see
docs/design/rebuild/job-board-contracts.md.

THE CENTRAL CAVEAT, and the reason this is a report before it is a pipeline: a disagreement is
NOT automatically an error. Curation deliberately trims IGDB's peripheral tags (a "6-job"
auto-detection is usually 2 real jobs and 4 incidental ones), so a contract holding FEWER jobs
than detection suggests is equally likely to be good curation or stale data, and nothing stored
distinguishes them. Only `freelancer_repair` is unambiguous. The buckets below exist to keep
those cases apart rather than reporting one undifferentiated "drifted" count.

Shared by the report (`report_job_drift`) and any future evaluator, following the
`CatalogScanner` precedent in contract_candidates.py: the rule lives in one place so a pipeline
built on it cannot classify differently from the report that sized it.
"""
from django.db.models import Count, Sum

from trophies.models import (
    Concept, Contract, ContractXPGrant, EarnedContract, IGDBMatch,
)
from trophies.services.job_detection import (
    COMBO_OVERRIDES, FALLBACK_SLUG, assign_job_slugs, flatten_tags, pool_tags, top_jobs,
)

# --- buckets ---------------------------------------------------------------

ALIGNED = 'aligned'
UNJOBBED = 'unjobbed'
NO_SIGNAL = 'no_signal'
FREELANCER_REPAIR = 'freelancer_repair'
FREELANCER_REGRESSION = 'freelancer_regression'
COMBO_UPGRADE = 'combo_upgrade'
COMBO_DOWNGRADE = 'combo_downgrade'
NARROWER = 'narrower'
WIDER = 'wider'
MIXED = 'mixed'

#: Display order, most actionable first. `aligned` leads because it is the denominator every
#: other line is read against.
BUCKETS = [
    ALIGNED, FREELANCER_REPAIR, COMBO_UPGRADE, NARROWER, WIDER, MIXED,
    COMBO_DOWNGRADE, FREELANCER_REGRESSION, UNJOBBED, NO_SIGNAL,
]

#: Description ONLY -- the bucket key is rendered alongside it by the caller, so a label that
#: repeated the name would print it twice.
BUCKET_LABELS = {
    ALIGNED: 'jobs match detection, nothing to do',
    FREELANCER_REPAIR: 'holds only Freelancer, detection now has real jobs',
    COMBO_UPGRADE: 'a base job became its combo (IGDB gained the paired theme)',
    NARROWER: 'holds FEWER jobs than detected (staff trim OR new IGDB tags)',
    WIDER: 'holds jobs detection no longer suggests (staff pick OR IGDB lost tags)',
    MIXED: 'jobs both added and removed, no single explanation',
    COMBO_DOWNGRADE: 'a combo fell back to its base job (IGDB lost the theme)',
    FREELANCER_REGRESSION: 'detection collapsed to Freelancer alone',
    UNJOBBED: 'NO jobs set: this contract banks ZERO XP on claim',
    NO_SIGNAL: 'no member concepts or no IGDB enrichment; cannot be judged',
}

#: Buckets that are NOT drift: the two agree, or the comparison is meaningless. Drives the
#: drifted count and which sections the report prints by default.
SETTLED = frozenset({ALIGNED, NO_SIGNAL})

#: Buckets whose XP exposure is never measured. ALIGNED alone, and only because there is
#: nothing to do about it -- it is also the bulk of the catalogue, so skipping it keeps the
#: stakes query small. NO_SIGNAL is deliberately NOT here despite being settled: a contract
#: hunters are actively claiming whose IGDB data has vanished is worth seeing costed, and a
#: `--bucket no_signal` drill-down that reported "no earners" for every row would be stating a
#: fact nobody measured.
UNCOSTED = frozenset({ALIGNED})


def _combo_shift(added, removed):
    """COMBO_UPGRADE / COMBO_DOWNGRADE when the whole difference is one combo override
    flipping, else None.

    Requires the correspondence to be EXACT in both directions -- every addition is a combo
    and its bases are exactly the removals (or the mirror). An unrelated job riding along with
    the flip falls through to `mixed` rather than being hidden inside a bucket whose label
    claims the change is fully explained.
    """
    if not added or not removed:
        return None
    if all(slug in COMBO_OVERRIDES for slug in added):
        if {COMBO_OVERRIDES[slug] for slug in added} == removed:
            return COMBO_UPGRADE
    if all(slug in COMBO_OVERRIDES for slug in removed):
        if {COMBO_OVERRIDES[slug] for slug in removed} == added:
            return COMBO_DOWNGRADE
    return None


def classify(current, suggested, *, has_signal=True):
    """(bucket, added, removed) for one contract's current vs detected job slugs.

    PURE -- no queries, no model access -- so the buckets can be tested exhaustively without a
    database and a future evaluator can reuse the rule without the scanner.

    `added` = detected but not held; `removed` = held but no longer detected. Both are always
    returned (empty sets for the settled buckets) so callers never re-derive them.

    `has_signal=False` means IGDB told us NOTHING about this game -- no member concepts, or
    members carrying no genres and no themes. That case cannot be read off `suggested` alone,
    because `assign_job_slugs` answers a tagless game with `{freelancer}`: the fallback is
    indistinguishable, in its output, from a real detection saying "this game is
    unspecialized". Treating the two alike would have filled `freelancer_regression` with
    contracts whose enrichment simply has not run, which is the opposite of a finding.
    """
    current, suggested = set(current), set(suggested)
    added, removed = suggested - current, current - suggested

    if not current:
        # Checked before ALIGNED: an unjobbed contract whose detection is also empty is still
        # unjobbed, and that is the fact worth reporting -- `accept_contracts_bulk` skips a
        # contract with no jobs entirely, so it sits on the board paying nothing.
        return UNJOBBED, added, removed
    if not suggested or not has_signal:
        return NO_SIGNAL, added, removed
    if not added and not removed:
        return ALIGNED, added, removed
    if current == {FALLBACK_SLUG}:
        return FREELANCER_REPAIR, added, removed
    if suggested == {FALLBACK_SLUG}:
        return FREELANCER_REGRESSION, added, removed
    combo = _combo_shift(added, removed)
    if combo:
        return combo, added, removed
    if not removed:
        return NARROWER, added, removed
    if not added:
        return WIDER, added, removed
    return MIXED, added, removed


# --- the scan --------------------------------------------------------------

class JobDriftScanner:
    """One batched pass over the Contract catalogue, classifying each contract's drift.

    BOUNDED BY CONTRACTS, not by concepts and never by profiles. The per-contract helper
    `job_detection.suggest_jobs_for_contract` costs ~5 queries EACH (members, bundles, bundle
    concepts, genres, themes), which is a query storm at catalogue scale; this pays a FIXED
    seven regardless of how many contracts there are -- the contract rows, three prefetches
    (`jobs`, `bundles`, `bundles__concepts`), the member concepts, and the two tag lookups in
    `job_detection.pool_tags` -- then does the matching in Python, exactly as
    `simulate_stage_jobs` already does for badge stages. The id sets it holds are
    catalogue-sized, the shape `audit_job_board_coverage` established for staff audits.

    It must agree with `suggest_jobs_for_contract` on every contract or the report is fiction,
    so that equivalence is pinned by a test rather than by reading the two implementations.
    """

    def __init__(self, *, live_only=False):
        self.contracts = list(
            (Contract.objects.filter(is_live=True) if live_only else Contract.objects.all())
            .prefetch_related('jobs', 'bundles__concepts')
            .order_by('pk')
        )
        self._concepts_by_contract = self._resolve_concepts()
        # `set().union()` with no arguments is legal and returns set(), so the empty catalogue
        # needs no guard of its own.
        concept_ids = set().union(*self._concepts_by_contract.values())
        self._genres, self._themes = pool_tags(concept_ids)

    def _resolve_concepts(self):
        """contract_id -> set of concept ids, unioning igdb-derived members with bundle
        concepts. Mirrors `suggest_jobs_for_contract`, which pools both."""
        igdb_ids = {c.igdb_id for c in self.contracts if c.igdb_id is not None}
        members = {}
        if igdb_ids:
            rows = (
                Concept.objects
                .filter(anchor_migration_completed_at__isnull=False,
                        igdb_match__igdb_id__in=igdb_ids,
                        igdb_match__status__in=IGDBMatch.TRUSTED_STATUSES)
                .values_list('igdb_match__igdb_id', 'id')
            )
            for igdb_id, concept_id in rows:
                members.setdefault(igdb_id, set()).add(concept_id)

        by_contract = {}
        for contract in self.contracts:
            ids = set(members.get(contract.igdb_id, ())) if contract.igdb_id is not None else set()
            for bundle in contract.bundles.all():          # prefetched
                ids.update(c.id for c in bundle.concepts.all())
            by_contract[contract.pk] = ids
        return by_contract

    def suggested_for(self, contract):
        """(suggested slugs, has_signal) for `contract` as detection sees it right now.

        The slug LIST is IDENTICAL to what `job_detection.suggest_jobs_for_contract` returns for
        the same contract -- same pooling, same `top_jobs` cap and ordering, same `{freelancer}`
        fallback for a tagless game. That equivalence is the report's whole claim to accuracy and
        is pinned by `test_job_drift.py`, so resist "improving" the answer here: a divergence
        would make the report describe a rule the admin action and the staging pipeline do not
        follow. Returned as a LIST rather than a set so `top_jobs`' strongest-first ordering
        survives to the report, which tells the reader which detections are load-bearing.

        `has_signal` is the extra fact the per-contract helper cannot express in its return
        value -- whether IGDB supplied any genre or theme at all. See `classify`.
        """
        concept_ids = self._concepts_by_contract.get(contract.pk, set())
        if not concept_ids:
            return [], False
        genres, themes = flatten_tags(concept_ids, self._genres, self._themes)
        return top_jobs(assign_job_slugs(genres, themes)), bool(genres or themes)

    def rows(self):
        """One dict per contract: its bucket, the job sets, and the diff. No XP figures --
        those are attached in a second pass over the costed subset (`attach_stakes`, everything
        outside `UNCOSTED`).

        `suggested` keeps `top_jobs`' strongest-first order; `current` is sorted, because a
        contract's `jobs` M2M carries no meaningful order to preserve."""
        out = []
        for contract in self.contracts:
            current = {job.slug for job in contract.jobs.all()}     # prefetched
            suggested, has_signal = self.suggested_for(contract)
            bucket, added, removed = classify(current, suggested, has_signal=has_signal)
            out.append({
                'contract_id': contract.pk,
                'slug': contract.slug,
                'name': contract.name,
                'igdb_id': contract.igdb_id,
                'is_live': contract.is_live,
                'bucket': bucket,
                'current': sorted(current),
                'suggested': list(suggested),     # strongest first, from top_jobs
                'added': sorted(added),
                'removed': sorted(removed),
                'banked_xp': 0,
                'banked_hunters': 0,
                'pending_hunters': 0,
            })
        return out


def attach_stakes(rows):
    """Merge each row's XP exposure in place: banked XP, hunters holding it, and hunters who
    have REACHED the contract without claiming.

    Two grouped queries for the whole set, never per row, and the aggregation happens in
    Postgres -- these counts are per-profile data and a Python tally over them is the whale-OOM
    shape the project bans outright.

    The pending count is not decoration. A reached-but-unaccepted contract re-splits for FREE:
    no grants exist yet, so a job change costs nothing and the hunter simply claims under the
    new profile. Separating the two says how much of the drift is a data edit and how much is a
    migration of banked levels.

    Scoped to the rows handed in, so callers pass only what is worth costing (`UNCOSTED`) and
    the aligned majority never reaches the query.
    """
    ids = [r['contract_id'] for r in rows]
    if not ids:
        return rows

    banked = {
        r['earned_contract__contract_id']: (r['xp'], r['hunters'])
        for r in ContractXPGrant.objects
        .filter(earned_contract__contract_id__in=ids)
        .values('earned_contract__contract_id')
        .annotate(xp=Sum('amount'), hunters=Count('profile_id', distinct=True))
    }
    # Counted separately rather than as a filtered annotation on the query above: joining
    # grants multiplies EarnedContract rows (one per job x tier), so a single query would have
    # to count DISTINCT on both halves to stay honest. Two clean aggregates over an indexed
    # column are cheaper to run and far cheaper to read.
    earners = dict(
        EarnedContract.objects.filter(contract_id__in=ids)
        .values('contract_id').annotate(n=Count('id')).values_list('contract_id', 'n')
    )
    for row in rows:
        xp, hunters = banked.get(row['contract_id'], (0, 0))
        row['banked_xp'] = xp or 0
        row['banked_hunters'] = hunters or 0
        # Never negative, though NOT for the reason it first looks: `earned_contract` is
        # nullable (quest/event/manual grants leave it unset). What guarantees it is that
        # `filter(earned_contract__contract_id__in=...)` is an INNER JOIN, so null-FK grants are
        # excluded outright, and the FK is CASCADE, so a grant cannot outlive its EarnedContract.
        # The banked count is therefore always a subset of the earners. `max` is belt-and-braces.
        row['pending_hunters'] = max(earners.get(row['contract_id'], 0) - row['banked_hunters'], 0)
    return rows


def scan(*, live_only=False, with_stakes=True):
    """The whole report input: classified rows, XP attached to the drifted ones.

    The single entry point both the command and any future evaluator should call, so neither
    can reach a different verdict from the other.
    """
    scanner = JobDriftScanner(live_only=live_only)
    rows = scanner.rows()
    if with_stakes:
        attach_stakes([r for r in rows if r['bucket'] not in UNCOSTED])
    return rows
