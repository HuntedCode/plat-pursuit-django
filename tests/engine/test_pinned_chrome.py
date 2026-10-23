"""The page bar names the trophy group under it on both list surfaces (the laptop review, 2026-10-23).

The behaviour runs in a browser (test_pinned_chrome_browser.py); this pins what each page serves: the shared group
label in its bar, gated to the view that shows the grid, and the page title marked as the one the label stands in for.
"""
import pytest

from tests.factories import ConceptFactory, GameFactory, TrophyFactory, TrophyGroupFactory
from trophies.models import IGDBMatch

pytestmark = pytest.mark.django_db

DEFINED = {'bronze': 2, 'gold': 1, 'platinum': 1}


def _bar(html):
    start = html.index('class="pp-minibar gd-minibar"')
    return html[start:html.index('class="pp-minibar__controls"', start)]


def _list_game(**extra):
    game = GameFactory(title_name='Bar List', title_platform=['PS4'], defined_trophies=DEFINED, **extra)
    TrophyGroupFactory(game=game, trophy_group_id='default', defined_trophies=DEFINED)
    TrophyFactory(game=game, trophy_type='bronze')
    return game


def test_the_list_page_bar_carries_the_group_label(client):
    game = _list_game()
    bar = _bar(client.get(f'/games/{game.np_communication_id}/').content.decode())
    assert 'data-minibar-group data-mb-only="trophies" hidden' in bar
    assert 'data-minibar-page-title>Bar List<' in bar
    # The label took the shown-trophies count's place; the control row still shows the count.
    assert 'data-minibar-count' not in bar


def test_the_game_page_bar_carries_the_group_label(client):
    concept = ConceptFactory()
    IGDBMatch.objects.create(concept=concept, igdb_id=980101, status='accepted')
    _list_game(concept=concept)
    bar = _bar(client.get('/games/980101/').content.decode())
    assert 'data-minibar-group data-mb-only="lists" hidden' in bar
    assert 'data-minibar-page-title>' in bar


def test_the_label_partial_says_nothing_out_loud(client):
    """Its notes are a {% comment %} block: a two-line {# #} renders as text (it did, in the bar, on the first cut)."""
    game = _list_game()
    html = client.get(f'/games/{game.np_communication_id}/').content.decode()
    assert 'the group headers no longer pin' not in html
    assert '{#' not in _bar(html)
