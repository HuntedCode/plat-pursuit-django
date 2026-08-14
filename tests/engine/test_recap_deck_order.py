"""The deck's ARC -- slide order as data, and the pairings that make it read as authored.

`build_slides_response` used to be ~110 lines of `if ...: slides.append(...)`, so the arc could only be
understood by reading control flow. It is now the `DECK` list, and these tests pin the editorial decisions
that list encodes -- the ones a later edit could quietly undo:

- **Every quiz sits immediately before the thing it asks about.** Guess, then find out. Insert a slide
  between a quiz and its reveal and the pairing silently breaks; nothing errors.
- **The payoff is second to last.** `getScore()` was computed and never shown for the whole life of the
  feature. It closes the loop, and it has to land before the wrap, not after it.
- **One catalogue.** The API's slide-partial view used to rebuild every payload in a parallel if/elif
  chain, and the two had already drifted (their summary chips differed).

Uses an UNSAVED MonthlyRecap so the model's real fields and defaults are exercised without a database.
"""
import json

import pytest

from trophies.models import MonthlyRecap
from trophies.services.monthly_recap_service import DECK, DECK_BY_TYPE, MonthlyRecapService

# quiz -> the slide that answers it.
QUIZ_PAIRS = {
    'quiz_total_trophies': 'total_trophies',
    'quiz_active_day': 'most_active_day',
    'quiz_rarest_trophy': 'rarest_trophy',
    'quiz_closest_badge': 'badges',
}

ORDER = [beat.type for beat in DECK]


def _full_recap():
    """A month where everything happened, so every conditional beat is included."""
    return MonthlyRecap(
        year=2026, month=3,
        total_trophies_earned=147, bronzes_earned=96, silvers_earned=34,
        golds_earned=14, platinums_earned=3,
        games_started=6, games_completed=3,
        badges_earned_count=2, badge_xp_earned=4100,
        # A post-rework row: `art_layers` is what the Medallion draws, and its absence is how the beat
        # recognises a pre-rework snapshot and drops it.
        badges_data=[{'series_name': 'Soulsborne', 'badge_name': 'Ultra HD',
                      'art_layers': ['/static/bg.png', '/media/main.png'], 'state': 'earned'}],
        platinums_data=[{'game_name': 'Bloodborne'}],
        rarest_trophy_data={'name': 'Chalice', 'earn_rate': 1.4},
        most_active_day={'date': 'March 18, 2026', 'day_name': 'Wednesday', 'trophy_count': 31},
        activity_calendar={'days': [{'day': 1, 'level': 0}], 'first_day_weekday': 3},
        streak_data={'longest_streak': 9},
        time_analysis_data={'persona': 'night_owl', 'periods': {'Morning': 8, 'Late Night': 62}},
        quiz_total_trophies_data={'correct_value': 147, 'options': [88, 147, 203, 61]},
        quiz_rarest_trophy_data={'correct_trophy_id': 'a', 'options': []},
        quiz_active_day_data={'correct_day': 5, 'day_names': []},
        badge_progress_quiz_data={'correct_badge_id': 'b', 'options': []},
        comparison_data={'vs_prev_month_pct': '+42%', 'vs_last_year_pct': '+12%', 'personal_bests': []},
        taste_data={'genre': 'Role-playing (RPG)', 'genre_count': 62, 'runners_up': []},
        community_comparison_data={'game_name': 'Bloodborne', 'your_completion': 87,
                                   'avg_completion': 54, 'played_count': 12480},
        month_in_history_data={'years': [{'year': 2025, 'trophies': 96, 'platinums': 1},
                                         {'year': 2026, 'trophies': 147, 'platinums': 3}],
                               'best_year': 2026, 'best_trophies': 147, 'anniversary': None},
    )


def _quiet_recap():
    """A month with a handful of trophies and nothing else -- no platinums, no badges, no quizzes."""
    return MonthlyRecap(year=2026, month=3, total_trophies_earned=4,
                        bronzes_earned=4, comparison_data={'vs_prev_month_pct': '0%'})


# --- The arc ---------------------------------------------------------------------------------------

def test_the_deck_opens_and_closes_where_it_should():
    assert ORDER[0] == 'intro'
    assert ORDER[-1] == 'summary'
    assert ORDER[-2] == 'quiz_score', 'the payoff must land before the wrap, not after it'


@pytest.mark.parametrize('quiz,reveal', QUIZ_PAIRS.items())
def test_each_quiz_sits_immediately_before_its_answer(quiz, reveal):
    assert ORDER.index(reveal) == ORDER.index(quiz) + 1, (
        f'{quiz} no longer runs straight into {reveal}; the hunter guesses and then gets something else'
    )


def test_the_peak_comes_after_the_build():
    """Platinums used to be the FOURTH slide, spending the deck's biggest moment before it had built
    anything. The sequence now climbs volume -> habit -> rarity -> platinums."""
    for earlier in ('total_trophies', 'activity_calendar', 'rarest_trophy'):
        assert ORDER.index(earlier) < ORDER.index('platinums'), (
            f'{earlier} now comes after the platinum peak'
        )


def test_every_beat_type_is_unique():
    assert len(ORDER) == len(set(ORDER))


# --- Assembly --------------------------------------------------------------------------------------

def test_a_full_month_yields_the_whole_deck():
    types = [s['type'] for s in MonthlyRecapService.build_slides_response(_full_recap())]
    assert types == ORDER, 'a month where everything happened should skip nothing'


def test_a_quiet_month_drops_the_beats_it_did_not_earn():
    types = [s['type'] for s in MonthlyRecapService.build_slides_response(_quiet_recap())]
    for absent in ('platinums', 'badges', 'streak', 'rarest_trophy', 'activity_calendar'):
        assert absent not in types, f'{absent} appeared for a month with none'
    # The spine always survives.
    assert types[0] == 'intro' and types[-1] == 'summary'
    assert 'total_trophies' in types and 'comparison' in types


def test_a_pre_rework_badge_snapshot_is_dropped_rather_than_drawn_empty():
    """`badges_data` is sealed at generation and a finalized recap is never rewritten, so months from
    before the badge rework still hold the legacy payload (name/tier_name/image_url) instead of the
    Medallion frame dict. The slide feeds every entry to `badge_medallion.html`, which draws from
    `art_layers` -- so a legacy row rendered an empty medallion shell with blank captions.

    The COUNT and the XP survive on purpose: both are stored integers and historically true, so the slide
    still reports what the month was worth. Recomputing the count to match what can be drawn would make
    the recap understate a real month to tidy its own layout.
    """
    recap = _full_recap()
    recap.badges_data = [
        {'name': 'Crash Bandicoot', 'tier_name': 'Platinum', 'image_url': '/media/legacy.png',
         'has_image': True, 'series_slug': 'crash', 'tier': 4},
    ]

    badges = {s['type']: s for s in MonthlyRecapService.build_slides_response(recap)}['badges']

    assert badges['badges'] == [], 'a legacy row reached the Medallion and will render an empty shell'
    assert badges['badges_count'] == 2, 'the stored count was rewritten to match what could be drawn'
    assert badges['xp_earned'] == 4100, 'the XP the month actually earned was dropped with the art'


def test_a_post_rework_badge_snapshot_still_reaches_the_medallion():
    """Positive control for the filter above -- it must drop legacy rows WITHOUT eating modern ones."""
    badges = {s['type']: s for s in
              MonthlyRecapService.build_slides_response(_full_recap())}['badges']

    assert len(badges['badges']) == 1 and badges['badges'][0]['series_name'] == 'Soulsborne'


def test_the_payoff_is_dropped_when_there_was_nothing_to_guess():
    """A month with no quiz data would otherwise show "0 / 0 guessed right"."""
    types = [s['type'] for s in MonthlyRecapService.build_slides_response(_quiet_recap())]
    assert 'quiz_score' not in types

    full = [s['type'] for s in MonthlyRecapService.build_slides_response(_full_recap())]
    assert 'quiz_score' in full, 'positive control: it must appear when there ARE quizzes'


def test_include_quizzes_false_removes_the_score_too():
    types = [s['type'] for s in
             MonthlyRecapService.build_slides_response(_full_recap(), include_quizzes=False)]
    assert not [t for t in types if t.startswith('quiz_')], 'a quiz beat survived include_quizzes=False'
    assert 'total_trophies' in types, 'positive control: the non-quiz deck is still built'


def test_payloads_carry_what_their_templates_read():
    slides = {s['type']: s for s in MonthlyRecapService.build_slides_response(_full_recap())}
    assert slides['total_trophies']['breakdown']['platinum'] == 3
    assert slides['badges']['xp_earned'] == 4100
    assert slides['comparison']['vs_prev_month'] == '+42%'
    # The deck array is JSON-serialised, so payloads must stay JSON-safe. `first_day_offset` is a
    # `range` built for the template's loop and lives in the VIEW; a range here 500s every recap page.
    assert 'first_day_offset' not in slides['activity_calendar']
    assert slides['activity_calendar']['first_day_weekday'] == 3
    json.dumps(MonthlyRecapService.build_slides_response(_full_recap()))
    # Never 0 -- the bar heights divide by it.
    assert slides['time_analysis']['max_period_count'] == 62
    assert slides['quiz_score'] == {'type': 'quiz_score'}, 'the score is filled client-side, not stored'


def test_summary_highlights_are_worth_saying_out_loud():
    slides = {s['type']: s for s in MonthlyRecapService.build_slides_response(_full_recap())}
    assert slides['summary']['highlights'] == [
        '3 platinums', '147 trophies', '6 new games', '2 badges',
    ]


# --- One catalogue ---------------------------------------------------------------------------------

def test_every_beat_has_a_template_and_every_template_a_beat():
    from api.recap_views import RecapSlidePartialView
    assert set(DECK_BY_TYPE) == set(RecapSlidePartialView.SLIDE_TEMPLATES), (
        'the deck and the slide-partial template map disagree; a beat with no template 500s the slide '
        'request, and a template with no beat is dead'
    )


def test_the_api_does_not_rebuild_payloads_in_parallel():
    """The parallel if/elif chain is what let the two builders drift (their summary chips disagreed). It
    must stay gone. Bounded by LENGTH rather than by counting branches: the handful of genuinely
    view-level cases left (the calendar's range, the intro's identity, the summary's year/month) are
    legitimate, and a branch count would either forbid them or be a magic number."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2] / 'api' / 'recap_views.py').read_text(encoding='utf-8')
    builder = src[src.index('def _build_slide_context'):]
    nxt = builder.find('\n    def ', 1)          # -1 when it is the last method in its class
    builder = builder[:nxt] if nxt != -1 else builder

    assert 'DECK_BY_TYPE' in builder, 'the slide view no longer reads the shared catalogue'
    assert len(builder.splitlines()) < 45, (
        f'_build_slide_context has grown to {len(builder.splitlines())} lines -- it is rebuilding '
        f'payloads again instead of delegating to DECK'
    )
