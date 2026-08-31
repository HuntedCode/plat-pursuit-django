"""The browse-header stats that ride the hourly site heartbeat (2026-08 consolidation): the
Trophy Lists catalogue block and the Genres & Themes index counts. Both were request-path
computes before -- the lists block a lazy cache.get_or_set (its with-a-platinum EXISTS semi-join
belongs on the cron), the tag counts two hot DISTINCT joins on every request AND swap of
/genres/. The heartbeat is now their only compute site; the views are pure cache reads.
"""
import pytest

from core.services.site_heartbeat import compute_site_heartbeat
from tests.factories import (
    ConceptFactory,
    ConceptGenreFactory,
    GameFactory,
    GenreFactory,
    TrophyFactory,
)
from trophies.models import Theme

pytestmark = pytest.mark.django_db


def test_lists_catalogue_block():
    """The four Trophy Lists scard values: np-floored totals, regional requiring BOTH the flag
    AND a region (check_and_mark_regional can set the flag before region detection lands),
    platinum coverage, and the 7-day delta riding lists_total."""
    plat = GameFactory(title_platform=['PS5'])
    TrophyFactory(game=plat, trophy_type='platinum')
    GameFactory(title_platform=['PS5'], is_regional=True, region=['JP'])
    GameFactory(title_platform=['PS5'], is_regional=True)          # flag only -> NOT regional
    GameFactory(title_platform=['PS5'], np_communication_id=None)  # below the np floor

    exp = compute_site_heartbeat()['expanded']

    assert exp['lists_total']['value'] == 3
    assert exp['lists_total']['delta'] == 3        # all created just now -> within 7 days
    assert exp['lists_regional']['value'] == 1
    assert exp['lists_with_plat']['value'] == 1


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
    assert data['expanded']['lists_total']['value'] == 0
