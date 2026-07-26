"""Tests for the rebuilt game-detail Ratings tab (Phase 4).

Pins:
  - `rating_tone` filter: the per-stat tone thresholds (shared verbatim with the live-update JS in
    game-detail.js -- if these move, the two must move together).
  - `_rating_bars.html`: pp-horizon bars carry their tone + fill target; the quick-rate button preserves
    the data-* contract game-detail.js reads; the empty state keeps the layout.
  - The Ratings tab render: the tab was renamed from Community, the stats strip surfaces the four denormed
    Game numbers (with the platinum tile gated on the game actually having a platinum), and the DLC selector
    is adaptive (pills for a few groups, a dropdown once there are many).
"""
import pytest
from django.template.loader import render_to_string
from django.urls import reverse

from core.templatetags.custom_filters import rating_tone
from tests.factories import ConceptFactory, ConceptTrophyGroupFactory, GameFactory

pytestmark = pytest.mark.django_db

_DEFINED = {'bronze': 10, 'silver': 5, 'gold': 2, 'platinum': 1}
_NO_PLAT = {'bronze': 10, 'silver': 5, 'gold': 2}


# ── rating_tone (thresholds mirrored by game-detail.js toneOf) ──────────────

@pytest.mark.parametrize('kind,value,expected', [
    # difficulty / grindiness: LOW is good (easier / less grindy).
    ('difficulty', 3.9, 'good'), ('difficulty', 4, 'warn'), ('difficulty', 7.9, 'warn'), ('difficulty', 8, 'bad'),
    ('grindiness', 1, 'good'), ('grindiness', 6, 'warn'), ('grindiness', 9, 'bad'),
    # hours: four bands, an extra "high" (accent) tier near a very long plat.
    ('hours', 24, 'good'), ('hours', 25, 'warn'), ('hours', 74, 'warn'), ('hours', 80, 'high'), ('hours', 100, 'bad'),
    # fun /10 and overall /5: HIGH is good (opposite polarity), different midpoints.
    ('fun', 3, 'bad'), ('fun', 6, 'warn'), ('fun', 9, 'good'),
    ('overall', 1.5, 'bad'), ('overall', 3, 'warn'), ('overall', 4.5, 'good'),
])
def test_rating_tone_thresholds(kind, value, expected):
    assert rating_tone(value, kind) == expected


def test_rating_tone_non_numeric_is_neutral_good():
    assert rating_tone(None, 'difficulty') == 'good'
    assert rating_tone('', 'fun') == 'good'


# ── _rating_bars.html ──────────────────────────────────────────────────────

_AVERAGES = {
    'avg_difficulty': 7.0, 'avg_grindiness': 2.0, 'avg_hours': 40.0,
    'avg_fun': 9.0, 'avg_rating': 4.5, 'count': 12,
}


def _bars(averages, **kw):
    ctx = {
        'averages': averages, 'concept_id': 1, 'group_id': 'default',
        'hours_label': 'Hours to Plat', 'hours_label_long': 'Hours to Platinum',
    }
    ctx.update(kw)
    return render_to_string('trophies/partials/game_detail/_rating_bars.html', ctx)


def test_rating_bars_render_tone_and_fill():
    html = _bars(_AVERAGES)
    # Difficulty 7.0 -> warn tone, fill 70 (7/10). Grindiness 2.0 -> good, fill 20. Overall 4.5 -> good, 90.
    assert 'data-stat="difficulty" data-tone="warn"' in html
    assert 'data-stat="grindiness" data-tone="good"' in html
    assert 'data-gd-fill="70"' in html          # difficulty
    assert 'data-gd-fill="20"' in html          # grindiness
    assert 'data-gd-fill="90"' in html          # overall 4.5/5
    assert 'pp-horizon__fill' in html
    assert 'Based on 12 community ratings.' in html


def test_rating_bars_hours_bar_caps_at_100():
    html = _bars({**_AVERAGES, 'avg_hours': 250.0})
    assert 'data-tone="bad"' in html            # 250h -> bad
    assert 'data-gd-fill="100"' in html         # capped, not 250


def test_rating_bars_empty_state_keeps_layout():
    html = _bars(None)
    assert 'gd-rate__bars--empty' in html
    assert html.count('data-stat-value') == 5   # all five rows still present
    assert 'data-tone=' not in html             # neutral (no tone) until rated
    assert 'No community ratings yet' in html


def test_rating_bars_quick_rate_button_contract():
    """The quick-rate button preserves the data-* game-detail.js reads (concept/group/hours + existing JSON)."""
    user_rating = type('R', (), {'difficulty': 8, 'grindiness': 3, 'hours_to_platinum': 55, 'fun_ranking': 9, 'overall_rating': 4.5})()
    html = _bars(_AVERAGES, can_rate=True, user_rating=user_rating, concept_id=42, group_id='001',
                 hours_label_long='Hours to 100%')
    assert 'quick-rate-btn' in html
    assert 'data-concept-id="42"' in html
    assert 'data-group-id="001"' in html
    assert 'data-hours-label="Hours to 100%"' in html
    assert '"difficulty":8' in html and '"hours_to_platinum":55' in html   # data-existing prefill
    assert 'Update rating' in html


def test_rating_bars_no_button_when_cannot_rate():
    assert 'quick-rate-btn' not in _bars(_AVERAGES, can_rate=False)


# ── Full page: tab rename + stats strip + adaptive selector ─────────────────

def _detail(client, game):
    url = reverse('game_detail', kwargs={'np_communication_id': game.np_communication_id})
    return client.get(url).content.decode()


def test_ratings_tab_replaced_community(client):
    """The tab is now Ratings, not Community: the panel id / label / hero jump all moved."""
    content = _detail(client, GameFactory(defined_trophies=_DEFINED, played_count=100))
    assert 'id="gd-view-ratings"' in content
    assert 'data-view="ratings"' in content
    assert '<span class="pp-switch__lbl">Ratings</span>' in content
    assert 'data-gd-goto="ratings"' in content          # hero "Players" jump retargeted
    assert 'gd-view-community' not in content
    assert '<span class="pp-switch__lbl">Community</span>' not in content


def test_ratings_stats_strip_surfaces_denormed_numbers(client):
    content = _detail(client, GameFactory(
        defined_trophies=_DEFINED, played_count=1234, plats_earned_count=56,
        full_completion_count=78, avg_completion=63.4,
    ))
    assert 'gd-rate__stats' in content
    assert 'data-gd-countup="1234"' in content    # players
    assert 'data-gd-countup="56"' in content      # platinums (game has a plat)
    assert 'data-gd-countup="78"' in content      # 100% club
    assert 'data-gd-countup="63"' in content      # avg completion (floored), % unit alongside
    assert '100% Club' in content


def test_ratings_platinum_tile_gated_on_platinum(client):
    """A game with no platinum hides the Platinums tile (it would always read 0)."""
    content = _detail(client, GameFactory(defined_trophies=_NO_PLAT, plats_earned_count=0, played_count=9))
    assert 'gd-rate__stats' in content
    assert 'Platinums' not in content
    assert 'Players' in content


def test_ratings_selector_pills_for_few_groups(client):
    concept = ConceptFactory()
    ConceptTrophyGroupFactory(concept=concept, trophy_group_id='default', display_name='Base Game')
    ConceptTrophyGroupFactory(concept=concept, trophy_group_id='001', display_name='DLC One')
    content = _detail(client, GameFactory(concept=concept, defined_trophies=_DEFINED))
    assert 'gd-rate__segchip' in content
    assert 'data-rate-ctg=' in content
    assert 'gd-rate__dropmenu' not in content     # few groups -> pills, no dropdown


def test_ratings_selector_dropdown_for_many_groups(client):
    concept = ConceptFactory()
    ConceptTrophyGroupFactory(concept=concept, trophy_group_id='default', display_name='Base Game')
    for i in range(1, 6):                          # 5 DLC + base = 6 groups (> 4) -> dropdown
        ConceptTrophyGroupFactory(concept=concept, trophy_group_id=f'{i:03d}', display_name=f'DLC {i}')
    content = _detail(client, GameFactory(concept=concept, defined_trophies=_DEFINED))
    assert 'gd-rate__dropmenu' in content
    assert 'data-rate-drop-toggle' in content
    assert 'gd-rate__dropitem' in content


def test_ratings_no_selector_for_base_game_only(client):
    concept = ConceptFactory()
    ConceptTrophyGroupFactory(concept=concept, trophy_group_id='default', display_name='Base Game')
    content = _detail(client, GameFactory(concept=concept, defined_trophies=_DEFINED))
    assert 'gd-rate__seg' not in content          # one group -> no selector at all


def test_quick_rate_modal_form_contract(client):
    """The modal ships the five inputs the rating API expects, by their exact names."""
    content = _detail(client, GameFactory(defined_trophies=_DEFINED))
    assert 'id="gd-qr-modal"' in content
    for name in ('difficulty', 'grindiness', 'hours_to_platinum', 'fun_ranking', 'overall_rating'):
        assert f'name="{name}"' in content
