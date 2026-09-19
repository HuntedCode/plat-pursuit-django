"""Cover art for a browse tile, without an N+1.

Lifted from `gamelists.services.covers`, and it REUSES that module's `cover_games_for` rather than
copying it: that function takes bare concept ids, knows nothing about lists, and already carries the
two pieces of hard-won discipline this needs -- one query regardless of input size, and
`select_related('concept__igdb_match')` paired with `.defer('...raw_response')`, which is the ~30 KB
IGDB blob behind the May 2026 web-server OOM.

WHAT IS DIFFERENT HERE IS THE GRID. A list always has items, and so do a tier list and a poll -- but
a GRID has no author pool at all (owner's call, 2026-09-19), so it can never have author-chosen art.
Inventing some by reaching into its responses would be a per-viewer aggregate on a browse page, the
whale rule's exact shape. So a grid renders with no mosaic, and the tile treats that as a state
rather than as missing data. When the tally lands there is a cheap cached answer available; until
then the honest render is the empty one.

THE TILE MUST NOT READ THIS BACKWARDS. An empty `cover_items` does NOT mean "a grid": a tier list
whose first four games have no trophy list comes back empty too, which is `cover_games_for`'s stated
contract. The tile asks the shape.
"""
from gamelists.services.covers import cover_games_for
from prompts.models import PromptGame

#: How many covers a tile shows. The bound is what makes the prefetch below safe to run over a whole
#: page of prompts, and it is why `PromptGame.position` is kept dense -- a gap would render three
#: covers on a four-game prompt.
COVERS_PER_TILE = 4


def attach_cover_games(prompts, *, per_prompt=COVERS_PER_TILE):
    """Set `.cover_items` on each prompt: up to `per_prompt` `Game` rows, in pool order.

    TWO QUERIES FOR THE WHOLE PAGE, whatever the page size. One for the bounded pool slice, one for
    the covers. The alternative -- a property that looks up its own art -- is what took the list
    browse to 23 queries for 20 rows, and it looked like a field while doing it.

    Bounded by `position__lt`, which is only correct because positions are dense. `prompt_service`
    re-compacts on removal for exactly this reason.
    """
    prompts = list(prompts)
    if not prompts:
        return prompts

    rows = (PromptGame.objects
            .filter(prompt_id__in=[p.pk for p in prompts], position__lt=per_prompt)
            .order_by('prompt_id', 'position')
            .values_list('prompt_id', 'concept_id'))

    by_prompt = {}
    concept_ids = []
    for prompt_id, concept_id in rows:
        by_prompt.setdefault(prompt_id, []).append(concept_id)
        concept_ids.append(concept_id)

    games = cover_games_for(concept_ids)
    for prompt in prompts:
        # A concept with no trophy list is simply absent from `cover_games_for`'s mapping -- the
        # caller renders a placeholder for a missing entry rather than checking None, which is that
        # function's stated contract.
        prompt.cover_items = [games[cid] for cid in by_prompt.get(prompt.pk, []) if cid in games]
    return prompts
