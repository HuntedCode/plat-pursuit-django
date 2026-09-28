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
from trophies.models import Job
from trophies.services.job_render import job_atom


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
    atoms = _key_atoms(challenge)

    return [_card(slot, covers, atoms) for slot in slots]


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
    atoms = _key_atoms(slot.challenge)
    return _card(slot, covers, atoms)


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
    }


def label_for_key(key):
    """What a square calls itself when no job atom names it.

    PUBLIC because the picker needs the same degradation: without it the same square read
    "Card Shark" on the grid and `card-shark` in the panel that opened over it.

    TWO CALLERS' WORTH OF TRAFFIC, and the earlier name plus its docstring got the proportions exactly
    backwards -- it was called `_fallback_label`, said "reachable one way only", and then four paragraphs
    later said "every A-Z square reaches this function". The second is the true one:

    - EVERY A-Z SQUARE, on every render. `_key_atoms` returns `{}` for that type, so the lookup always
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


def _key_atoms(challenge):
    """{job slug: job_atom} for the run's squares, empty for A-Z.

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
