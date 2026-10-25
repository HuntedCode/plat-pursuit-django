"""The game flags a surface shows, as badges: which ones, for which game, by which rule.

A game's flags are columns on `Game` (one trophy list): `is_obtainable`, `is_delisted`, `has_online_trophies`,
`has_buggy_trophies` and the shovelware status, set by community reports and staff (docs/features/community-flags.md).
This module turns them into an ordered tuple of KEYS, which `{% flag_badges %}` draws and the JSON endpoints send.

TWO RULES, and which one a surface uses is a fact about what the card stands for (owner, 2026-10-24):

- A card for ONE trophy list (a hunter's own game, a calendar day's platinum, a Trophy Lists browse card) shows that
  list's own flags: `own_keys`.
- A card for a whole GAME (a Game List entry, a condensed browse card, a nav search result) shows a flag only when
  EVERY version has it: `work_keys_by_game` / `work_keys_by_concept`. It answers "can I still do this game?": a PS3
  version going unobtainable does not mark a game the PS4 version still plats. The Game page's own chips, the
  picker's availability (owner, 2026-10-10) and these badges are the same rule, and the versions are the trophy
  lists on the Game page the card links (`game_grouping_service.page_version_rows`).

BUGGY IS NOT AN EVERYWHERE FLAG (owner, 2026-10-24): it shows on the Game pages and the browse cards, as before, and
nowhere new. Callers that keep it pass `buggy=True`.
"""
from types import SimpleNamespace

from trophies.models import Concept, Game, SHOVELWARE_FLAGGED_STATUSES
from trophies.services.game_grouping_service import page_version_rows

#: Display order, most severe first: a card that can carry three shows the one that stops a hunter at the top.
FLAG_ORDER = ('unobtainable', 'delisted', 'online', 'buggy', 'shovelware')

#: key -> (label, tone). The label is the badge's `title` and its screen-reader text; the tone is its colour, by
#: severity: red is "you cannot get these trophies", amber a caution, blue information.
FLAG_META = {
    'unobtainable': ('Unobtainable', 'error'),
    'delisted': ('Delisted', 'warning'),
    'online': ('Online trophies', 'info'),
    'buggy': ('Buggy trophies', 'warning'),
    'shovelware': ('Shovelware', 'warning'),
}

#: The sentence a labelled chip's tooltip carries on the Game pages (`_game_flags.html`), where there is room to
#: say what the flag means and not only its name.
FLAG_TIPS = {
    'unobtainable': 'Trophies in this game can no longer be earned (e.g. server shutdown).',
    'delisted': 'This game has been removed from the PlayStation Store.',
    'online': 'This game has trophies that require an online connection.',
    'buggy': 'This game has trophies that are known to be buggy or broken.',
    'shovelware': 'This game has been flagged as shovelware.',
}

#: The `Game` columns the rule reads, for callers that fetch rows rather than models.
FLAG_FIELDS = ('is_obtainable', 'is_delisted', 'has_online_trophies', 'has_buggy_trophies', 'shovelware_status')

_TESTS = {
    'unobtainable': lambda g: not g.is_obtainable,
    'delisted': lambda g: g.is_delisted,
    'online': lambda g: g.has_online_trophies,
    'buggy': lambda g: g.has_buggy_trophies,
    # The status, not `Game.is_shovelware`, so a `values()` row answers the same as a model.
    'shovelware': lambda g: g.shovelware_status in SHOVELWARE_FLAGGED_STATUSES,
}


def every_version_keys(games, buggy=False):
    """The flags EVERY one of `games` carries, in `FLAG_ORDER`. Nothing for no games: "all of none" is not a flag.

    `games` are `Game`s or anything with `FLAG_FIELDS` as attributes."""
    games = list(games)
    if not games:
        return ()
    return tuple(key for key in FLAG_ORDER
                 if (buggy or key != 'buggy') and all(_TESTS[key](g) for g in games))


def own_keys(game, buggy=False):
    """One trophy list's own flags. Reads columns already on the row, so it costs no query."""
    return every_version_keys([game], buggy=buggy)


def keys_from_rows(rows, buggy=False):
    """`every_version_keys` for `values()` rows (dicts holding `FLAG_FIELDS`), as `page_version_rows` returns them."""
    return every_version_keys([SimpleNamespace(**row) for row in rows], buggy=buggy)


def _rows_to_keys(rows_by_anchor, buggy):
    return {anchor: keys_from_rows(rows, buggy=buggy) for anchor, rows in rows_by_anchor.items()}


def work_keys_by_game(games, buggy=False):
    """`{game.pk: keys}` for a page of games, each standing for its whole work (every version must agree).

    Two queries for the page: the anchors' own concept and IGDB id, then every version's flags. A game with no
    concept has no Game page and falls back to its own columns. An anchor whose own np id is blank is not a list on
    its page, so it reads its page's lists only, as the page does. Pass the games already on the page."""
    games = [g for g in games if g is not None]
    if not games:
        return {}
    anchors = Game.objects.filter(pk__in={g.pk for g in games}).values_list(
        'pk', 'concept_id', 'concept__igdb_match__igdb_id', 'concept__igdb_match__status')
    out = _rows_to_keys(page_version_rows(anchors, FLAG_FIELDS), buggy)
    for g in games:
        out.setdefault(g.pk, own_keys(g, buggy=buggy))
    return out


def work_keys_by_concept(concept_ids, buggy=False):
    """`{concept_id: keys}` for concept-keyed surfaces (a Game List entry, a rating, a search result): every
    version of the concept's work must agree. Two queries for the page. A concept with no games yet says nothing."""
    concept_ids = {cid for cid in concept_ids if cid is not None}
    if not concept_ids:
        return {}
    anchors = Concept.objects.filter(pk__in=concept_ids).values_list(
        'pk', 'pk', 'igdb_match__igdb_id', 'igdb_match__status')
    return _rows_to_keys(page_version_rows(anchors, FLAG_FIELDS), buggy)
