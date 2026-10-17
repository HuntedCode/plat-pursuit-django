"""The finished-square foil TILT, in a real browser (owner, 2026-10-17).

WHY A BROWSER TEST: the first cut of `wireFoilTilt` passed every source-text pin and still did not work. Its
"is this square still arriving?" check read `getAnimations()`, which also returns CSS TRANSITIONS, and the tilt
itself starts a transform transition, so every tilt cancelled itself on the next move: the owner saw it "flash,
move a little after a moment and stop". Only real pointer events against the real stylesheet catch that class of
bug, so this drives Chromium over the REAL `wireFoilTilt` (lifted from `challenge-detail.js`) and the REAL
`output.css`, no server needed.

Skips with a reason where the Playwright browser is not installed; CI installs it (see test_plat_card_layout).
"""
from pathlib import Path

import pytest

pytest.importorskip('playwright.sync_api')

ROOT = Path(__file__).resolve().parents[2]


def _browser_missing():
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as p:
            p.chromium.launch().close()
        return None
    except Exception as exc:  # noqa: BLE001 -- any launch failure means we cannot measure
        return f'playwright chromium unavailable ({type(exc).__name__}); run `playwright install chromium`'


_SKIP_REASON = _browser_missing()
pytestmark = pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or '')

SQUARE = ('<li><div class="pp-csq pp-csq--done" style="--rev: 0;">'
          '<span class="pp-csq__foil" aria-hidden="true"><span class="pp-csq__gleam"></span></span>'
          '<span class="pp-csq__scrim"></span><span class="pp-csq__key">A</span></div></li>')


def _page_file(tmp_path):
    js = (ROOT / 'static/js/challenge-detail.js').read_text(encoding='utf-8')
    fn = js[js.index('    function wireFoilTilt(board) {'):js.index('    function boot(first) {')]
    css = (ROOT / 'static/css/output.css').as_uri()
    html = (f'<!doctype html><html data-theme="dark"><head><link rel="stylesheet" href="{css}">'
            '<style>body{background:#0b0f17;padding:40px;margin:0}.wrap{width:420px}</style></head><body>'
            f'<div class="wrap"><div class="pp-csq-board"><ul class="pp-csq-grid" role="list">{SQUARE * 3}</ul>'
            f'</div></div><script>{fn}\nwireFoilTilt(document.querySelector(".pp-csq-board"));</script>'
            '</body></html>')
    path = tmp_path / 'tilt.html'
    path.write_text(html, encoding='utf-8')
    return path.as_uri()


STATE = """() => { const s = document.querySelector('.pp-csq');
    const m = getComputedStyle(s).transform;
    const v = m.startsWith('matrix3d(') ? m.slice(9, -1).split(',').map(Number) : null;
    return { tilting: s.classList.contains('is-tilting'), m13: v ? v[2] : 0 }; }"""


def _run(tmp_path, *, settle_ms=1500, reduced=False, sweep=True):
    from playwright.sync_api import sync_playwright
    samples = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 600, 'height': 600})
        page.emulate_media(reduced_motion='reduce' if reduced else 'no-preference')
        page.goto(_page_file(tmp_path))
        page.wait_for_timeout(settle_ms)
        box = page.locator('.pp-csq').first.bounding_box()
        steps = 24 if sweep else 1
        for i in range(steps):
            frac = 0.15 + 0.7 * i / max(steps - 1, 1)
            page.mouse.move(box['x'] + box['width'] * frac, box['y'] + box['height'] * 0.5)
            page.wait_for_timeout(40)
            samples.append(page.evaluate(STATE))
        page.mouse.move(5, 5)   # off the board
        page.wait_for_timeout(80)
        samples.append(page.evaluate(STATE))
        browser.close()
    return samples


def test_a_finished_square_stays_tilted_while_the_pointer_moves_across_it(tmp_path):
    samples = _run(tmp_path)
    moving, after = samples[:-1], samples[-1]
    # EVERY sample, not "sometimes": the self-cancelling bug tilted in 3 of 30.
    assert all(s['tilting'] for s in moving), [s['tilting'] for s in moving]
    # It leans one way at the left edge and the other way at the right.
    assert moving[0]['m13'] * moving[-1]['m13'] < 0, (moving[0]['m13'], moving[-1]['m13'])
    # Leaving the board lets it go.
    assert not after['tilting']


def test_a_square_still_arriving_waits_then_tilts(tmp_path):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 600, 'height': 600})
        page.emulate_media(reduced_motion='no-preference')
        page.goto(_page_file(tmp_path))
        page.wait_for_timeout(60)
        box = page.locator('.pp-csq').first.bounding_box()
        page.mouse.move(box['x'] + box['width'] * 0.3, box['y'] + box['height'] * 0.5)
        page.wait_for_timeout(40)
        # The pointer arrives during the board's entrance: no tilt yet (the entrance owns `transform`) ...
        early = page.evaluate(STATE)
        page.wait_for_timeout(900)
        page.mouse.move(box['x'] + box['width'] * 0.7, box['y'] + box['height'] * 0.5)
        page.wait_for_timeout(60)
        # ... and once it has landed, the next move tilts it.
        landed = page.evaluate(STATE)
        browser.close()
    assert not early['tilting'] and landed['tilting']


def test_reduced_motion_never_tilts(tmp_path):
    samples = _run(tmp_path, reduced=True)
    assert not any(s['tilting'] for s in samples)
