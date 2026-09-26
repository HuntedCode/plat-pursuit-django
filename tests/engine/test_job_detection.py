"""Tests for the shared job detection (genre/theme -> job slugs).

The single source feeding Contract job suggestions + the report_job_assignment
analysis. Pure-function tests need no DB; suggest_job_slugs() hits ConceptGenre/Theme.
"""
import pytest

from trophies.services.job_detection import assign_job_slugs, suggest_job_slugs


# --- assign_job_slugs (pure) ---

def test_combo_overrides_base():
    assert assign_job_slugs({'Role-playing (RPG)'}, {'Fantasy'}) == {'mage'}        # not champion
    assert assign_job_slugs({'Shooter'}, {'Science fiction'}) == {'vanguard'}       # not gunslinger


def test_plain_genre_jobs():
    assert assign_job_slugs({'Role-playing (RPG)'}, set()) == {'champion'}
    assert assign_job_slugs({'Shooter'}, {'Fantasy'}) == {'gunslinger'}             # Fantasy != Sci-fi


def test_theme_and_multi_genre():
    assert assign_job_slugs({'Shooter', 'Platform'}, {'Stealth'}) == {'gunslinger', 'pathfinder', 'infiltrator'}


def test_merged_tactician():
    assert assign_job_slugs({'Turn-based strategy (TBS)'}, set()) == {'tactician'}
    assert assign_job_slugs({'MOBA'}, set()) == {'tactician'}


def test_open_world_partitions_on_combat_genre():
    assert assign_job_slugs({'Shooter'}, {'Open world'}) == {'gunslinger', 'outlaw'}
    assert assign_job_slugs({'Role-playing (RPG)'}, {'Open world'}) == {'champion', 'cartographer'}


def test_comedy_partitions_on_platform():
    assert assign_job_slugs({'Platform'}, {'Comedy'}) == {'pathfinder', 'mascot'}
    assert assign_job_slugs({'Puzzle'}, {'Comedy'}) == {'mastermind', 'jester'}


def test_freelancer_fallback():
    assert assign_job_slugs({'Adventure'}, {'Action'}) == {'freelancer'}
    assert 'freelancer' not in assign_job_slugs({'Shooter'}, set())


# --- suggest_job_slugs (pools concept genres/themes) ---

@pytest.mark.django_db
def test_suggest_pools_concept_genres_and_themes():
    from trophies.models import ConceptGenre, ConceptTheme, Genre, Theme
    from tests.factories import ConceptFactory
    c = ConceptFactory()
    ConceptGenre.objects.create(concept=c, genre=Genre.objects.create(igdb_id=1, name='Role-playing (RPG)', slug='rpg'))
    ConceptTheme.objects.create(concept=c, theme=Theme.objects.create(igdb_id=1, name='Fantasy', slug='fantasy'))

    assert suggest_job_slugs([c.id]) == {'mage'}   # RPG + Fantasy -> Mage combo


@pytest.mark.django_db
def test_suggest_empty_for_no_concepts():
    assert suggest_job_slugs([]) == set()


# --- pool_tags / flatten_tags (the shared batched pooling) ---

@pytest.mark.django_db
def test_pool_tags_returns_plain_dicts_keyed_per_concept():
    """The batched primitive behind suggest_job_slugs, simulate_stage_jobs and the drift scanner.

    Pins the two properties its callers rely on: PER-CONCEPT keys (a flat set would lose which
    game contributed what, which is what the per-group flatten needs) and PLAIN dicts, so a
    missing concept raises on `d[cid]` instead of minting an entry.
    """
    from trophies.models import ConceptGenre, ConceptTheme, Genre, Theme
    from trophies.services.job_detection import pool_tags
    from tests.factories import ConceptFactory

    a, b, untagged = ConceptFactory(), ConceptFactory(), ConceptFactory()
    rpg = Genre.objects.create(igdb_id=9101, name='Role-playing (RPG)', slug='pt-rpg')
    shooter = Genre.objects.create(igdb_id=9102, name='Shooter', slug='pt-shooter')
    fantasy = Theme.objects.create(igdb_id=9103, name='Fantasy', slug='pt-fantasy')
    ConceptGenre.objects.create(concept=a, genre=rpg)
    ConceptGenre.objects.create(concept=b, genre=shooter)
    ConceptTheme.objects.create(concept=a, theme=fantasy)

    genres, themes = pool_tags([a.id, b.id, untagged.id])

    assert genres == {a.id: {'Role-playing (RPG)'}, b.id: {'Shooter'}}
    assert themes == {a.id: {'Fantasy'}}
    assert untagged.id not in genres and untagged.id not in themes   # absent, not an empty set
    with pytest.raises(KeyError):
        genres[untagged.id]          # a plain dict, not a defaultdict


@pytest.mark.django_db
def test_pool_tags_is_empty_and_query_free_for_no_concepts():
    from django.db import connection
    from django.test.utils import CaptureQueriesContext
    from trophies.services.job_detection import pool_tags

    with CaptureQueriesContext(connection) as ctx:
        assert pool_tags([]) == ({}, {})
    assert len(ctx) == 0


def test_flatten_tags_unions_a_group_and_tolerates_missing_concepts():
    from trophies.services.job_detection import flatten_tags

    genres = {1: {'Shooter'}, 2: {'Platform'}}
    themes = {1: {'Stealth'}}
    # Concept 3 has no entry at all -- the case `.get(cid, set())` exists for.
    assert flatten_tags([1, 2, 3], genres, themes) == ({'Shooter', 'Platform'}, {'Stealth'})
    assert flatten_tags([], genres, themes) == (set(), set())


@pytest.mark.django_db
def test_suggest_job_slugs_still_costs_two_queries_after_the_extraction():
    """The extraction pools per-concept instead of flat; it must not have added a query."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext
    from trophies.models import ConceptGenre, Genre
    from tests.factories import ConceptFactory

    c = ConceptFactory()
    ConceptGenre.objects.create(
        concept=c, genre=Genre.objects.create(igdb_id=9110, name='Racing', slug='pt-racing'))

    with CaptureQueriesContext(connection) as ctx:
        assert suggest_job_slugs([c.id]) == {'driver'}
    assert len(ctx) == 2
