"""Cover art for a concept, in one query for a whole page.

THE PROBLEM CONCEPT-KEYING CREATES. The site's single-source cover chain is
`Game.display_image_url` -- trusted IGDB cover, then `concept.concept_icon_url`, then the PSN
`title_image` / `title_icon_url`. Two of those four sources live on `Game` (a trophy list), so a
Concept alone cannot answer "what does this game look like". `Concept.cover_url` is the IGDB half
only, and an unmatched concept has nothing.

HOW THE REST OF THE SITE ANSWERS IT. Two established shapes, and this reuses the cheaper one:

- Genre, Theme, Franchise and Company each materialize a `representative_game` FK, recomputed
  nightly by `recompute_tag_covers`. Right for a few thousand slow-moving rows; wrong here, since it
  would mean a new column on `Concept` (and therefore a new `absorb()` branch, a new recompute pass,
  and a nightly job) for something a list page can derive on the spot.
- The concept Game page picks a `_host_game`: the first trophy list, in deterministic platform
  order, whose concept holds a trusted match. Correct, but it runs per concept -- fine for one page
  about one game, fatal for a grid of twenty lists showing four covers each.

So this is `_host_game`'s rule, BATCHED. One query for every concept on the page, the pick made in
Python over rows already in memory. The browse grid stays query-flat, which is the property
`test_lists_browse` pins and the reason the old browse page was rebuilt in the first place.

Why platform order matters even though IGDB comes first: a concept WITH a trusted match returns the
same IGDB cover whichever of its lists you ask, so the order is free. A concept WITHOUT one falls
through to PSN art, and then "which stack" decides whether you get the PS5 key art or a PS3 icon.
"""
from trophies.models import Game
from trophies.util_modules.constants import platform_priority_rank


def cover_games_for(concept_ids):
    """Map each concept id to the `Game` whose cover should represent it.

    Returns `{concept_id: Game}`, omitting concepts with no trophy list at all (a `PP_*` stub, or a
    concept whose games were reassigned) -- callers render the placeholder for a missing key rather
    than being handed a None to check.

    ONE query regardless of how many concepts are asked for. `select_related` pulls the two hops
    `display_image_url` walks, and `raw_response` is deferred: it is the ~30 KB IGDB blob that no
    cover template reads and the direct trigger for the May 2026 web-server OOM, so every queryset
    that joins `igdb_match` for art has to drop it (CLAUDE.md).
    """
    ids = list({int(cid) for cid in concept_ids if cid})
    if not ids:
        return {}

    # `select_related` + `defer`, and NOT `.only()`: naming a field list that omits every
    # `concept__igdb_match__*` column makes Django treat the relation as deferred and traversed at
    # once, which it refuses ("cannot be both deferred and traversed"). The documented pairing in
    # CLAUDE.md is exactly these two, so follow it rather than inventing a narrower one.
    rows = (
        Game.objects.filter(concept_id__in=ids)
        .select_related('concept', 'concept__igdb_match')
        .defer('concept__igdb_match__raw_response')
    )

    best = {}
    for game in rows:
        current = best.get(game.concept_id)
        if current is None or _sort_key(game) < _sort_key(current):
            best[game.concept_id] = game
    return best


def _sort_key(game):
    """Deterministic, and deterministic is the point.

    Platform priority first, then pk. Without the pk tiebreak two lists on the same platform would
    resolve by whatever order the database happened to return, so the same list could render a
    different cover on two consecutive loads -- the kind of flicker that reads as a bug and cannot
    be reproduced on request.

    `title_platform` is a LIST (`JSONField(default=list)`), because a cross-buy game is on PS4 AND
    PS5. This read it as a scalar and did `_RANK.get(game.title_platform, ...)`, which is a dict
    lookup on a list: `TypeError: unhashable type: 'list'` for every real row. It took out all four
    cover surfaces -- browse tiles, My Lists tiles, the detail items, and the adder search -- and no
    test caught it because the tests handed `title_platform='PS5'`, a STRING, overriding the
    factory's correct `['PS5']` and inventing a shape the schema cannot hold.
    """
    return (platform_priority_rank(game.title_platform), game.pk)


def attach_cover_games(lists, *, per_list=4):
    """Give each list in `lists` a `cover_items` attribute for the browse tile.

    The tile composes its mosaic around however many covers there are (`is-1` .. `is-4`), so this
    hands back what exists rather than padding. Two queries for the whole page no matter how many
    lists: one for the bounded item slice, one for their concepts' games.

    `per_list` mirrors the tile's `is-N` compositions. It is bounded HERE rather than by the
    template, because slicing in the template would mean fetching every item on every list.
    """
    from gamelists.models import GameListItem

    lists = list(lists)
    if not lists:
        return lists

    # `position__lt=per_list` is only correct because positions are DENSE -- the contract the
    # service re-compacts on removal and `absorb()` now repairs after a merge. A gap here silently
    # renders a three-cover mosaic on a four-game list.
    items = (
        GameListItem.objects
        .filter(game_list__in=lists, position__lt=per_list)
        .order_by('game_list_id', 'position')
        .values_list('game_list_id', 'concept_id')
    )

    by_list = {}
    concept_ids = set()
    for list_id, concept_id in items:
        by_list.setdefault(list_id, []).append(concept_id)
        concept_ids.add(concept_id)

    covers = cover_games_for(concept_ids)
    for game_list in lists:
        game_list.cover_items = [
            covers[cid] for cid in by_list.get(game_list.pk, []) if cid in covers
        ]
    return lists
