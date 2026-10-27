"""The browse-page stats that ride the hourly site heartbeat (2026-08 consolidation): the Genres & Themes
switcher counts. They were request-path computes before (the tag counts two hot DISTINCT joins on every
request AND swap of /genres/); the heartbeat is their only compute site and the view is a pure cache read.

The six browse LIST pages' tile grids were removed by the browse pass (DS-04, 2026-10-22), the Jobs
header's by the Progression pass (PR-02, 2026-10-24), and the Hunters header's by the Identity pass
(ID-05, 2026-10-26; `test_identity_review.py`). The Hunters values stay in the heartbeat: the home ribbon
reads them.
"""
import pytest

from core.services.site_heartbeat import compute_site_heartbeat
from tests.factories import (
    ConceptFactory,
    ConceptGenreFactory,
    GameFactory,
    GenreFactory,
)
from trophies.models import Theme

pytestmark = pytest.mark.django_db


def test_genre_theme_index_counts():
    """genres/themes_with_games: only tags that actually carry at least one Game count."""
    tagged = GenreFactory(name='Racing', slug='racing')
    ConceptGenreFactory(concept=GameFactory(title_platform=['PS5']).concept, genre=tagged)
    GenreFactory(name='Empty Genre', slug='empty-genre')                    # no games -> not counted
    Theme.objects.create(igdb_id=901, name='Gameless Theme', slug='gameless-theme')      # no games -> not counted
    orphan = Theme.objects.create(igdb_id=902, name='Conceptless', slug='conceptless')
    orphan.theme_concepts.create(concept=ConceptFactory())                  # concept but no Game rows

    exp = compute_site_heartbeat()['expanded']

    assert exp['genres_with_games']['value'] == 1
    assert exp['themes_with_games']['value'] == 0


def test_a_failed_stat_flags_partial_not_fatal(monkeypatch):
    """The heartbeat's resilience contract holds for the new blocks: a failing query Nones the
    block and flips meta.is_partial instead of sinking the whole cron payload."""
    from trophies import models as trophy_models

    class _Boom:
        def __get__(self, obj, objtype=None):
            raise RuntimeError('genre table on fire')

    monkeypatch.setattr(trophy_models.Genre, 'objects', _Boom())

    data = compute_site_heartbeat()

    assert data['meta']['is_partial'] is True
    assert data['expanded']['genres_with_games']['value'] is None
    assert data['expanded']['themes_with_games']['value'] is None
    # The neighbor block is untouched by the failure.
    assert data['expanded']['ratings_total']['value'] == 0
