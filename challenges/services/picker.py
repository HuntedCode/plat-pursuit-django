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
`eligible_contracts` for the pool, `fitting_keys` for which slots a game suits, `catchup_offers` for
whether an already-completed game is allowed, under WHICH label, and when it was earned. This module chooses what to SHOW and
in what order. `challenge_service.assign` remains the only authority on whether a placement is permitted,
and it re-checks everything -- so a stale panel can only ever produce a refusal, never a bad write.

BOUNDED, DELIBERATELY. Prod holds ~2,390 live contracts and grows ~150/day with ~4,000 queued, so a
single letter's pool already runs to the hundreds and will reach four figures. Every list here is a
`PAGE`-sized slice with a DB `COUNT` beside it, and the expensive per-hunter work (`completion_dates`, 3-5
queries) touches only the slice -- never the pool. Ordering is `Lower('name')` in the database, per
the project's front-facing-name rule.
"""
from django.db.models.functions import Lower

from challenges.models import CHALLENGE_TYPE_AZ
from challenges.services import challenge_service as svc
from challenges.services import eligibility
from challenges.services.slot_render import covers_by_contract, label_for_key
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

#: A bound on the term, not on a title. `Contract.name` is a 255-char column, so 120 does not guarantee it
#: only ever truncates a non-search -- an earlier comment here asserted "the longest live one is comfortably
#: under 100 characters", which is a measurement of production nothing in this branch made.
#:
#: What makes it safe is the direction of the error: truncating an `icontains` term can only WIDEN the match
#: set, so even a genuinely 200-character title still matches its own 120-character prefix. Nobody loses a
#: result; a paste of a paragraph stops being a scan for a paragraph.
MAX_QUERY = 120

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


def slot_panel(profile, challenge, key, *, query='', limit=PAGE, slot=None):
    """Everything the slot-first panel draws for the `key` square.

    QUERY COST, counted again after the last two changes invalidated the previous figure. SIX on the
    common path, inside this function: the pool COUNT, the pool slice, the completed-candidates slice, and
    three for the covers. The caller adds two (the run and the slot), and a jobs run adds one for its atom.

    The previous version of this paragraph said seven and named two items that are no longer right: "the
    slot", which moved out to the caller, and "one hatch COUNT", which does not run on the common path at
    all -- `hatch_is_open` is only reached when the candidate slice is non-empty. The seventh query it was
    actually counting was that candidate slice, which runs unconditionally.

    It rises with completed candidates present, which is the feature working:
    - `importer_is_available` (one) is paid whether the importer is open OR spent -- it is the query that
      ANSWERS that, so a spent importer does not save it;
    - an open importer adds `completion_dates`, which is 3-5 queries (member concepts, then `Trophy`, then
      `EarnedTrophy` only if a platinum exists, then `ProfileGame`) -- not "five over trophy data", which
      overstated both the count and how many of them touch trophy tables;
    - `hatch_is_open` adds one COUNT.

    So a first-run hunter on a thin slot pays 13-15 here, and a hunter with no completed candidates pays
    six whatever their importer state. None of it scales with the POOL, only with `limit`.
    """
    # THE VIEW ALREADY HAS IT. `_SlotView.resolve` fetches the slot to decide whether the key is real, and
    # this used to fetch the same row again -- one wasted query on every panel open. Passing it in keeps the
    # 404 decision with the view (which must make it before doing any work) and the rendering here.
    if slot is None:
        slot = challenge.slots.filter(key=key).first()
    if slot is None:
        return None
    # A MISMATCHED SLOT WOULD BE SILENT. The one caller is correct, but a second one passing the wrong
    # row gets a panel labelled `key` while gated on another square's `is_completed` and naming another
    # square's game -- so the parameter is only safe to hand around if it checks itself.
    if slot.key != key or slot.challenge_id != challenge.id:
        return None

    atom = _atom_for(challenge, key)
    # `label_for_key` rather than the raw key: a `Job` row deleted under a live run leaves the slug behind,
    # and `card-shark` reads as a content bug. The grid already degrades it to "Card Shark" through the same
    # helper, so without this the SAME square read one way on the page and another in the panel over it.
    label = atom['name'] if atom else label_for_key(key)

    # A LOCKED SQUARE ASKS FOR NOTHING. A completed square can never be reassigned (`assign` refuses it
    # first), so every query below would be spent building offers whose every click must 400 -- the pool
    # COUNT, the `icontains` slice, the catch-up block's five-query trophy read, and the covers batch. The
    # panel says why instead, and says it in one query.
    if slot.is_completed:
        return {
            'key': key, 'label': label, 'job': atom, 'query': '',
            'slot_is_filled': True, 'slot_is_completed': True,
            'current_name': slot.contract_name,
            'total': 0, 'showing': 0, 'rows': [],
            'catchup': [], 'catchup_more': False,
            'locked': True,
        }

    pool = _narrow(eligibility.eligible_contracts(profile, challenge, key), query)
    total = pool.count()
    rows = list(pool.order_by(*BY_NAME)[:limit])

    catchup, catchup_more = _catchup_offers(profile, challenge, key, query=query, limit=limit)

    covers = covers_by_contract(rows + [c for c, _, _ in catchup])

    return {
        'key': key,
        'label': label,
        'job': atom,
        'query': clean_term(query),
        'slot_is_filled': slot.is_filled,
        'slot_is_completed': False,
        'current_name': slot.contract_name,
        'total': total,
        'showing': len(rows),
        'rows': [_row(contract, covers) for contract in rows],
        # Already-completed games a rule lifts. Kept as their own list rather than mixed into `rows` with
        # a flag: they are the only offers that land a square COMPLETE and therefore LOCKED, so the
        # warning and the confirmation belong to one block of the panel rather than to scattered rows.
        'catchup': [_catchup_row(contract, via, when, covers) for contract, via, when in catchup],
        'catchup_more': catchup_more,
        'locked': False,
    }


def search_panel(profile, challenge, query, *, limit=PAGE):
    """Everything the contract-first panel draws for a search term.

    Returns empty-handed for a term under `MIN_QUERY` rather than running the scan.

    THE SLOTS COME FROM MEMORY. `fitting_keys_for` says which keys a game suits at all; which of those are
    actually OPEN, and which of those already hold something, are questions about this run's own slots --
    already loaded, so the intersection and the `filled` map both cost nothing. A completed square is excluded (it never reopens); a
    filled but unfinished one is offered, because reassigning it is allowed.

    SEVEN QUERIES, flat in the result count: the run's slots, the search COUNT, the search slice, three
    for the slice's covers, one for which of them this hunter has completed, one for the fitting keys, and
    one for the job catalogue on a jobs run. An earlier version called `fitting_keys` per row, which made
    it 24 more.
    """
    # SCRUBBED FIRST. `clean_term` strips control bytes before measuring, so `?q=%00%00` -- two characters,
    # enough to clear a length floor -- becomes the empty string here instead of reaching `icontains` and
    # raising the `DataError` that was an unhandled 500.
    query = clean_term(query)
    if not query:
        return {'query': query, 'total': 0, 'showing': 0, 'rows': [], 'too_short': True, 'filled': {}}

    slots = list(challenge.slots.all())
    open_keys = {s.key for s in slots if not s.is_completed}
    # WHAT IS ALREADY IN EACH SQUARE, so a result can warn before it replaces something. Free: the slots are
    # already in memory, and the name is the snapshot the square itself shows. Completed squares are absent
    # from `open_keys` anyway, so this only ever describes a square a hunter could still overwrite.
    filled = {s.key: s.contract_name for s in slots if s.is_filled and not s.is_completed}
    # THE LIVE FK, NOT THE FROZEN SLUG. Keyed on `contract_slug` this missed a contract whose slug staff
    # had edited since assignment -- so a Job Coverage game already in the `slayer` square reported
    # `already_in_run: False`, and `assign` (which had the same bug) accepted it into `card-shark` too.
    # One completion, two squares, two job-XP payouts: exactly what "one payout per job per challenge"
    # forbids. `challenge_service`'s own module header argues for the FK over the slug for this reason and
    # the argument had been applied to detection only.
    used_ids = {s.contract_id for s in slots if s.contract_id}

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
        already_here = contract.id in used_ids
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

    return {'query': query, 'total': total, 'showing': len(rows), 'rows': out, 'too_short': False,
            # Run-level rather than per-row: every result offers the same squares, so repeating this on
            # each row would be the same map 24 times.
            'filled': filled}


# ── internals ─────────────────────────────────────────────────────────────────────────────────────

#: Characters that must never reach a text comparison. A NUL byte is the one that matters: psycopg
#: refuses it outright ("PostgreSQL text fields cannot contain NUL (0x00) bytes"), and the `DataError` it
#: raises is caught by nothing on this path -- so `?q=%00%00` was an unhandled **500**, repeatable at the
#: read limit by any linked hunter on their own run. Two NULs cleared `MIN_QUERY` because that is a LENGTH
#: floor and says nothing about content; a single `%00` was saved only by accident.
#:
#: The rest go with it because none of them can help a search and all of them can only surprise: C0
#: controls arrive from paste accidents and fuzzers, never from somebody looking for a game.
_UNSEARCHABLE = ''.join(chr(c) for c in range(0x20)) + '\x7f'
_STRIP_UNSEARCHABLE = str.maketrans('', '', _UNSEARCHABLE)


def clean_term(query):
    """The search term, scrubbed and bounded, or `''` for anything that is not a search.

    SCRUBBED BEFORE MEASURED, which is the whole fix: doing it the other way round lets two NUL bytes
    satisfy `MIN_QUERY` and then blow up in the database. After scrubbing, `%00%00` is the empty string and
    is treated as no term at all -- the resting state of the panel, not an error to paint.

    `MAX_QUERY` because a term longer than any real title is not a search either, and truncating is kinder
    than refusing: somebody who pasted a paragraph gets results for its first words rather than a rejection.
    `gamelists.services.game_search` raises for an over-long term instead, which is right for a typeahead
    that must not silently answer a different question than the one typed; here the term is only ever a
    filter over a slot's own pool, so narrowing it is harmless.
    """
    query = (query or '').translate(_STRIP_UNSEARCHABLE).strip()
    if len(query) < MIN_QUERY:
        return ''
    return query[:MAX_QUERY]


def _narrow(queryset, query):
    """Apply a search term, or don't. `icontains` on an unindexed column, knowingly -- see the module
    docstring of `gamelists.services.game_search`, which measured the same thing and recorded that no
    expression index on `UPPER(name)` exists to serve it."""
    query = clean_term(query)
    if not query:
        return queryset
    return queryset.filter(name__icontains=query)


def _catchup_offers(profile, challenge, key, *, query, limit):
    """([(contract, via, completed_at)], total) for the already-completed games a rule currently lifts.

    Asks `catchup_offers` for the labels AND the dates rather than deciding or re-deriving either, so the
    panel and `assign` cannot disagree about whether a square will be stamped `import` or `hatch`, and the
    five-query trophy read happens once. A contract neither rule lifts is dropped rather than shown greyed
    out: it is not an offer, and a list of things you cannot pick is how a panel becomes a puzzle.

    RETURNS "IS THERE MORE" TOO, because this is a `[:limit]` slice like every other list here and a
    truncated list that looks complete is a lie. Not a count: see the note at the return.
    """
    pool = _narrow(eligibility.completed_contracts(profile, challenge, key), query)
    candidates = list(pool.order_by(*BY_NAME)[:limit])
    if not candidates:
        return [], 0

    offers = svc.catchup_offers(profile, challenge, key, candidates)
    lifted = [(c, offers[c.id][0], offers[c.id][1]) for c in candidates if c.id in offers]
    # A BOOLEAN, NOT A COUNT, and the count it replaces was wrong in the branch a returning hunter hits.
    #
    # It read `len(lifted) if len(candidates) < limit else pool.count()` under a comment claiming it
    # counted "the CANDIDATES a rule actually lifts, not the whole completed pool" -- which described one
    # branch and was the exact opposite of the other. `pool` is every completed contract in the slot's
    # pool with NO lifting filter, so a hunter on run 2 with a deep pool and 40 completed B-games got
    # `catchup: []` beside `catchup_total: 40`: a count of forty games above a block holding none, none of
    # them pickable. The two branches also returned different quantities under one key, so no client could
    # interpret the field at all.
    #
    # The honest question is "might there be more than I am seeing", and the honest answer is whether the
    # candidate slice filled up. Counting the lifted ones across the WHOLE pool would need the lifting rule
    # pushed into the queryset, and only the `import` half is per-row -- a real change, not a fix.
    return lifted, len(candidates) >= limit


def _row(contract, covers):
    return {
        'slug': contract.slug,
        'name': contract.name,
        'cover': covers.get(contract.id),
    }


def _catchup_row(contract, via, when, covers):
    row = _row(contract, covers)
    row['via'] = via
    # Only an `import` row has a date to show, and it is the date the WORK happened (read from trophy
    # data), never an `EarnedContract` detection stamp -- those record when we noticed, not when they did.
    # `catchup_offers` already returns None for a hatch row, so this does not have to branch on `via`.
    row['completed_at'] = when
    return row


def _atom_for(challenge, key):
    """The job atom for a jobs slot, None for an A-Z letter. One query, and only for jobs."""
    if challenge.challenge_type == CHALLENGE_TYPE_AZ:
        return None
    job = Job.objects.filter(slug=key).first()
    return job_atom(job) if job else None


def _job_names(challenge):
    """{slug: name} for a jobs run, empty for A-Z. One query for the 25-row catalogue.

    THERE ARE SEVERAL WAYS TO NAME A JOB in this app -- `_atom_for` (one slug, with its icon and
    discipline), this one (the catalogue as slug to name), `slot_render._key_atoms` (the catalogue as
    atoms), and `slot_render.label_for_key` (a slug degraded to a readable name when the row is gone). They
    answer different questions and the overlap is real. An earlier version of this note counted them and
    declared a limit, in the same change that imported a fourth -- and this branch has already been wrong
    once about a count of its own copies. Consolidation is worth doing; asserting a number is not.
    """
    if challenge.challenge_type == CHALLENGE_TYPE_AZ:
        return {}
    return dict(Job.objects.values_list('slug', 'name'))
