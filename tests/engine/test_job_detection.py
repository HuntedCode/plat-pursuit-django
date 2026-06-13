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
