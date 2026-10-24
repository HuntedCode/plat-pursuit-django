"""The browse-page stats that ride the hourly site heartbeat (2026-08 consolidation): the Genres & Themes
switcher counts and the Hunters header grid. They were request-path computes before (the tag counts two
hot DISTINCT joins on every request AND swap of /genres/); the heartbeat is their only compute site and the
views are pure cache reads.

The six browse LIST pages' tile grids were removed by the browse pass (DS-04, 2026-10-22), and the Jobs
header's by the Progression pass (PR-02, 2026-10-24); the stats only they read went with them, and
`test_browse_pass.py` pins both halves.
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


# ── The page renders that still carry a grid (Hunters) ──────────────────────────────

def _warm(**expanded):
    """Write the hourly heartbeat bucket (the badge-catalog header tests' pattern)."""
    from django.core.cache import cache
    from django.utils import timezone

    now = timezone.now()
    key = f"site_heartbeat_{now.date().isoformat()}_{now.hour:02d}"
    cache.set(key, expanded, 120)
    return key


def _page_pins(client, url, payload, marker, context_key, swap_headers):
    """The uniform contract: warm heartbeat -> grid renders + context dict; cold -> gated off;
    swap -> the stats key is None/absent (header furniture never rides a partial)."""
    from django.core.cache import cache

    key = _warm(**payload)
    try:
        warm = client.get(url)
    finally:
        cache.delete(key)
    assert warm.status_code == 200
    assert marker in warm.content.decode()
    assert warm.context[context_key] is not None

    cold = client.get(url)
    assert cold.status_code == 200
    assert marker not in cold.content.decode()
    assert cold.context.get(context_key) is None

    key = _warm(**payload)
    try:
        swap = client.get(url, **swap_headers)
    finally:
        cache.delete(key)
    assert not swap.context.get(context_key)


def test_hunters_browse_grid(client):
    _page_pins(
        client, '/hunters/',
        {'always': {
            'profiles_total': {'value': 5100}, 'trophies_total': {'value': 9000000},
            'trophies_24h': {'value': 4200},
         },
         'expanded': {'platinums_total': {'value': 61000}}},
        'Hunters tracked', 'hunters_stats',
        {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'},
    )


def test_zeroed_community_stats_never_render(client):
    """The truthy gate (the audit's zero-lie catch): a failed community compute caches ZEROS
    through _community_value(default=0) -- Hunters must treat a zeroed payload as cold, not
    render 'Hunters tracked 0' for two hours."""
    from django.core.cache import cache

    key = _warm(always={
        'profiles_total': {'value': 0}, 'trophies_total': {'value': 0},
        'trophies_24h': {'value': 0}, 'games_total': {'value': 0, 'delta': 0},
    }, expanded={'platinums_total': {'value': 0}})
    try:
        hunters = client.get('/hunters/')
    finally:
        cache.delete(key)

    assert hunters.context.get('hunters_stats') is None
    assert 'Hunters tracked' not in hunters.content.decode()
