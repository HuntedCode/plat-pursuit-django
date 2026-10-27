"""`staggerReveal` starts a whole batch before it reads any of it, in a real browser (review ID-01, 2026-10-26).

WHY A BROWSER TEST: the cost is the browser's. The engine read `getAnimations()` inside its per-card loop, right
after starting that card's animation, and the read makes the browser bring every style on the page up to date.
Once per card: a profile's 287-badge tab spent 37 seconds there before it could be used (1.8s with the read
taken out). No source-text pin can see the order of reads and writes at run time, so this runs the REAL
`staggerReveal` (lifted from `utils.js`) in Chromium and logs each reveal and each `getAnimations()` call.

Skips with a reason where the Playwright browser is not installed; CI installs it (see test_plat_card_layout).
"""
from pathlib import Path

import pytest

pytest.importorskip('playwright.sync_api')

from tests.engine.test_challenge_foil_tilt_browser import _SKIP_REASON  # noqa: E402

pytestmark = pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or '')

ROOT = Path(__file__).resolve().parents[2]
CARDS = 120


def _engine():
    js = (ROOT / 'static/js/utils.js').read_text(encoding='utf-8')
    start = js.index('function staggerReveal(o) {')
    return js[start:js.index('\n}\n', start) + 3]


def _page(tmp_path):
    cards = ''.join(f'<li class="cell"><div class="card">{n}</div></li>' for n in range(CARDS))
    html = ('<!doctype html><html><head><style>.pp-reveal .card:not(.is-revealed){opacity:0}'
            '.cell{height:20px}</style></head><body>'
            f'<ul id="grid">{cards}</ul><ul id="more"></ul>'
            '<script>window.PlatPursuit = {};\n'
            f'{_engine()}\n'
            'window.log = [];\n'
            'const read = Element.prototype.getAnimations;\n'
            'Element.prototype.getAnimations = function () { log.push("read"); return read.apply(this, arguments); };\n'
            'window.handle = staggerReveal({ grid: document.getElementById("grid"), cardSelector: ".card",\n'
            '  cellSelector: ".cell", reveal: function (el, d) { log.push("write");\n'
            '    el.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 150, delay: d, fill: "backwards" }); } });\n'
            '</script></body></html>')
    path = tmp_path / 'reveal.html'
    path.write_text(html, encoding='utf-8')
    return path.as_uri()


def _run(tmp_path, script=None):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 400, 'height': 3000})
        page.goto(_page(tmp_path))
        first = page.evaluate('log.slice()')
        later = page.evaluate(script) if script else None
        page.wait_for_timeout(1200)
        state = page.evaluate('''() => ({
            revealed: document.querySelectorAll('.card.is-revealed').length,
            arriving: document.querySelectorAll('.pp-arriving').length })''')
        browser.close()
    return first, later, state


def test_the_first_batch_starts_every_card_before_it_reads_any(tmp_path):
    log, _, _ = _run(tmp_path)
    assert log.count('write') == CARDS and log.count('read') == CARDS, (log.count('write'), log.count('read'))
    # Every write, then every read: one style recalculation for the batch, not one per card.
    assert log == ['write'] * CARDS + ['read'] * CARDS, 'a read came between two writes'


def test_every_card_still_arrives_and_is_handed_back(tmp_path):
    """The split must keep what the read was for: each cell is inert while it arrives and usable after."""
    _, _, state = _run(tmp_path)
    assert state == {'revealed': CARDS, 'arriving': 0}, state


def test_an_appended_batch_is_also_written_before_it_is_read(tmp_path):
    appended = '''async () => {
        const more = document.getElementById('more');
        for (let n = 0; n < 8; n++) { more.insertAdjacentHTML('beforeend', '<li class="cell"><div class="card">x</div></li>'); }
        log.length = 0;
        handle.observe(more.querySelectorAll('.cell'));
        await new Promise(r => setTimeout(r, 400));
        return log.slice(); }'''
    _, log, state = _run(tmp_path, appended)
    assert log == ['write'] * 8 + ['read'] * 8, log
    assert state == {'revealed': CARDS + 8, 'arriving': 0}, state
