"""Cover art for a browse tile: bounded, flat, and honest about the open grid.

The query-count tests here are the point. A cover helper that looks like a field and runs a query per
call is what took the list browse to 23 queries for 20 rows, and the shape is invisible in a
functional test -- every assertion passes either way.
"""
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from prompts.models import MIN_GAMES_TO_PUBLISH, SHAPE_GRID, SHAPE_TIER, Prompt
from prompts.services import prompt_service as svc
from prompts.services.covers import COVERS_PER_TILE, attach_cover_games
from tests.factories import ConceptFactory, GameFactory, ProfileFactory

pytestmark = pytest.mark.django_db


def _hunter(psn='hunter'):
    return ProfileFactory(is_linked=True, psn_username=psn)


def _with_pool(owner, games, *, title='Rank them'):
    prompt = svc.create_prompt(owner, shape=SHAPE_TIER, title=title)
    for _ in range(games):
        concept = ConceptFactory()
        GameFactory(concept=concept)
        svc.add_concept(prompt, owner, concept)
    return prompt


def _queries(prompts):
    with CaptureQueriesContext(connection) as ctx:
        attach_cover_games(prompts)
    return len(ctx.captured_queries)


def test_a_tile_shows_the_first_few_of_the_pool_in_order():
    owner = _hunter()
    prompt = _with_pool(owner, MIN_GAMES_TO_PUBLISH[SHAPE_TIER] + 2)

    attach_cover_games([prompt])

    assert len(prompt.cover_items) == COVERS_PER_TILE
    expected = list(prompt.games.order_by('position')
                    .values_list('concept_id', flat=True)[:COVERS_PER_TILE])
    assert [g.concept_id for g in prompt.cover_items] == expected


def test_the_query_count_does_not_move_with_the_page_size():
    """TWO QUERIES FOR THE WHOLE PAGE, whatever is on it. Compared as two sizes rather than against a
    magic number, which is the shape the list browse's own flatness tests use."""
    owner = _hunter()
    few = [_with_pool(owner, 4, title=f'Few {i}') for i in range(2)]
    many = [_with_pool(_hunter(f'other-{i}'), 4, title=f'Many {i}') for i in range(10)]

    assert _queries(few) == _queries(many)


def test_the_query_count_does_not_move_with_the_pool_size():
    """The slice is bounded by `position__lt`, so a 200-game prompt costs what a 4-game one does."""
    owner = _hunter()
    small = [_with_pool(owner, 4, title='Small')]
    large = [_with_pool(_hunter('large'), 30, title='Large')]

    assert _queries(small) == _queries(large)


def test_an_open_grid_has_no_mosaic_and_that_is_a_state():
    """A prompt need not have a pool at all. Reaching into its ANSWERS for art would be a per-viewer
    aggregate on a browse page, which is the whale rule's exact shape -- so the tile renders empty and
    the template treats that as a state rather than as missing data."""
    owner = _hunter()
    grid = svc.create_prompt(owner, shape=SHAPE_GRID, title='Open')
    svc.create_bucket(grid, owner, label='Best combat')

    attach_cover_games([grid])

    assert grid.cover_items == []


def test_a_game_with_no_trophy_list_is_simply_absent():
    """`cover_games_for` omits a concept it cannot cover, and the caller renders a placeholder for the
    gap rather than checking None -- that function's stated contract, relied on here."""
    owner = _hunter()
    prompt = svc.create_prompt(owner, shape=SHAPE_TIER, title='Mixed')
    covered = ConceptFactory()
    GameFactory(concept=covered)
    svc.add_concept(prompt, owner, covered)
    svc.add_concept(prompt, owner, ConceptFactory())      # no Game behind it

    attach_cover_games([prompt])

    assert [g.concept_id for g in prompt.cover_items] == [covered.pk]


def test_an_empty_page_costs_nothing():
    with CaptureQueriesContext(connection) as ctx:
        assert attach_cover_games([]) == []
    assert len(ctx.captured_queries) == 0


def test_the_cover_query_never_drags_the_igdb_blob_along():
    """`raw_response` is the ~30 KB IGDB payload behind the May 2026 web-server OOM, and no cover
    render reads it. A byte-size regression no query count can see, so it is asserted against the SQL
    text -- the same pin the list covers carry."""
    owner = _hunter()
    prompts = [_with_pool(owner, 4, title='Blob')]

    with CaptureQueriesContext(connection) as ctx:
        attach_cover_games(prompts)

    for query in ctx.captured_queries:
        if 'igdb' in query['sql'].lower():
            assert 'raw_response' not in query['sql'], (
                'the cover prefetch is carrying the IGDB blob'
            )
