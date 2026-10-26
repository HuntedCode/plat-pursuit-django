"""What the hunter READING a list has done with its games (owner, 2026-10-26, review AU-02).

A list is concept-keyed, and a hunter can hold several versions of one game (the PS4 and PS5 stacks), so a
card's state is the hunter's BEST version: a platinum beats none, then the furthest along. That is the version
the card's mark and bar describe, in the same vocabulary the browse game cards already use
(`partials/_hunter_mark.html`).

ONE QUERY per page, bounded by the list itself (`MAX_ITEMS_PER_LIST` concepts, a few versions each), and the
pick is made in Python over those rows: the bounded-slice form the whale rule names as acceptable. It never
reads the hunter's library, only their rows for the games on screen.
"""
from types import SimpleNamespace

from trophies.models import ProfileGame


def progress_by_concept(profile, concept_ids):
    """{concept_id: SimpleNamespace(has_plat, progress, has_platinum)} for each game the hunter has started.

    `has_platinum` is whether the picked version HAS a platinum to chase, which is what decides the bar's
    colour (an amber hunt versus slate progress). Concepts the hunter has never touched are absent.
    """
    concept_ids = list(concept_ids)
    if profile is None or not concept_ids:
        return {}
    best = {}
    for concept_id, has_plat, progress, defined in (
        ProfileGame.objects.filter(profile=profile, game__concept_id__in=concept_ids)
        .values_list('game__concept_id', 'has_plat', 'progress', 'game__defined_trophies')
    ):
        key = (bool(has_plat), progress or 0)
        if concept_id not in best or key > best[concept_id][0]:
            best[concept_id] = (key, bool((defined or {}).get('platinum')))
    return {
        cid: SimpleNamespace(has_plat=key[0], progress=key[1], has_platinum=has_platinum)
        for cid, (key, has_platinum) in best.items() if key[1] > 0 or key[0]
    }


def platinumed_by_list(profile, lists):
    """Set `viewer_platinumed` on each list: how many of its games the hunter has platinumed (review AU-02),
    for the My Lists cards and a profile's Lists tab (review ID-12). ONE grouped query for the whole page, counted in the database: an item counts when
    the hunter holds a platinum on any version of its game."""
    from django.db.models import Count, Exists, OuterRef

    from gamelists.models import GameListItem

    lists = list(lists)
    counts = {}
    if profile is not None and lists:
        platinumed = ProfileGame.objects.filter(profile=profile, has_plat=True,
                                                game__concept_id=OuterRef('concept_id'))
        counts = dict(
            GameListItem.objects.filter(game_list__in=lists).filter(Exists(platinumed))
            .values('game_list_id').annotate(n=Count('id')).values_list('game_list_id', 'n')
        )
    for game_list in lists:
        game_list.viewer_platinumed = counts.get(game_list.pk, 0)


def summarise(progress, total):
    """The header's line over one list: {'platinumed', 'started', 'total'}, or None when the hunter has done
    nothing with any of it (absence is not shown as content)."""
    platinumed = sum(1 for p in progress.values() if p.has_plat)
    started = sum(1 for p in progress.values() if not p.has_plat and p.progress > 0)
    if not platinumed and not started:
        return None
    return {'platinumed': platinumed, 'started': started, 'total': total}
