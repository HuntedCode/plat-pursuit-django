"""What the picker offers, for one slot or for one search. Read-only: nothing here writes.

TWO PANELS, ONE COMPONENT, because a hunter arrives with one of two questions and neither is a subset of
the other:

- **slot-first** (`slot_panel`): "this square is empty -- what can I put in it?" The natural flow for A-Z,
  where you are filling alphabet gaps.
- **contract-first** (`search_panel`): "I just finished this game -- where does it go?" The natural flow
  for Job Coverage, where one game carries up to six jobs and the answer is genuinely unknown to the
  hunter. For A-Z it is nearly trivial (a game fits exactly one letter), which is why the two flows earn
  their keep rather than duplicating each other.

NOTHING HERE DECIDES ANYTHING. Every rule comes from `eligibility` or `challenge_service`:
`eligible_contracts` for the pool, `fitting_keys` for which slots a game suits, `catchup_reasons` for
whether an already-completed game is allowed and under WHICH label. This module chooses what to SHOW and
in what order. `challenge_service.assign` remains the only authority on whether a placement is permitted,
and it re-checks everything -- so a stale panel can only ever produce a refusal, never a bad write.

BOUNDED, DELIBERATELY. Prod holds ~2,390 live contracts and grows ~150/day with ~4,000 queued, so a
single letter's pool already runs to the hundreds and will reach four figures. Every list here is a
`PAGE`-sized slice with a DB `COUNT` beside it, and the expensive per-hunter work (`completion_dates`,
five queries) touches only the slice -- never the pool. Ordering is `Lower('name')` in the database, per
the project's front-facing-name rule.
"""
from django.db.models.functions import Lower

from challenges.models import CHALLENGE_TYPE_AZ, COMPLETED_VIA_IMPORT
from challenges.services import challenge_service as svc
from challenges.services import eligibility
from challenges.services.slot_render import covers_by_contract
from trophies.models import Job
from trophies.services.job_render import job_atom

#: One page of offers. 24 rather than a round 20 or 25 because it divides by 2, 3 and 4, so the grid it
#: feeds has no ragged last row at any of the picker's column counts.
PAGE = 24

#: A search term shorter than this matches most of the catalogue, so it is treated as no term at all --
#: the same floor `gamelists.services.game_search` applies, and for the same reason: an unbounded
#: `icontains` on an unindexed column is a sequential scan whatever the term, but a one-character term
#: also returns nothing a hunter can use.
MIN_QUERY = 2

#: THE ONE SORT, and the `pk` is not decoration. `Lower('name')` is the project's rule for front-facing
#: names, but on its own it is not a total order: two contracts whose names differ only in case compare
#: EQUAL under it, and Postgres is then free to return them in any order -- so a paginated slice could show
#: a row twice or never. The same argument `covers.sort_key` makes for its own pk tiebreak.
#:
#: WHAT `Lower()` IS AND IS NOT FOR, measured rather than assumed. This database collates `en_US.utf8`,
#: whose primary comparison already ignores case, so `order_by('name')` and `order_by(Lower('name'))`
#: agree on ordinary names -- an earlier comment here claimed raw ordering would put 'Bzzt' before
#: 'brothers', which is `C`-collation behaviour and false here. `Lower()` earns its place by making the
#: order independent of whatever collation the database happens to have, which is not something a dev box
#: can verify for prod.
BY_NAME = (Lower('name'), 'pk')


def slot_panel(profile, challenge, key, *, query='', limit=PAGE):
    """Everything the slot-first panel draws for the `key` square.

    QUERY COST, as the range it is rather than its best case. Seven for the common path: the slot, the
    pool count, the pool slice, three for the slice's covers, and one hatch COUNT. It rises in exactly two
    circumstances, both of which are the point of the feature rather than accidents:

    - the HATCH being open adds a slice of already-completed contracts plus its covers;
    - the IMPORTER being open adds `importer_is_available` (one) and, if that slice is non-empty,
      `importable_ids` -> `completion_dates`, which is FIVE queries reading trophy data.

    So a first-run hunter opening a thin slot pays around fifteen, and a hunter with a deep pool and a
    spent importer pays seven. None of it scales with the POOL, only with `limit`.
    """
    slot = challenge.slots.filter(key=key).first()
    if slot is None:
        return None

    pool = eligibility.eligible_contracts(profile, challenge, key)
    pool = _narrow(pool, query)
    total = pool.count()
    rows = list(pool.order_by(*BY_NAME)[:limit])

    catchup = _catchup_offers(profile, challenge, key, query=query, limit=limit)

    covers = covers_by_contract(rows + [c for c, _ in catchup])
    atom = _atom_for(challenge, key)

    return {
        'key': key,
        'label': atom['name'] if atom else key,
        'job': atom,
        'query': query,
        'slot_is_filled': slot.is_filled,
        'slot_is_completed': slot.is_completed,
        'current_name': slot.contract_name,
        'total': total,
        'showing': len(rows),
        'rows': [_row(contract, covers) for contract in rows],
        # Already-completed games a rule lifts. Kept as their own list rather than mixed into `rows` with
        # a flag: they are the only offers that land a square COMPLETE and therefore LOCKED, so the
        # warning and the confirmation belong to one block of the panel rather than to scattered rows.
        'catchup': [_catchup_row(contract, via, covers, dates) for contract, via, dates in
                    _with_dates(profile, catchup)],
    }


def search_panel(profile, challenge, query, *, limit=PAGE):
    """Everything the contract-first panel draws for a search term.

    Returns empty-handed for a term under `MIN_QUERY` rather than running the scan.

    THE SLOTS COME FROM MEMORY. `fitting_keys_for` says which keys a game suits at all; which of those
    are actually OPEN is a question about this run's own slots, which are already loaded -- so the
    intersection costs nothing and no query asks it. A completed square is excluded (it never reopens); a
    filled but unfinished one is offered, because reassigning it is allowed.

    SEVEN QUERIES, flat in the result count: the run's slots, the search COUNT, the search slice, three
    for the slice's covers, one for which of them this hunter has completed, one for the fitting keys, and
    one for the job catalogue on a jobs run. An earlier version called `fitting_keys` per row, which made
    it 24 more.
    """
    query = (query or '').strip()
    if len(query) < MIN_QUERY:
        return {'query': query, 'total': 0, 'showing': 0, 'rows': [], 'too_short': True}

    slots = list(challenge.slots.all())
    open_keys = {s.key for s in slots if not s.is_completed}
    used_slugs = {s.contract_slug for s in slots if s.contract_slug}

    found = _narrow(eligibility.live_contracts(), query)
    total = found.count()
    # NO `prefetch_related('jobs')`. It was here and it could not have worked: `fitting_keys` read the
    # jobs with `values_list`, which issues a fresh query even on a prefetched manager (the prefetch
    # caches objects, not arbitrary querysets). The bulk call below asks once for the whole page instead.
    rows = list(found.order_by(*BY_NAME)[:limit])

    covers = covers_by_contract(rows)
    completed = eligibility.completed_contract_ids(profile, rows)
    keys_by_contract = eligibility.fitting_keys_for(challenge, rows)
    labels = _job_names(challenge)

    out = []
    for contract in rows:
        keys = keys_by_contract.get(contract.id, set()) & open_keys
        already_here = contract.slug in used_slugs
        out.append({
            **_row(contract, covers),
            # Every key it could go in, named for display. A jobs game routinely offers several.
            'keys': sorted(keys, key=lambda k: labels.get(k, k)),
            'key_labels': {k: labels.get(k, k) for k in keys},
            # WHY it cannot be placed, when it cannot -- so the row explains itself instead of just
            # rendering without a button.
            'already_in_run': already_here,
            'is_completed_by_you': contract.id in completed,
        })

    return {'query': query, 'total': total, 'showing': len(rows), 'rows': out, 'too_short': False}


# ── internals ─────────────────────────────────────────────────────────────────────────────────────

def _narrow(queryset, query):
    """Apply a search term, or don't. `icontains` on an unindexed column, knowingly -- see the module
    docstring of `gamelists.services.game_search`, which measured the same thing and recorded that no
    expression index on `UPPER(name)` exists to serve it."""
    query = (query or '').strip()
    if len(query) < MIN_QUERY:
        return queryset
    return queryset.filter(name__icontains=query)


def _catchup_offers(profile, challenge, key, *, query, limit):
    """[(contract, via)] for the already-completed games a rule currently lifts.

    Asks `catchup_reasons` for the labels rather than deciding them, so the panel and `assign` cannot
    disagree about whether a square will be stamped `import` or `hatch`. A contract neither rule lifts is
    dropped rather than shown greyed out: it is not an offer, and a list of things you cannot pick is how
    a panel becomes a puzzle.
    """
    pool = _narrow(eligibility.completed_contracts(profile, challenge, key), query)
    candidates = list(pool.order_by(*BY_NAME)[:limit])
    if not candidates:
        return []

    reasons = svc.catchup_reasons(profile, challenge, key, candidates)
    return [(c, reasons[c.id]) for c in candidates if c.id in reasons]


def _with_dates(profile, catchup):
    """Attach completion dates, but only for the rows that will show one.

    `completion_dates` is five queries over trophy data, so it runs only when an `import` row exists --
    the hatch does not display a date, and paying for one would be five queries for nothing on every thin
    slot a hunter opens after their first run.
    """
    if not catchup:
        return []
    importable = [c for c, via in catchup if via == COMPLETED_VIA_IMPORT]
    dates = eligibility.completion_dates(profile, importable) if importable else {}
    return [(c, via, dates) for c, via in catchup]


def _row(contract, covers):
    return {
        'slug': contract.slug,
        'name': contract.name,
        'cover': covers.get(contract.id),
    }


def _catchup_row(contract, via, covers, dates):
    row = _row(contract, covers)
    row['via'] = via
    # Only an `import` row has a date to show, and it is the date the WORK happened (read from trophy
    # data), never an `EarnedContract` detection stamp -- those record when we noticed, not when they did.
    row['completed_at'] = dates.get(contract.id) if via == COMPLETED_VIA_IMPORT else None
    return row


def _atom_for(challenge, key):
    """The job atom for a jobs slot, None for an A-Z letter. One query, and only for jobs."""
    if challenge.challenge_type == CHALLENGE_TYPE_AZ:
        return None
    job = Job.objects.filter(slug=key).first()
    return job_atom(job) if job else None


def _job_names(challenge):
    """{slug: name} for a jobs run, empty for A-Z. One query for the 25-row catalogue."""
    if challenge.challenge_type == CHALLENGE_TYPE_AZ:
        return {}
    return dict(Job.objects.values_list('slug', 'name'))
