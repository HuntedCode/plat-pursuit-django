"""Cover art for concept-keyed lists.

Concept keying is right for the product and it costs exactly one thing: `Game.display_image_url`
(the site's single cover chain) needs a `Game`, and two of its four sources live on that model, so a
Concept cannot answer for itself. This is the batched version of the concept Game page's `_host_game`
rule, and what these tests protect is that it stays BATCHED -- the browse grid was rebuilt once
already because a per-card property took it to 23 queries for 20 lists.
"""
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from gamelists.models import GameList
from gamelists.services import covers
from gamelists.services import game_list_service as svc
from tests.factories import ConceptFactory, GameFactory, ProfileFactory

pytestmark = pytest.mark.django_db


def _hunter(psn='curator', premium=False):
    profile = ProfileFactory(is_linked=True, psn_username=psn)
    if premium:
        profile.user_is_premium = True
        profile.save(update_fields=['user_is_premium'])
    return profile


def _list_with(profile, name, concepts):
    game_list = svc.create_list(profile, name=name)
    for concept in concepts:
        svc.add_concept(game_list, profile, concept)
    return game_list


# ── the pick ─────────────────────────────────────────────────────────────────────────────────────

def test_a_concept_resolves_to_one_of_its_trophy_lists():
    concept = ConceptFactory()
    game = GameFactory(concept=concept, title_platform=['PS4'])

    assert covers.cover_games_for([concept.pk])[concept.pk].pk == game.pk


def test_the_newest_platform_wins():
    """Free when the concept has a trusted IGDB match (every stack returns the same cover) and
    decisive when it does not: PS5 key art versus a PS3 icon."""
    concept = ConceptFactory()
    GameFactory(concept=concept, title_platform=['PS3'])
    ps5 = GameFactory(concept=concept, title_platform=['PS5'])
    GameFactory(concept=concept, title_platform=['PS4'])

    assert covers.cover_games_for([concept.pk])[concept.pk].pk == ps5.pk


def test_the_pick_is_stable_when_two_lists_share_a_platform():
    """Without a tiebreak the database's row order decides, so the same list could render a
    different cover on two consecutive loads -- a flicker that reads as a bug and cannot be
    reproduced on demand."""
    concept = ConceptFactory()
    first = GameFactory(concept=concept, title_platform=['PS4'])
    GameFactory(concept=concept, title_platform=['PS4'])

    picks = {covers.cover_games_for([concept.pk])[concept.pk].pk for _ in range(5)}

    assert picks == {first.pk}


def test_a_concept_with_no_trophy_list_is_omitted_rather_than_returned_as_none():
    """A `PP_*` stub has no games. Callers render the placeholder for a missing key; handing back a
    None would make every caller remember to check one."""
    stub = ConceptFactory()

    assert covers.cover_games_for([stub.pk]) == {}


def test_an_unknown_platform_sorts_last_rather_than_crashing():
    concept = ConceptFactory()
    GameFactory(concept=concept, title_platform=['PSVR3000'])
    known = GameFactory(concept=concept, title_platform=['PS4'])

    assert covers.cover_games_for([concept.pk])[concept.pk].pk == known.pk


def test_no_concepts_asked_costs_no_query():
    with CaptureQueriesContext(connection) as captured:
        assert covers.cover_games_for([]) == {}

    assert len(captured.captured_queries) == 0


# ── the thing that actually matters: flatness ────────────────────────────────────────────────────

def test_one_query_answers_any_number_of_concepts():
    """The whole reason this exists rather than calling the concept page's `_host_game` per item."""
    concepts = [ConceptFactory() for _ in range(12)]
    for concept in concepts:
        GameFactory(concept=concept, title_platform=['PS5'])

    with CaptureQueriesContext(connection) as one:
        covers.cover_games_for([concepts[0].pk])
    with CaptureQueriesContext(connection) as many:
        covers.cover_games_for([c.pk for c in concepts])

    assert len(one.captured_queries) == len(many.captured_queries) == 1


def test_the_browse_grid_costs_the_same_for_two_lists_as_for_eight():
    """The rebuilt browse page's contract, restated for the concept re-key. The version this
    replaces read a `first_game_image` PROPERTY per card, which is how it reached 23 queries for
    20 lists."""
    # A member, because eight lists is past the free cap of three -- the service's own guard doing
    # its job. Members get ten, and list SIZE is uncapped for everyone.
    profile = _hunter(premium=True)

    def build(count):
        for n in range(count):
            concepts = [ConceptFactory() for _ in range(4)]
            for concept in concepts:
                GameFactory(concept=concept, title_platform=['PS5'])
            _list_with(profile, f'List {n}-{count}', concepts)

    # The queryset is materialized OUTSIDE the capture: measuring `attach_cover_games` means
    # measuring what IT costs, not what fetching its argument costs.
    build(2)
    few_lists = list(GameList.objects.owned_by(profile))
    with CaptureQueriesContext(connection) as few:
        covers.attach_cover_games(few_lists)

    build(6)
    many_lists = list(GameList.objects.owned_by(profile))
    with CaptureQueriesContext(connection) as many:
        covers.attach_cover_games(many_lists)

    assert len(few.captured_queries) == len(many.captured_queries), (
        f'{len(few.captured_queries)} queries for 2 lists, {len(many.captured_queries)} for 8'
    )
    # Equality alone would hold at a constant fifty. The contract is TWO -- one bounded item read,
    # one batched cover read -- so the number is asserted as well as its flatness.
    assert len(few.captured_queries) == 2, (
        f'expected 2 queries, got {len(few.captured_queries)}'
    )


def test_the_cover_join_does_not_drag_the_igdb_blob_along():
    """`raw_response` is the ~30 KB IGDB payload no cover template reads, and the direct trigger for
    the May 2026 web-server OOM. Every queryset that joins `igdb_match` for art has to defer it."""
    concept = ConceptFactory()
    GameFactory(concept=concept, title_platform=['PS5'])

    with CaptureQueriesContext(connection) as captured:
        covers.cover_games_for([concept.pk])

    sql = captured.captured_queries[0]['sql'].lower()
    assert 'igdb' in sql, 'the cover chain no longer joins the match at all'
    assert 'raw_response' not in sql, 'the 30 KB IGDB blob is being fetched for a cover'


# ── the mosaic ───────────────────────────────────────────────────────────────────────────────────

def test_only_the_first_four_games_become_covers():
    """The tile composes `is-1`..`is-4`. Bounded here rather than sliced in the template, because a
    template slice still fetches every item on every list."""
    profile = _hunter()
    concepts = [ConceptFactory() for _ in range(7)]
    for concept in concepts:
        GameFactory(concept=concept, title_platform=['PS5'])
    game_list = _list_with(profile, 'Long', concepts)

    covers.attach_cover_games([game_list])

    assert len(game_list.cover_items) == 4


def test_a_short_list_gets_what_it_has_rather_than_padding():
    """The mosaic lays out by how many there ARE, so a two-game list reads as composed instead of a
    four-slot grid with holes."""
    profile = _hunter()
    concepts = [ConceptFactory() for _ in range(2)]
    for concept in concepts:
        GameFactory(concept=concept, title_platform=['PS5'])
    game_list = _list_with(profile, 'Short', concepts)

    covers.attach_cover_games([game_list])

    assert len(game_list.cover_items) == 2


def test_an_empty_list_gets_an_empty_cover_set_not_a_missing_attribute():
    """The tile branches on `cover_items` to draw its placeholder, so the attribute has to exist."""
    profile = _hunter()
    game_list = svc.create_list(profile, name='Nothing yet')

    covers.attach_cover_games([game_list])

    assert game_list.cover_items == []


def test_covers_follow_list_order_not_database_order():
    """A curated list is ordered on purpose; the mosaic should show the top of it."""
    profile = _hunter()
    concepts = [ConceptFactory() for _ in range(4)]
    games = [GameFactory(concept=c, title_platform=['PS5']) for c in concepts]
    game_list = _list_with(profile, 'Ordered', concepts)

    covers.attach_cover_games([game_list])

    assert [g.pk for g in game_list.cover_items] == [g.pk for g in games]


def test_a_game_missing_its_cover_source_is_skipped_not_rendered_as_a_hole():
    """A stub concept among real ones must not leave a gap the mosaic composes around wrongly."""
    profile = _hunter()
    real = [ConceptFactory() for _ in range(2)]
    for concept in real:
        GameFactory(concept=concept, title_platform=['PS5'])
    stub = ConceptFactory()
    game_list = _list_with(profile, 'Mixed', [real[0], stub, real[1]])

    covers.attach_cover_games([game_list])

    assert len(game_list.cover_items) == 2


# -- the shape the schema actually holds ----------------------------------------------------------

def test_title_platform_is_a_list_and_the_rank_must_treat_it_as_one():
    """The root cause, pinned at the source.

    `_sort_key` read this as a scalar (`RANK.get(game.title_platform, ...)`) -- a dict lookup on a
    list, i.e. `TypeError: unhashable type: 'list'` for every row the database can actually produce.
    It 500'd all four cover surfaces: browse tiles, My Lists tiles, the detail items and the adder
    search.

    Every test in this file passed throughout, because they handed `title_platform='PS5'` -- a
    STRING, overriding the factory's correct `['PS5']` and inventing a shape the schema cannot hold.
    The factory was right and the tests overrode it.
    """
    from django.db.models import JSONField

    from trophies.models import Game

    field = Game._meta.get_field('title_platform')
    assert isinstance(field, JSONField)
    assert field.default is list


def test_a_cross_buy_game_ranks_by_its_newest_platform():
    """The case that makes `title_platform` a list in the first place: one trophy list, two
    platforms. It must rank as PS5, not fall to the unknown bucket."""
    from trophies.util_modules.constants import (PLATFORM_PRIORITY_ORDER,
                                                 platform_priority_rank)

    assert platform_priority_rank(['PS4', 'PS5']) == PLATFORM_PRIORITY_ORDER.index('PS5')
    assert platform_priority_rank(['PS5', 'PS4']) == PLATFORM_PRIORITY_ORDER.index('PS5')
    assert platform_priority_rank(['PS3']) == PLATFORM_PRIORITY_ORDER.index('PS3')
    # Neither of these can be ranked, and neither may raise.
    assert platform_priority_rank([]) == len(PLATFORM_PRIORITY_ORDER)
    assert platform_priority_rank(None) == len(PLATFORM_PRIORITY_ORDER)
    assert platform_priority_rank(['PSVR3000']) == len(PLATFORM_PRIORITY_ORDER)


def test_a_cross_buy_stack_wins_the_cover_over_an_older_single_platform_list():
    """End to end through `cover_games_for`, not just the rank function: a PS4+PS5 cross-buy list
    represents the concept over a PS3-only one."""
    concept = ConceptFactory(unified_title='Cross Buy')
    GameFactory(concept=concept, title_platform=['PS3'])
    cross_buy = GameFactory(concept=concept, title_platform=['PS4', 'PS5'])

    picked = covers.cover_games_for([concept.pk])

    assert picked[concept.pk].pk == cross_buy.pk


def test_the_factory_refuses_a_scalar_platform():
    """The guard that closes this class, tested rather than assumed.

    `test_title_platform_is_a_list_...` above pins the SCHEMA, and the schema was never wrong. What
    needed pinning is what tests WRITE into the field: 22 call sites wrote a string, jsonb stored it
    without complaint, and the suite stayed green through a 500 on four surfaces. 21 were literals
    and a text sweep caught them; the 22nd passed a loop variable and survived it.
    """
    with pytest.raises(TypeError, match='JSONField'):
        GameFactory(title_platform='PS5')

    # The real shapes all still work, including the empty case and the untouched default.
    assert GameFactory(title_platform=['PS5']).title_platform == ['PS5']
    assert GameFactory(title_platform=['PS4', 'PS5']).title_platform == ['PS4', 'PS5']
    assert GameFactory(title_platform=[]).title_platform == []
    assert GameFactory().title_platform == ['PS5']
