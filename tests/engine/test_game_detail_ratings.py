"""Tests for the rebuilt game-detail Ratings tab (Phase 4).

Pins:
  - `rating_tone` filter: the per-stat tone thresholds (shared verbatim with the live-update JS in
    game-detail.js -- if these move, the two must move together).
  - `rating_verdict` / `rating_summary` filters: the per-stat word and the synthesized one-line sentence
    (both mirrored by game-detail.js verdictOf / summaryOf).
  - `_rating_conditions.html`: the summary headline + star score, the icon word-tiles (verdict + tone +
    number), the empty state, and the preserved quick-rate data-* contract.
  - The Ratings tab render: the tab was renamed from Community, the stats strip surfaces the four denormed
    Game numbers (with the platinum tile gated on the game actually having a platinum), and the DLC selector
    is adaptive (pills for a few groups, a dropdown once there are many).
"""
import pytest
from django.template.loader import render_to_string
from django.urls import reverse

from core.templatetags.custom_filters import rating_tone, rating_verdict, rating_summary
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


# ── rating_verdict (plain-language words, mirrored by game-detail.js verdictOf) ──

@pytest.mark.parametrize('kind,value,expected', [
    ('difficulty', 2, 'A breeze'), ('difficulty', 6, 'Tough'), ('difficulty', 9, 'Brutal'),
    ('grindiness', 1, 'Breezy'), ('grindiness', 6, 'Grindy'), ('grindiness', 9, 'A slog'),
    ('fun', 2, 'A chore'), ('fun', 6, 'Fun'), ('fun', 9, 'A blast'),
    ('overall', 1.5, 'Rough'), ('overall', 2.5, 'Mixed'), ('overall', 3.5, 'Solid'),
    ('overall', 4.2, 'Great'), ('overall', 4.8, 'Beloved'),
])
def test_rating_verdict_words(kind, value, expected):
    assert rating_verdict(value, kind) == expected


def test_rating_verdict_non_numeric_is_blank():
    assert rating_verdict(None, 'overall') == ''


# ── rating_summary (the synthesized one-line sentence; mirrored by game-detail.js summaryOf) ──

def test_rating_summary_hard_but_fun_uses_but():
    """Hard/grindy YET fun flips the final conjunction to 'but' (the contrast is the whole point)."""
    s = rating_summary({'avg_difficulty': 8.0, 'avg_grindiness': 6.5, 'avg_fun': 9.0})
    assert s == 'Brutally hard, a real grind, but a blast to platinum.'


def test_rating_summary_easy_and_fun_uses_and():
    s = rating_summary({'avg_difficulty': 2.0, 'avg_grindiness': 2.0, 'avg_fun': 8.0})
    assert s == 'A breeze, not grindy, and a blast to platinum.'


def test_rating_summary_hard_and_unfun_uses_and():
    s = rating_summary({'avg_difficulty': 8.0, 'avg_grindiness': 8.0, 'avg_fun': 2.0})
    assert s == 'Brutally hard, a serious slog, and a chore.'


def test_rating_summary_blank_without_data():
    assert rating_summary(None) == ''
    assert rating_summary({'avg_difficulty': 5.0}) == ''   # missing grind/fun


# ── _rating_conditions.html (summary headline + icon word-tiles) ────────────

_AVERAGES = {
    'avg_difficulty': 7.0, 'avg_grindiness': 2.0, 'avg_hours': 40.0,
    'avg_fun': 9.0, 'avg_rating': 4.5, 'count': 12,
}


def _conditions(averages, **kw):
    ctx = {
        'averages': averages, 'concept_id': 1, 'group_id': 'default',
        'hours_label': 'Hours to Plat', 'hours_label_long': 'Hours to Platinum',
    }
    ctx.update(kw)
    return render_to_string('trophies/partials/game_detail/_rating_conditions.html', ctx)


def test_conditions_render_summary_verdicts_and_score():
    html = _conditions(_AVERAGES)
    # Summary from difficulty 7.0 (Tough) / grind 2.0 (not grindy) / fun 9.0 (a blast); fun despite hard -> "but".
    assert 'Tough, not grindy, but a blast to platinum.' in html
    # Tiles: verdict word is the headline, tone tints, number is the subscript.
    assert 'data-stat="difficulty" data-tone="warn"' in html
    assert 'data-stat="grindiness" data-tone="good"' in html
    assert '>Tough<' in html and '>Breezy<' in html and '>A blast<' in html
    assert '>7.0<' in html and '>2.0<' in html and '>9.0<' in html   # quiet number subscripts
    assert '4.5' in html and '12 rating' in html                      # star score + count
    assert '--fill: 90%' in html                                      # gold stars filled to 4.5/5
    assert 'gd-cond--empty' not in html
    assert 'gd-cond__tile' in html and 'pp-horizon' not in html       # tiles, not a bar chart


def test_conditions_empty_state_keeps_structure():
    html = _conditions(None)
    assert 'gd-cond--empty' in html
    assert 'Not yet rated' in html
    assert html.count('gd-cond__verdict') == 3  # all three tiles present, muted
    assert 'data-tone=' not in html             # neutral until rated


def test_conditions_quick_rate_button_contract():
    """The quick-rate button preserves the data-* game-detail.js reads (concept/group/hours + existing JSON)."""
    user_rating = type('R', (), {'difficulty': 8, 'grindiness': 3, 'hours_to_platinum': 55, 'fun_ranking': 9, 'overall_rating': 4.5})()
    html = _conditions(_AVERAGES, can_rate=True, user_rating=user_rating, concept_id=42, group_id='001',
                       hours_label_long='Hours to 100%')
    assert 'quick-rate-btn' in html
    assert 'data-concept-id="42"' in html
    assert 'data-group-id="001"' in html
    assert 'data-hours-label="Hours to 100%"' in html
    assert '"difficulty":8' in html and '"hours_to_platinum":55' in html   # data-existing prefill
    assert 'Update rating' in html


def test_conditions_no_button_when_cannot_rate():
    assert 'quick-rate-btn' not in _conditions(_AVERAGES, can_rate=False)


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
