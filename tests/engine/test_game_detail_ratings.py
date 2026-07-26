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

from core.templatetags.custom_filters import rating_tone, rating_verdict, rating_summary, rating_comparison
from trophies.models import UserConceptRating
from tests.factories import ConceptFactory, ConceptTrophyGroupFactory, GameFactory, ProfileFactory

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


# ── rating_comparison ("your take vs community", mirrored by game-detail.js comparisonOf) ──

def _rater(difficulty, grindiness, fun_ranking, overall_rating=4.0):
    return type('R', (), {'difficulty': difficulty, 'grindiness': grindiness,
                          'fun_ranking': fun_ranking, 'overall_rating': overall_rating})()


def test_rating_comparison_harder_but_fun_uses_but():
    a = {'avg_difficulty': 5.0, 'avg_grindiness': 5.0, 'avg_fun': 5.0}
    assert rating_comparison(_rater(9, 8, 9), a) == 'You found it tougher than most, grindier, but more fun.'


def test_rating_comparison_in_line_collapses():
    """All three axes within the threshold -> one clean 'in line' line, not a clunky triple 'about as'."""
    a = {'avg_difficulty': 5.2, 'avg_grindiness': 4.8, 'avg_fun': 5.3}
    assert rating_comparison(_rater(5, 5, 5), a) == 'Right in line with the community.'


def test_rating_comparison_easier_and_less_fun_uses_and():
    a = {'avg_difficulty': 6.0, 'avg_grindiness': 6.0, 'avg_fun': 6.0}
    assert rating_comparison(_rater(2, 2, 2), a) == 'You found it easier than most, less grindy, and less fun.'


def test_rating_comparison_blank_without_inputs():
    assert rating_comparison(None, {'avg_difficulty': 5}) == ''
    assert rating_comparison(_rater(5, 5, 5), None) == ''


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
    assert 'HARD FACTS' not in html                                   # template comments must not leak to the page


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


def test_verdict_group_title_shown_only_with_dlc():
    """A per-group title labels the verdict card only when the game has DLC (base-only is self-evident)."""
    dlc = _conditions(_AVERAGES, has_dlc=True, group_name='The Old Hunters')
    assert 'gd-rate__grouptitle' in dlc and 'The Old Hunters' in dlc
    base_only = _conditions(_AVERAGES, group_name='Base Game')   # has_dlc falsy -> no title
    assert 'gd-rate__grouptitle' not in base_only


# ── "Your take" personal comparison band ────────────────────────────────────

_YOU = type('R', (), {'difficulty': 9, 'grindiness': 8, 'fun_ranking': 9,
                      'overall_rating': 4.5, 'hours_to_platinum': 50, 'blurb': ''})()


def test_conditions_your_take_band_when_rated():
    html = _conditions(_AVERAGES, user_rating=_YOU)   # _AVERAGES: diff 7.0 / grind 2.0 / fun 9.0, count 12
    assert 'gd-cond__you' in html and 'Your take' in html
    assert 'You found it tougher than most, grindier, and just as fun.' in html
    assert 'community <b>4.5</b>' in html             # your score vs community, juxtaposed


def test_conditions_no_your_take_without_rating():
    assert 'gd-cond__you' not in _conditions(_AVERAGES)          # not rated -> no personal band


def test_conditions_no_your_take_when_sole_rater():
    solo = {**_AVERAGES, 'count': 1}
    assert 'gd-cond__you' not in _conditions(solo, user_rating=_YOU)   # nothing to compare against


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
    assert 'name="blurb"' in content            # the optional quick-take field
    assert 'data-gd-qr-count' in content        # + its char counter


def test_quick_rate_persistent_guidelines_notice_and_sheet(client):
    """The compose modal shows a persistent guidelines notice; the rules open in an in-context sheet."""
    content = _detail(client, GameFactory(defined_trophies=_DEFINED))
    assert 'gd-qr__notice' in content                     # persistent notice (always shown, not JS-toggled)
    assert 'data-gd-guidelines-open' in content           # its Community Guidelines trigger
    assert 'id="gd-guidelines-modal"' in content          # the in-context sheet is on the page
    assert 'Mark spoilers.' in content                    # a real rule rendered in the sheet
    assert 'data-gd-qr-fine' not in content               # the old conditional fine-print is gone


# ── Quick takes: the community blurb strip under the aggregate ──────────────

def _blurb_row(concept, profile, text, ctg=None, hidden=False):
    return UserConceptRating.objects.create(
        profile=profile, concept=concept, concept_trophy_group=ctg,
        difficulty=6, grindiness=4, hours_to_platinum=30, fun_ranking=8, overall_rating=4.5,
        blurb=text, blurb_hidden=hidden,
    )


def test_conditions_quick_takes_empty_when_no_blurbs():
    """No blurbs -> the strip renders muted+hidden (is-empty), so JS can fill it live, but shows no cards."""
    html = _conditions(_AVERAGES)
    assert 'gd-blurbs is-empty' in html
    assert 'gd-blurb__text' not in html


def test_conditions_quick_takes_flag_own_and_gate_report():
    """The viewer's own blurb gets a You pill and no report control; others' cards are reportable."""
    concept = ConceptFactory()
    mine = ProfileFactory()
    ours = _blurb_row(concept, mine, 'My own take.')
    theirs = _blurb_row(concept, ProfileFactory(), 'Great combat, brutal plat.')
    html = _conditions(_AVERAGES, blurbs=[ours, theirs], viewer_profile_id=mine.id)
    assert 'Quick takes' in html and 'is-empty' not in html
    assert 'My own take.' in html and 'Great combat, brutal plat.' in html
    assert 'gd-blurb__you' in html                              # own card flagged
    assert html.count('data-blurb-report') == 1                 # only the other card is reportable
    assert f'data-rating-id="{theirs.id}"' in html
    assert f'data-rating-id="{ours.id}"' not in html            # can't report yourself


def test_ratings_page_shows_visible_blurbs_and_hides_hidden(client):
    """The view feeds visible_blurbs(): present blurbs render; a staff-hidden one never reaches the page."""
    concept = ConceptFactory()
    ConceptTrophyGroupFactory(concept=concept, trophy_group_id='default', display_name='Base Game')
    _blurb_row(concept, ProfileFactory(), 'Shown quick take.')
    _blurb_row(concept, ProfileFactory(), 'Hidden quick take.', hidden=True)
    content = _detail(client, GameFactory(concept=concept, defined_trophies=_DEFINED))
    assert 'Quick takes' in content
    assert 'Shown quick take.' in content
    assert 'Hidden quick take.' not in content
