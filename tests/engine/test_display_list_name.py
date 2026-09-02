"""The list label helper: what a trophy list is CALLED on the Games/Trophy Lists surfaces.

`Game.title_name` cannot be the label -- save() cleans it, syncs rewrite it, and the IGDB CJK
promotion replaces-and-locks it. The chain here prefers what PSN actually said (the observation
table's raw name, display-cleaned at this boundary) and falls back to title_name. The Latin-script
window rule exists solely to stop a dual-region list's label flapping between JP and US syncs;
these tests pin that rule's edges, because "most recent" is exactly the version that flaps.
"""
from datetime import timedelta

import pytest
from django.utils import timezone

from tests.factories import GameFactory
from trophies.models import Game, PSNTitleObservation, _pick_observed_name

pytestmark = pytest.mark.django_db


def _observe(game, raw_name, seen_days_ago=0, source='trophy_titles'):
    row = PSNTitleObservation.objects.create(
        np_communication_id=game.np_communication_id, game=game, source=source,
        title_name_raw=raw_name, content_hash=f'{game.np_communication_id}:{raw_name}:{source}',
    )
    if seen_days_ago:
        PSNTitleObservation.objects.filter(pk=row.pk).update(
            last_seen_at=timezone.now() - timedelta(days=seen_days_ago),
        )
    return row


# --- the picker (pure function) ------------------------------------------------------------------

def _pick(pairs):
    now = timezone.now()
    return _pick_observed_name(
        [(name, now - timedelta(days=days)) for name, days in pairs], now,
    )


def test_two_recent_names_prefer_the_latin_one_even_when_older():
    """THE flap rule: a JP sync yesterday must not relabel a list its US players know by the
    English name. Freshness loses to script inside the window."""
    assert _pick([('ゴースト・オブ・ツシマ', 1), ('Ghost of Tsushima', 5)]) == 'Ghost of Tsushima'


def test_a_single_recent_name_wins_as_is_even_when_cjk():
    """One recent name cannot flap; a JP-only list is legitimately labeled in Japanese."""
    assert _pick([('ゴースト・オブ・ツシマ', 1), ('Ghost of Tsushima', 45)]) == 'ゴースト・オブ・ツシマ'


def test_all_recent_cjk_takes_the_freshest():
    assert _pick([('イース', 2), ('イースIX', 1)]) == 'イースIX'


def test_all_stale_takes_the_freshest_overall():
    assert _pick([('Old Name', 90), ('Older Name', 200)]) == 'Old Name'


def test_no_observations_returns_none():
    assert _pick([]) is None


# --- the batch + property ------------------------------------------------------------------------

def test_observed_raw_name_beats_title_name_and_is_display_cleaned():
    """The raw table keeps the (TM); the page does not. Cleaning happens at this boundary, not in
    the table -- storing what PSN said and showing a polished label are different jobs."""
    game = GameFactory(title_name='Locked IGDB Name')
    _observe(game, 'Ghost of Tsushima™')

    assert game.display_list_name == 'Ghost of Tsushima'


def test_no_observation_falls_back_to_title_name():
    game = GameFactory(title_name='Stray')

    assert game.display_list_name == 'Stray'


def test_title_stats_observations_are_not_labels():
    """title_stats' name is a different rendering of the game name (no list suffixes); only the
    trophy_titles source describes the LIST."""
    game = GameFactory(title_name='Stored Name')
    _observe(game, 'Stats Name', source='title_stats')

    assert game.display_list_name == 'Stored Name'


def test_cleaning_that_empties_the_name_falls_back():
    """A raw name that is NOTHING BUT a stripped suffix must not produce an empty label."""
    game = GameFactory(title_name='Real Name')
    _observe(game, '™')

    assert game.display_list_name == 'Real Name'


def test_batch_returns_an_entry_for_every_game_in_one_query():
    """Grid surfaces must use the batch: the observation table's only index leads on
    np_communication_id, so per-row properties would N+1 it."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    observed = GameFactory(np_communication_id='NPWR10001_00')
    _observe(observed, 'Observed Name™')
    bare = GameFactory(np_communication_id='NPWR10002_00', title_name='Bare Name')

    with CaptureQueriesContext(connection) as ctx:
        names = Game.display_list_names([observed, bare])

    assert names == {'NPWR10001_00': 'Observed Name', 'NPWR10002_00': 'Bare Name'}
    assert len(ctx) == 1, f'the batch must be exactly one query, used {len(ctx)}'
