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
import re
from pathlib import Path

import pytest
from django.template.loader import render_to_string
from django.urls import reverse

from core.templatetags.custom_filters import rating_tone, rating_verdict, rating_summary, rating_comparison
from trophies.models import UserConceptRating
from tests.factories import ConceptFactory, ConceptTrophyGroupFactory, GameFactory, ProfileFactory

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[2]

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


_SPLIT = {
    'options': [
        {'value': 'worth_it', 'label': 'Do it', 'count': 4, 'pct': 80},
        {'value': 'good_game_bad_plat', 'label': 'Good game, tough plat', 'count': 1, 'pct': 20},
        {'value': 'skip', 'label': 'Skip it', 'count': 0, 'pct': 0},
    ],
    'answered': 5,
}


def test_the_split_shows_every_option_including_the_empty_ones():
    """Showing only the recommend share reports "everybody said Do it" and "most said Do it, one said
    skip" as 100% against 83%, when the dissent is often the interesting half. A zero is a fact too:
    nobody calling a platinum a slog says something about the game."""
    html = _conditions(dict(_AVERAGES, recommendation_split=_SPLIT))

    for value in ('worth_it', 'good_game_bad_plat', 'skip'):
        assert f'data-rec-cell="{value}"' in html, f'{value} is missing from the split'
    # '0%' would be satisfied by the '0%' inside '80%', so the zero share is read off its OWN cell.
    assert '80%' in html and '20%' in html
    skip = html[html.index('data-rec-cell="skip"'):]
    assert '>0%<' in skip[:skip.index('gd-cond__rec-lbl')], 'the skipped share is not printed as 0%'
    # The option nobody picked is drawn but held back -- present, not a finding.
    assert 'gd-cond__rec is-none' in html


def test_a_never_rated_game_draws_the_three_answers_at_zero():
    """`averages` is None until somebody rates the game, and looping it for the cells produced a block with
    no cells in it. The live-update is written to only ever set values and never build DOM -- so the first
    person to rate the game found every `[data-rec-cell]` lookup missing, skipped all three, and still
    un-hid the block: an empty row with an orphan "from 1 rating that answered" under it.

    Zeros are the honest starting state anyway, and they keep that contract true."""
    html = _conditions(None)

    for value in ('worth_it', 'good_game_bad_plat', 'skip'):
        assert f'data-rec-cell="{value}"' in html, f'{value} is missing before anyone has rated'
    assert 'gd-cond__recs is-empty' in html, 'the block should still be collapsed until someone answers'
    assert html.count('gd-cond__rec is-none') == 3, 'all three should be drawn held back'


def test_the_split_is_worded_for_the_group_being_shown():
    """`_compute_averages` is concept-wide and bakes the platinum wording into the labels it caches. A DLC
    pack's own panel therefore read "Good game, tough plat" about a set that has no platinum -- the
    same fact the radio the hunter clicked had already worded the other way. Counts ride the cached dict;
    the words come from the group."""
    html = _conditions(dict(_AVERAGES, recommendation_split=_SPLIT), has_platinum=False)

    assert 'Good game, tough trophies' in html
    assert 'Good game, tough plat' not in html
    # And the counts still come from the cached split, which is the half that IS concept-wide.
    assert '80%' in html and '20%' in html


def test_the_host_tells_the_partial_whether_the_group_has_a_platinum():
    """The wording above is only right because the panel passes it down per group. Silent if dropped: an
    unset template variable resolves to the empty string, which is falsy, so every group would quietly get
    the no-platinum wording."""
    src = (ROOT / 'templates' / 'trophies' / 'partials' / 'game_detail' / 'ratings_panel.html').read_text(
        encoding='utf-8')

    # The exact expression, not the substring. `has_platinum=False` or the word appearing in any other
    # include would satisfy a bare `'has_platinum=' in src`, and the failure it guards is SILENT.
    assert 'has_platinum=ct.has_platinum' in src, (
        'the panel no longer passes the group\'s own has_platinum to _rating_conditions'
    )


def test_the_total_sits_with_the_action_not_beside_the_answered_count():
    """Two counts touching read as one confused sentence: "from 5 ratings that answered" and "12 ratings"
    are DIFFERENT denominators (every rating, versus only those carrying a recommendation), and the reader
    has to work that out before either means anything. The total moved down to the action row, with a row
    of content between them.

    The live-update has to follow it. `[data-rate-count]` was queried from the conditions grid, which the
    total is no longer inside -- and that failure is silent: the figure simply stops updating after a save
    and is right again on reload."""
    html = _conditions(dict(_AVERAGES, recommendation_split=_SPLIT))
    foot = html[html.index('gd-rate__foot'):]

    assert 'data-rate-count' in foot, 'the total is not in the action row'
    assert 'data-rate-count' not in html[:html.index('gd-rate__foot')], 'the total is still in the hero too'

    js = (ROOT / 'static' / 'js' / 'game-detail.js').read_text(encoding='utf-8')
    assert "panel.querySelector('[data-rate-count]')" in js, (
        'the live-update looks for the total inside the conditions grid, which no longer contains it'
    )


def test_the_shares_add_up_to_a_hundred():
    """Three percentages printed side by side are expected to total. Naive rounding does not: three equal
    shares give 33/33/33 and read as a missing percent, and 1/3/3 of seven gives 14/43/43 and reads as an
    extra one. Largest-remainder hands the leftover to whichever options were rounded down hardest."""
    from trophies.services.rating_service import _percentages

    assert sum(_percentages([1, 1, 1])) == 100
    assert sum(_percentages([1, 3, 3])) == 100
    assert sum(_percentages([2, 1, 0])) == 100
    assert _percentages([4, 1, 0]) == [80, 20, 0]
    # Nobody has answered: zeroes, not a division by zero.
    assert _percentages([0, 0, 0]) == [0, 0, 0]


def test_one_glyph_per_answer_across_every_surface():
    """The form and the community split show the same three answers, so they show the same three glyphs --
    thumbs for the ends, a tilde for the qualified middle. Two copies is how the same verdict ends up
    looking like two different things on two pages, which is the drift this feature keeps removing."""
    from django.template.loader import render_to_string as render

    for tpl in ('partials/_rating_fields.html',
                'trophies/partials/game_detail/_rating_conditions.html'):
        src = (ROOT / 'templates' / tpl).read_text(encoding='utf-8')
        assert "'partials/_recommendation_icon.html'" in src, f'{tpl} draws its own recommendation glyphs'

    # The middle answer is NOT a broken heart (what it first shipped as): that says the game let you down,
    # when the option means the opposite -- the game was good and the platinum was not.
    icon = (ROOT / 'templates' / 'partials' / '_recommendation_icon.html').read_text(encoding='utf-8')
    body = re.sub(r'\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}', '', icon, flags=re.S)
    assert 'M19 14c1.49' not in body, 'the middle option is back to a broken heart'
    for value in ('worth_it', 'good_game_bad_plat', 'skip'):
        assert value in body, f'{value} has no glyph'
        assert render('partials/_recommendation_icon.html', {'value': value}).strip(), f'{value} renders nothing'


def test_the_split_prints_its_sample_size():
    """"80% would recommend" is honest at 40 ratings and misleading at 5, and there is no way to tell them
    apart without the N. Printed at every size rather than hidden behind a floor -- the same reason the
    doc parks the cross-game percentile: a figure that looks authoritative on thin data erodes trust."""
    html = _conditions(dict(_AVERAGES, recommendation_split=_SPLIT))

    # The count sits in its own span (the live-update writes it), so the figure and the word it belongs to
    # are not adjacent in the source.
    assert 'data-cond-rec-n>5</span> rating' in html
    assert 'gd-cond__recs is-empty' not in html


def test_the_split_is_absent_until_someone_answers():
    """Which is every game until the recommendation backlog clears. The element stays in the DOM (the
    live-update only sets values, it never builds), but it collapses."""
    html = _conditions(dict(_AVERAGES, recommendation_split={'options': [], 'answered': 0}))

    assert 'gd-cond__recs is-empty' in html


def test_a_stale_cached_averages_dict_does_not_break_the_card():
    """The dict is cached for an hour, so right after the field ships some panels render from one pickled
    before it existed. A missing key must degrade to "no split", not to a traceback."""
    html = _conditions(_AVERAGES)   # no recommendation_split key at all

    assert 'gd-cond__recs is-empty' in html
    assert '4.5' in html            # the rest of the card is unaffected


def _conditions(averages, **kw):
    ctx = {
        'averages': averages, 'concept_id': 1, 'group_id': 'default',
        'hours_label': 'Hours to Plat', 'hours_label_long': 'Hours to Platinum',
        # The host always supplies this (pinned below) and the split's labels are worded from it.
        'has_platinum': True,
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


# ── star-distribution histogram ──────────────────────────────────────────────

def test_rating_distribution_buckets():
    """_compute_averages gives each 0.5 step its own bucket (10 columns, step 1..10, no rounding)."""
    from trophies.services.rating_service import RatingService
    concept = ConceptFactory()
    base = ConceptTrophyGroupFactory(concept=concept, trophy_group_id='default', display_name='Base Game')
    for r in (5.0, 3.5, 5.0, 1.0):   # 3.5 stays its own bucket (step 7), NOT folded into 4 or 3
        UserConceptRating.objects.create(profile=ProfileFactory(), concept=concept, concept_trophy_group=None,
                                         difficulty=5, grindiness=5, hours_to_platinum=10, fun_ranking=5, overall_rating=r)
    avg = RatingService.get_community_averages_for_group(concept, base)
    dist = {row['step']: row for row in avg['distribution']}
    assert len(avg['distribution']) == 10
    assert dist[10]['count'] == 2 and dist[10]['value'] == 5.0 and dist[10]['pct'] == 50 and dist[10]['bar'] == 100
    assert dist[7]['count'] == 1 and dist[7]['value'] == 3.5 and dist[7]['starnum'] is None   # half step, unlabeled
    assert dist[2]['count'] == 1 and dist[2]['value'] == 1.0 and dist[2]['starnum'] == 1      # whole star, labeled
    assert dist[10]['starnum'] == 5 and dist[9]['starnum'] is None
    assert [row['step'] for row in avg['distribution']] == list(range(1, 11))   # stored 0.5 -> 5.0


_DIST = [{'step': s, 'value': s / 2, 'starnum': s // 2 if s % 2 == 0 else None,
          'count': (5 if s == 10 else 0), 'pct': (100 if s == 10 else 0), 'bar': (100 if s == 10 else 0)}
         for s in range(1, 11)]


def test_distribution_histogram_shown_with_enough_ratings():
    html = _conditions({**_AVERAGES, 'count': 20, 'distribution': _DIST})
    assert 'gd-dist' in html and 'data-dist-step="10"' in html
    assert 'height: 100%' in html                  # the 5.0 column (the tallest) fills the chart


def test_distribution_histogram_hidden_below_threshold():
    """Gated to 3+ ratings: a 1-2 rating spread is noise, so the chart (and its wide layout) stay hidden."""
    html = _conditions({**_AVERAGES, 'count': 2, 'distribution': _DIST})
    assert 'gd-dist' not in html and 'is-wide' not in html
    assert 'gd-dist' not in _conditions(None)   # unrated -> no averages -> no histogram


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
    assert 'gd-view-community' not in content
    assert '<span class="pp-switch__lbl">Community</span>' not in content


def test_roadmap_tab_removed_and_players_link_goes_to_ranks(client):
    """The Roadmap tab/panel were removed from game detail; the hero "X Players" jump now targets the Ranks
    (leaderboard) tab, not Ratings."""
    content = _detail(client, GameFactory(defined_trophies=_DEFINED, played_count=100))
    assert 'data-view="roadmap"' not in content and 'gd-view-roadmap' not in content   # tab + panel gone
    assert '<span class="pp-switch__lbl">Roadmap</span>' not in content
    assert 'data-gd-goto="leaderboard"' in content and 'href="?view=leaderboard"' in content
    assert 'data-gd-goto="ratings"' not in content     # the Players jump no longer points at Ratings


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


def test_quick_rate_playtime_hint_and_toast_container():
    """The modal shows the viewer's tracked playtime as an estimate hint (with a fallback), and hosts its own
    top-layer toast container so submit warnings aren't hidden behind the backdrop."""
    tpl = 'trophies/partials/game_detail/quick_rate_modal.html'
    hinted = render_to_string(tpl, {'user_play_hours': 42})
    assert '<b>42</b>' in hinted and 'Playtime: about' in hinted   # playtime shown as a reference
    assert 'modal-toast-container' in hinted                     # toasts render in the dialog's top layer
    assert 'No playtime tracked.' in render_to_string(tpl, {})   # graceful fallback when untracked


def test_playtime_hint_reaches_modal_from_context(client):
    """Regression: the view must copy user_play_hours out of _build_profile_context so the modal hint shows."""
    from datetime import timedelta
    from trophies.models import ProfileGame
    profile, game = ProfileFactory(is_linked=True), GameFactory(defined_trophies=_DEFINED)
    ProfileGame.objects.create(profile=profile, game=game, play_duration=timedelta(hours=42))
    client.force_login(profile.user)
    content = _detail(client, game)
    assert 'Playtime: about' in content and '<b>42</b>' in content


def test_quick_takes_count_in_title():
    """The non-hidden quick-take count sits next to the section title."""
    concept = ConceptFactory()
    b = _blurb_row(concept, ProfileFactory(), 'A take.')
    html = _conditions(_AVERAGES, blurbs=[b], blurb_count=7)
    assert 'data-blurbs-count' in html and '>7<' in html


def test_minibar_has_per_tab_icons_and_ratings_group_slot(client):
    """The sticky minibar carries an icon per tab (matches the active one) + the Base/DLC group slot."""
    content = _detail(client, GameFactory(defined_trophies=_DEFINED))
    for tab in ('trophies', 'ratings', 'leaderboard', 'about'):
        assert 'data-mb-only="' + tab + '"' in content
    assert 'data-rate-mb-title' in content


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
    # Scoped to the blurbs SECTION. A bare `'is-empty' not in html` also matched the recommendation row,
    # which carries the same modifier and is legitimately empty until someone answers.
    assert 'Quick takes' in html and 'gd-blurbs is-empty' not in html
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
