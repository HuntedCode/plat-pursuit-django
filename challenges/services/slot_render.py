"""The squares of one run, ready to draw. Flat in queries however many squares there are.

A run is 26 squares (A-Z) or 25 (one per job), and every filled one wants cover art -- which is the
exact shape this project has been bitten by twice. The rule from CLAUDE.md is that a queryset rendering
many games must `select_related('concept', 'concept__igdb_match')` AND `.defer(...raw_response)`,
because `raw_response` is a ~30 KB IGDB blob no cover template reads and was the trigger for the May
2026 web-server OOM. A grid of 26 covers resolving one at a time is how that happens.

So nothing here resolves a cover per square. `gamelists.services.covers.cover_games_for` already does
this job batched, and its docstring frames it as exactly this problem -- the concept Game page's
`_host_game` rule, applied to a whole grid in one query, with the `select_related` and the `defer`
already in place.

WHY IT IS IMPORTED ACROSS APPS. `cover_games_for` and `sort_key` are catalogue concerns that happen to
have been extracted into `gamelists/services/` first: they reach only into `trophies` and know nothing
about lists.

THE MODULE AS A WHOLE IS NOT SO CLEAN, and an earlier version of this note said it was. `covers.py` also
holds `attach_cover_games`, which is GameList-specific and imports `GameListItem` -- only the
module-level imports are trophies-only. So "move the file to `trophies/services/` when a third consumer
appears" was the wrong recommendation: it would drag a lists-only function into `trophies`. What can move
is the two functions this imports. Noted rather than done, because it is somebody else's branch.

THERE IS A THIRD CONTRACT-COVER RESOLVER, and it is worth naming here rather than discovering later.
`trophies.services.new_contracts_modal._hero_covers` answers "cover art for a batch of contracts" in
ONE query rather than the three here, and would have been the better starting point -- except that it
keys on `igdb_id` and so silently drops an admin/episodic contract whose concepts come from its
`ContractBundle` rows, which `member_concepts_by_contract` handles. More importantly it picks a
contract's stack by MOST-PLAYED (`-played_count`), where this picks by platform priority. So the same
contract can legitimately show different art on a challenge square and in Career's new-contracts modal.

That divergence is real, it predates this module, and unifying it is a decision about which rule wins
site-wide rather than a cleanup to slip into a page build. An earlier version of this docstring claimed
the opposite -- that "which stack's art" was answered one way across the whole site. It is not, and a
comforting claim is worse than none.
"""
from gamelists.services.covers import cover_games_for, sort_key

from challenges.models import CHALLENGE_TYPE_AZ
from challenges.services.eligibility import member_concepts_by_contract
from trophies.models import Contract, Job
from trophies.services.job_render import DISCIPLINE_ICON, DISCIPLINE_LABELS, job_atom


def slot_cards(challenge):
    """Every square of `challenge` in render order, each a dict the template draws directly.

    QUERY COST, stated as the range it actually is rather than as its worst case:

    - an EMPTY run is 1 (the slots; with no contracts no cover work is attempted at all), or 2 for a Job
      Coverage run, which also needs its catalogue;
    - a FILLED run is 4, or 5 for Job Coverage: the slots, two to resolve contract membership, one for
      every cover on the page, and the catalogue;
    - 3 (or 4) in the all-episodic case, where no contract carries an `igdb_id` and
      `member_concepts_by_contract` skips its first query;
    - 3 (or 4) again by a different route, when membership resolves to no concepts at all (every contract
      unmatched or untrusted) so `cover_games_for` is never called;
    - 2 (or 3) for an all-episodic run whose bundles are empty, which is both of those at once.

    None of those numbers moves with the square count, which is the property worth having. An earlier
    version of this docstring gave only "four and five" -- the common case presented as the only one.
    """
    slots = list(challenge.slots.select_related('contract'))
    covers = covers_by_contract([s.contract for s in slots if s.contract_id])
    atoms = key_atoms(challenge)

    cards = [_card(slot, covers, atoms) for slot in slots]
    # POSITION IN THE WHOLE RUN, stamped here rather than read from `forloop` in the template.
    #
    # The template used `forloop.counter0` for two things: the entrance stagger and the lazy-image
    # threshold. Grouping the squares into shelves restarts that counter per GROUP -- five per shelf on a
    # jobs run -- so the threshold of seven would never be reached and all 25 covers would load eagerly,
    # silently undoing a fix made two rounds earlier. It also frees the partial from needing a loop at all,
    # which is what lets a single square be re-rendered on its own after a write.
    for index, card in enumerate(cards):
        card['index'] = index
    return cards


def cards_for(slots):
    """Cards for several slots of ONE run, sharing the cover map and the catalogue read.

    THE BATCH VERSION OF `card_for`, added when a caller appeared that needed it: a Claim-all reply
    re-renders every square it paid, and looping the single-slot builder over 25 of them meant 25 cover
    resolutions and 25 reads of the same 25-row job catalogue. `card_for`'s docstring is right that nothing
    in IT scales with the run; the caller was what scaled.

    `index = 0` for every card, exactly as `card_for` does and for the same reason: these squares are
    arriving one at a time into a page that already exists, so none of them should be lazy.

    ONE RUN'S SLOTS, not an arbitrary mix. `key_atoms` takes a challenge, so a batch spanning two runs
    would silently label half of them from the wrong catalogue -- hence the first slot decides, and callers
    have no reason to mix (both doors act on a single run).

    IT FETCHES THE CONTRACTS ITSELF, and the first version did not -- which left the N+1 this function was
    written to remove. `[s.contract for s in slots]` lazy-loads one row per slot unless the caller happened
    to `select_related`, and `redeem_all`'s queryset cannot: it is a `select_for_update`, where joining the
    contract in would take a row lock on the catalogue as well. So a 25-square Claim all measured 33 queries,
    25 of them the identical single-row contract fetch -- better than the 125 it replaced, and not the flat
    cost the docstring implied.

    Asking here rather than pushing `select_related` onto callers is also the right place for it: a batch
    helper that depends on how its caller built the queryset is a trap for the next caller.
    """
    slots = list(slots)
    if not slots:
        return []
    contract_ids = {s.contract_id for s in slots if s.contract_id}
    covers = covers_by_contract(
        list(Contract.objects.filter(pk__in=contract_ids)) if contract_ids else [])
    atoms = key_atoms(slots[0].challenge)
    cards = []
    for slot in slots:
        card = _card(slot, covers, atoms)
        card['index'] = 0
        cards.append(card)
    return cards


def card_for(slot):
    """One slot's card, for redrawing a single square after a write.

    WHY THIS EXISTS: filling a square used to reload the whole page. That was defensible while the only
    alternative was a second renderer in JavaScript -- a filled square needs cover art the write reply did
    not carry, a completed one needs its check glyph, and a client that rebuilt those could drift from the
    template that draws them everywhere else. But the cost landed on the hunter: 26 squares means 26 full
    navigations, each flashing the page, replaying the grid's entrance and scrolling to the top.

    So the server re-renders the one square it just changed and hands back the markup. Still ONE renderer --
    `partials/_square_body.html`, the same file the page uses -- and no reload.

    FOUR QUERIES for a filled square (its contract, then three for the cover), one for a jobs run's
    catalogue, and one for an empty square. It is a single slot, so nothing here scales with the run.
    """
    covers = covers_by_contract([slot.contract] if slot.contract_id else [])
    atoms = key_atoms(slot.challenge)
    card = _card(slot, covers, atoms)
    # Position zero: one image arriving on its own is never lazy, and the partial no longer
    # depends on a `forloop` being absent to get that answer.
    card['index'] = 0
    return card


def slot_groups(challenge):
    """The run's squares, grouped the way the page should draw them.

    `[{label, slug, icon, cards, done, total, dom_id}]`, the same seven keys for both challenge types --
    `done`/`total` were once omitted on the A-Z branch, which made `group['total']` a `KeyError` on exactly
    one type.

    WHY GROUPS AT ALL. A Job Coverage run is 25 squares that are really FIVE groups of five -- the radar's
    disciplines -- and they were being laid out seven across, so every group broke mid-row and the structure
    was invisible. The owner called it: "5 groupings of 5 spread across rows of 7 just looks wrong".

    A-Z GETS ONE UNLABELLED GROUP, not 26 groups of one. The alphabet has no sub-structure, so the template
    draws a single grid exactly as before and nothing about that page changes.

    ORDER COMES FROM `DISCIPLINE_LABELS`, via `job_render`, because that dict IS the canonical radar
    sequence -- combat, exploration, mind, heart, finesse -- and sorting the `discipline` COLUMN gives the
    alphabetical one instead, which agrees for two disciplines and then diverges.

    GROUPED BY DICT, NOT BY ADJACENCY, and the distinction is load-bearing rather than pedantic. An earlier
    version of this said `slot_keys_for` had already stamped `position` in this order "so the slots arrive
    grouped; this only has to segment them". That holds for the canonical five on an untouched catalogue and
    fails two ways otherwise: `discipline_order()` collapses every unseeded discipline to one sort value, so
    two unknown disciplines interleave; and a `Job.discipline` edited AFTER a run was created moves nothing,
    because `position` is frozen. Bucketing into a dict is correct under both -- a group's cards need not be
    contiguous -- and the cards within a group still come out in the order the run froze.

    NO EXTRA QUERIES over `slot_cards`: the labels and icons are module constants, and the atoms carry the
    discipline each square belongs to.
    """
    cards = slot_cards(challenge)
    if challenge.challenge_type == CHALLENGE_TYPE_AZ:
        # `done`/`total` ARE INCLUDED even though the A-Z template never draws a head. One function
        # returning two dict shapes means a Python consumer reading `group['total']` raises `KeyError`
        # on exactly one challenge type -- the kind of difference that is invisible until it is a 500.
        return _with_dom_ids([{'label': '', 'slug': '', 'icon': '', 'cards': cards,
                               'done': sum(1 for c in cards if c['is_completed']),
                               'total': len(cards)}])

    by_discipline = {}
    for card in cards:
        # A card whose `Job` row was deleted has no atom and so no discipline. It lands in a group of its
        # own rather than being dropped -- a square that exists must be drawable, and `label_for_key` has
        # already given it something readable to say.
        slug = (card['job'] or {}).get('disc_slug') or ''
        by_discipline.setdefault(slug, []).append(card)

    # EVERY BUCKET IS EMITTED, and the leftover clause is the whole point rather than defensive padding.
    # Iterating the canonical five plus `''` silently DROPPED any other discipline together with its
    # squares: `slot_keys_for` builds a run from `Job.objects` with no discipline filter, and
    # `Job.discipline` is `choices=` only -- which Postgres does not enforce -- so a discipline added to
    # `Job.DISCIPLINES` (or written straight into the column) without a matching `DISCIPLINE_LABELS` entry
    # would give a run that DREW fewer squares than it counted: the tally says `x/total_slots` while one
    # square has no DOM and therefore no way to ever fill it. Stated as a hazard rather than as history,
    # which an earlier version of this comment got wrong twice over -- `Job.DISCIPLINES` and
    # `DISCIPLINE_LABELS` currently carry the same five, so it has not happened, and the "26-slot run" it
    # described was an A-Z square count on a jobs board.
    # `job_render.discipline_order` already plans for this exact case ("an unseeded discipline sorts last"),
    # so the catalogue contemplates it even though the labels dict did not.
    #
    # Canonical order first, then whatever is left in the order it arrived -- which is `position` order, so
    # an unlabelled discipline lands where the run froze it.
    leftovers = [s for s in by_discipline if s and s not in DISCIPLINE_LABELS]
    groups = []
    for slug in list(DISCIPLINE_LABELS) + leftovers + ['']:
        if slug not in by_discipline:
            continue
        members = by_discipline[slug]
        groups.append({
            'label': _discipline_label(slug),
            'slug': slug,
            'icon': DISCIPLINE_ICON.get(slug, ''),
            'cards': members,
            # Per-discipline progress, which is what the label area is FOR: a hunter reading a shelf
            # wants to know how much of that discipline is left, and the page-level tally cannot say.
            'done': sum(1 for c in members if c['is_completed']),
            'total': len(members),
        })
    return _with_dom_ids(groups)


def _discipline_label(slug):
    """What a shelf calls itself.

    THE UNKNOWN CASE USED TO READ "Other", which was wrong in the same way a raw slug is wrong: two jobs
    seeded under different unmapped disciplines produced two shelves both titled "Other", indistinguishable
    from each other and from the group of squares whose `Job` row was deleted. A hunter cannot act on that.

    So an unmapped discipline is named after ITSELF -- `archaeology` reads "Archaeology" -- which is the same
    degradation `label_for_key` applies to a job slug that has lost its row, for the same reason. The truly
    nameless case is the blank slug, where there is no `Job` left to ask, and that one keeps "Other".
    """
    if slug in DISCIPLINE_LABELS:
        return DISCIPLINE_LABELS[slug]
    if not slug:
        return 'Other'
    return slug.replace('-', ' ').replace('_', ' ').title()


def _with_dom_ids(groups):
    """A DOM id per group, unique within the page, for `aria-labelledby` to point at.

    WHY UNIQUENESS IS CORRECTNESS HERE, not tidiness. Each shelf names itself by pointing
    `aria-labelledby` at its own `<h2>`; duplicate ids mean every one of them resolves to the FIRST
    matching heading, so several shelves would announce the same discipline. That is worse than no name.

    The id was built inline from `slug|default:'other'`, which collides: a blank slug (a deleted `Job`) and a
    job whose discipline is literally `other` both produce `csq-shelf-other`. `Job.discipline` is `choices=`
    with no database constraint, which is the premise this whole leftover branch exists for, so the second
    half of that is reachable by the same route as the first.

    Suffixed rather than hashed or indexed, so the common five keep readable, stable, assertable ids.
    """
    seen = set()
    for group in groups:
        base = 'csq-shelf-%s' % (group['slug'] or 'other')
        dom_id, suffix = base, 2
        while dom_id in seen:
            dom_id = '%s-%d' % (base, suffix)
            suffix += 1
        seen.add(dom_id)
        group['dom_id'] = dom_id
    return groups


def _card(slot, covers, atoms):
    """One square.

    THE NAME COMES FROM THE SNAPSHOT, never from `slot.contract.name`, and that is the whole reason the
    snapshot exists: a finished square should say what it said the day it was finished, even after staff
    rename the contract or re-anchor it onto a different IGDB entry. The COVER comes from the live
    contract, because a picture has no such promise to keep and the best available art is the right art.

    `job` IS THE KEY for a Job Coverage square and is None for an A-Z one, which is the one branch the
    template needs: a letter is its own label and a job is an icon, a discipline colour and a name.
    `label` stays a plain string for both, because the screen-reader line and the `title` attribute want
    one spelling of "which square is this" whichever type the run is.

    NOTHING SPECULATIVE IN HERE. `position`, `completed_via` and `contract_slug` were all built for all
    26 cards and read by nobody, which is the shape of a dict that grows a field per guess. They come
    back when a reader does: `completed_via` when the picker needs to show a square's provenance,
    `contract_slug` when a square links to its game.

    `xp_pending` ARRIVED WITH A READER, which is what that rule asks. It marks a square holding unclaimed
    job XP, so the grid can show where the reward panel's rows came from. Three terms, and the third is the
    one that is easy to miss: the square must be COMPLETE, must not be paid yet, and its job must still be
    in the catalogue -- because a square whose `Job` was deleted can never be paid, and marking it would
    promise XP that `redeem_all` skips. `atom` presence IS that catalogue check: `key_atoms` is built from
    `Job` rows, so a missing atom means a missing job. It is False for every A-Z square, where `atoms` is
    empty by construction and there is no XP to claim.
    """
    atom = atoms.get(slot.key)
    return {
        'key': slot.key,
        'label': atom['name'] if atom else label_for_key(slot.key),
        'job': atom,
        'is_filled': slot.is_filled,
        'is_completed': slot.is_completed,
        'game_name': slot.contract_name,
        'cover': covers.get(slot.contract_id),
        'xp_pending': bool(slot.is_completed and slot.xp_redeemed_at is None and atom is not None),
    }


def label_for_key(key):
    """What a square calls itself when no job atom names it.

    PUBLIC because the picker needs the same degradation: without it the same square read
    "Card Shark" on the grid and `card-shark` in the panel that opened over it.

    TWO CALLERS' WORTH OF TRAFFIC, and the earlier name plus its docstring got the proportions exactly
    backwards -- it was called `_fallback_label`, said "reachable one way only", and then four paragraphs
    later said "every A-Z square reaches this function". The second is the true one:

    - EVERY A-Z SQUARE, on every render. `key_atoms` returns `{}` for that type, so the lookup always
      misses and this is the normal label producer for 26 of every 26 squares. `'A'` returns `'A'`.
    - A JOB SQUARE ONLY IF ITS `Job` ROW WAS DELETED under a live run. `total_slots` is frozen at
      creation so the catalogue cannot move under a run, and this is the one place that promise is not
      kept for free: the `key` survives the deletion but nothing can turn it back into a name. A raw slug
      is wrong twice over -- `card-shark` reads as a content bug, and the square also loses its icon and
      discipline colour (there is no atom), so it renders with A-Z's layout beside 24 neighbours that keep
      the jobs one. Nothing here restores the colour; the label can at least read as a name.

    KEYED ON LENGTH, not on a hyphen. A hyphen test left single-word slugs alone, so a deleted
    `Architect` rendered as `architect`. An A-Z key is one character and a job slug never is.
    """
    return key if len(key) == 1 else key.replace('-', ' ').title()


def covers_by_contract(contracts):
    """{contract_id: Game} for every contract that has one, in a fixed number of queries.

    PUBLIC because the picker needs the same thing for its own bounded page of results. It was
    private when the grid was the only caller; a second consumer is the same condition that promoted
    `covers.sort_key` and `eligibility.member_concepts_by_contract` earlier in this branch.

    A contract usually has exactly one member concept; the exceptions are same-entry multi-platform or
    regional siblings. Where there are several, the cover is picked by `covers.sort_key` -- the same
    function, not a copy of the rule. That matters: this expression has already taken out four cover
    surfaces once, and the docstring on it is the record of how.

    ONE BOUND WORTH KNOWING, stated correctly on the second attempt. `cover_games_for` caps its fetch at
    `4 x len(ids)` rows. The first version of this paragraph claimed a run with regional siblings could
    exceed that budget -- which is backwards: `ids` IS the concept union, so a sibling concept raises the
    rows fetched AND the budget together. Truncation needs an average of more than four trophy lists per
    CONCEPT, which is the same pathological shape a list page faces and no more likely here.

    What the cap does when it bites is still worth knowing: it applies under `order_by('concept_id', 'pk')`
    ascending, so it drops the highest concept ids entirely (those squares fall to the no-art placeholder)
    and cuts the boundary concept mid-stack, which can hand that one square a suboptimal cover rather than
    none -- the case `covers.py` names when it explains why the cap is generous. The query SHAPE stays
    flat either way; it is the output that degrades.
    """
    if not contracts:
        return {}

    concepts_by_contract = member_concepts_by_contract(contracts)
    every_concept = {cid for ids in concepts_by_contract.values() for cid in ids}
    if not every_concept:
        return {}

    games_by_concept = cover_games_for(every_concept)

    out = {}
    for contract_id, concept_ids in concepts_by_contract.items():
        candidates = [games_by_concept[cid] for cid in concept_ids if cid in games_by_concept]
        if candidates:
            out[contract_id] = min(candidates, key=sort_key)
    return out


def key_atoms(challenge):
    """{job slug: job_atom} for the run's squares, empty for A-Z.

    PUBLIC on the same condition that promoted `covers_by_contract` and `label_for_key` above: a second
    consumer. The picker's search panel needs the icon and the discipline for every square a game fits, so
    that a button offering the Slayer square looks like the Slayer square. It read the catalogue as a
    separate slug-to-name dict before, which was a second query and a second shape for a subset of this.

    A-Z needs nothing -- the key IS the label, and a dict lookup that always misses is cheaper than a
    branch in the template. Job Coverage needs the catalogue, which is 25 rows in one query; without it
    every square would read `card-shark` instead of `Card Shark`.

    `job_atom` RATHER THAN `values_list('slug', 'name')`, which is what this returned first. The square
    shows the job's icon tinted by its discipline, exactly as `.pp-jobchip` does everywhere else, and
    both of those live on the atom -- so a name-only dict meant the grid would have had to invent its own
    way of saying "this is a Finesse job". One definition of a job's identity, reused.

    NOT `.pp-jobchip` ITSELF on the square, though the plan named it: the chip is icon + name in a pill,
    and a square is ~109px wide at 375px, where that pill either overflows or ellipsises the name to
    `Card Sh...`. The chip is the right primitive for the picker panel, where there is a row's width to
    spend. Here the atom's parts are used directly: icon in the corner, name on its own line.
    """
    if challenge.challenge_type == CHALLENGE_TYPE_AZ:
        return {}
    return {job.slug: job_atom(job) for job in Job.objects.all()}
