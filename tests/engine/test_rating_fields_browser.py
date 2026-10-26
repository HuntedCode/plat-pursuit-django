"""The rating form asks for every score, in a real browser (owner, 2026-10-26, review AU-01).

The four sliders used to start at 5 / 5 / 5 / 3.0 and post those when left alone, so every rating that only
picked a recommendation pulled the game's community averages to the middle. Now a fresh form starts each slider
UNSET and `PlatPursuit.RatingFields` will not post until each one is set. Renders the real `_rating_fields.html`
with the real `quick-rate.js` against the built output.css in Chromium, because the part that matters most is a
pointer detail: pressing a slider where its thumb already sits fires no `input`, and that press must still count.

Skips with a reason where Playwright's browser is not installed (see test_trophy_controls_browser).
"""
from pathlib import Path

import pytest
from django.template.loader import render_to_string

from tests.engine.test_trophy_controls_browser import _SKIP_REASON

ROOT = Path(__file__).resolve().parents[2]
CSS = ROOT / 'static' / 'css' / 'output.css'
JS = ROOT / 'static' / 'js' / 'quick-rate.js'
pytestmark = [pytest.mark.django_db, pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or '')]

SLIDERS = ('difficulty', 'grindiness', 'fun_ranking', 'overall_rating')
ATTACH = """(existing) => {
  window.posted = []; window.refused = [];
  window.PlatPursuit.API = { post: (url, body) => { window.posted.push(body); return Promise.resolve({}); } };
  window.fields = window.PlatPursuit.RatingFields.attach(document.getElementById('f'), {
    conceptId: 1, groupId: 'default', existing: existing, announcesSave: true,
    onRefused: (msg, name) => window.refused.push([msg, name]),
  });
}"""


# Where a slider's thumb sits: the track's usable length is its width less one thumb, so the thumb's centre is
# half a thumb in from the left plus the value's share of the rest. Chromium's default thumb is 16px.
THUMB = """(name) => {
  const s = document.querySelector(`[name="${name}"]`), r = s.getBoundingClientRect(), t = 16;
  const share = (s.value - s.min) / (s.max - s.min);
  return [r.left + t / 2 + share * (r.width - t), r.top + r.height / 2];
}"""


def _page(tmp_path):
    fields = render_to_string('partials/_rating_fields.html', {'user_play_hours': 21})
    page = tmp_path / 'rate.html'
    page.write_text(
        '<!doctype html><html data-theme="plat-pursuit-dark"><head><meta charset="utf-8">'
        f'<link rel="stylesheet" href="{CSS.as_uri()}"></head><body class="bg-base-100" style="width:420px">'
        f'<form id="f" class="gd-qr" data-guidelines-agreed="1">{fields}</form>'
        f'<script>window.PlatPursuit = {{}};</script><script src="{JS.as_uri()}"></script></body></html>',
        encoding='utf-8')
    return page.as_uri()


def _answer_the_rest(pg):
    pg.fill('[name="hours_to_platinum"]', '30')
    pg.click('[data-rec="worth_it"]')          # the radio itself is visually hidden; its label is the control


def _readouts(pg):
    return pg.evaluate("() => [...document.querySelectorAll('[data-gd-qr-val]')].map(e => e.textContent.trim())")


def _run(tmp_path, steps):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch()
        try:
            pg = b.new_page()
            pg.goto(_page(tmp_path))
            return steps(pg)
        finally:
            b.close()


def test_a_fresh_form_starts_every_score_unset_and_will_not_post_it(tmp_path):
    def steps(pg):
        pg.evaluate(ATTACH, None)
        _answer_the_rest(pg)
        before = (_readouts(pg), pg.evaluate('window.fields.state().ready'))
        pg.evaluate('window.fields.submit()')
        return before, pg.evaluate('[window.posted, window.refused]')

    (readouts, ready), (posted, refused) = _run(tmp_path, steps)
    assert readouts == ['-', '-', '-', '-'], readouts
    assert ready is False
    assert posted == [], 'an untouched slider was filed as a score'
    assert refused == [['Set your difficulty.', 'difficulty']]


def test_pressing_a_slider_where_its_thumb_sits_still_sets_it(tmp_path):
    """The thumb rests at 5 (overall 3.0). A press that lands there changes nothing, so it fires no `input`,
    and choosing 5 is still a real answer."""
    def steps(pg):
        pg.evaluate(ATTACH, None)
        _answer_the_rest(pg)
        for name in SLIDERS:
            x, y = pg.evaluate(THUMB, name)
            pg.mouse.click(x, y)                  # on the thumb itself: the value does not change
        pg.evaluate('window.fields.submit()')
        pg.wait_for_function('window.posted.length === 1')
        return _readouts(pg), pg.evaluate('window.posted[0]')

    readouts, body = _run(tmp_path, steps)
    assert '-' not in readouts, readouts
    assert (body['difficulty'], body['grindiness'], body['fun_ranking'], body['overall_rating']) == (5, 5, 5, 3.0)


def test_a_key_press_sets_a_slider_and_the_refusal_names_the_next_one(tmp_path):
    def steps(pg):
        pg.evaluate(ATTACH, None)
        _answer_the_rest(pg)
        pg.focus('[name="difficulty"]')
        pg.keyboard.press('ArrowRight')
        pg.evaluate('window.fields.submit()')
        return _readouts(pg), pg.evaluate('window.refused')

    readouts, refused = _run(tmp_path, steps)
    assert readouts[0] == '6'
    assert refused == [['Set your grindiness.', 'grindiness']]


def test_an_existing_rating_prefills_as_answered(tmp_path):
    existing = {'difficulty': 7, 'grindiness': 2, 'fun_ranking': 9, 'overall_rating': 4.5,
                'hours_to_platinum': 40, 'recommendation': 'worth_it'}

    def steps(pg):
        pg.evaluate(ATTACH, existing)
        return _readouts(pg), pg.evaluate('window.fields.state().ready'), pg.evaluate(
            "document.querySelectorAll('[data-gd-qr-slider][data-unset]').length")

    readouts, ready, unset = _run(tmp_path, steps)
    assert readouts == ['7', '2', '9', '4.5']
    assert ready is True and unset == 0


def test_the_next_game_in_the_wizard_starts_unset_again(tmp_path):
    """Rate My Games re-prefills ONE form game after game. A score set for the last game must not carry over
    as an answer to the next."""
    def steps(pg):
        pg.evaluate(ATTACH, None)
        for name in SLIDERS:
            pg.click(f'[name="{name}"]')
        pg.evaluate('window.fields.prefill(null, "")')
        return _readouts(pg)

    assert _run(tmp_path, steps) == ['-', '-', '-', '-']
