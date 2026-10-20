"""The Challenge Optimizer's layout fixes, in a real browser (owner, 2026-10-17).

WHY A BROWSER TEST: both fixes are about where things land on screen. Back-to-top sat exactly on Next and Apply, and
a game in a long series printed one wrapping "Counts toward Stage N (title) of Series, Edition" per stage. Source
text cannot say whether two boxes overlap, so this drives Chromium over the REAL functions (lifted from
`challenge-optimizer.js`), the REAL back-to-top partial and the REAL `output.css`, no server needed.

Skips with a reason where the Playwright browser is not installed; CI installs it (see test_plat_card_layout).
"""
import json
from pathlib import Path

import pytest

pytest.importorskip('playwright.sync_api')

ROOT = Path(__file__).resolve().parents[2]
JS = (ROOT / 'static/js/challenge-optimizer.js').read_text(encoding='utf-8')


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


def _page(tmp_path, body, script, name):
    css = (ROOT / 'static/css/output.css').as_uri()
    html = (f'<!doctype html><html data-theme="plat-pursuit-dark"><head><meta name="viewport" '
            f'content="width=device-width, initial-scale=1"><link rel="stylesheet" href="{css}">'
            '<style>body{background:#0b0f17;margin:0;padding:16px}</style></head>'
            f'<body>{body}<script>{script}</script></body></html>')
    path = tmp_path / name
    path.write_text(html, encoding='utf-8')
    return path.as_uri()


# --- back-to-top clears the docks -------------------------------------------------------------------------

BACK_TO_TOP = (ROOT / 'templates/partials/back_to_top.html').read_text(encoding='utf-8')
WATCH_DOCKS = JS[JS.index('    function watchDocks(docks) {'):JS.index('    function boot() {')]

DOCK_PAGE = ('<div class="pp-cplan" data-cplan><section style="height:1600px;display:flex;flex-direction:column">'
             '<div style="flex:1"></div>'
             '<div class="pp-cplan__nav pp-cplan__dock" data-cplan-dock>'
             '<button type="button" class="pp-cta pp-cta--ghost">Back</button>'
             '<button type="button" class="pp-cta" data-next>Next</button></div></section>'
             '<div style="height:800px"></div></div>' + BACK_TO_TOP)


def _measure(tmp_path, width, *, hide_dock=False, wire=True, shown=True):
    from playwright.sync_api import sync_playwright
    script = (WATCH_DOCKS + '\nwatchDocks(document.querySelectorAll("[data-cplan-dock]"));') if wire else ''
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': width, 'height': 800})
        page.goto(_page(tmp_path, DOCK_PAGE, script, f'dock-{width}.html'))
        if hide_dock:
            page.evaluate("document.querySelector('[data-cplan-dock]').hidden = true")
        page.evaluate('window.scrollTo(0, 400)')
        if shown:
            # Measure where the button RESTS when offered. Since 2026-10 it is offered only on scroll-up
            # (wireBackToTop in main.js) and slides in from 10px below, so the hidden state is not where
            # it sits; the geometry that matters is the shown one.
            page.evaluate("document.getElementById('back-to-top').classList.add('is-shown')")
        page.wait_for_timeout(400)   # the observer reports on the next frame; the slide-in settles
        out = page.evaluate("""() => {
            const r = el => el.getBoundingClientRect();
            const btt = document.getElementById('back-to-top'), dock = document.querySelector('[data-cplan-dock]');
            const b = r(btt), n = r(document.querySelector('[data-next]'));
            return { bttTop: b.top, bttBottom: b.bottom, dockTop: r(dock).top, nextTop: n.top,
                     fromBottom: innerHeight - b.bottom,
                     hit: document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2) === btt }; }""")
        browser.close()
    return out


@pytest.mark.parametrize('width', [375, 1280])
def test_back_to_top_sits_above_the_docked_buttons(tmp_path, width):
    out = _measure(tmp_path, width)
    assert out['dockTop'] < 800 and out['nextTop'] < 800          # the dock is stuck in view
    assert out['bttBottom'] <= out['dockTop'] - 4, out             # and the button clears it, with a gap


@pytest.mark.parametrize('width', [375, 1280])
def test_without_the_lift_the_button_would_cover_the_dock(tmp_path, width):
    # The premise of the fix, measured: the site's default spot overlaps the dock at both widths.
    out = _measure(tmp_path, width, wire=False)
    assert out['bttBottom'] > out['dockTop'], out


@pytest.mark.parametrize('width, default_px', [(375, 96), (1280, 32)])
def test_with_no_dock_showing_the_button_keeps_its_own_spot(tmp_path, width, default_px):
    out = _measure(tmp_path, width, hide_dock=True)
    assert out['fromBottom'] == pytest.approx(default_px, abs=1)


def test_a_faded_out_back_to_top_lets_clicks_through(tmp_path):
    # Before the scroll threshold the button is invisible; it used to take the click meant for whatever sat
    # under it (the optimizer's Next). The partial ships hidden, so the point hits the page, not the button.
    out = _measure(tmp_path, 1280, shown=False)
    assert out['hit'] is False


# (main.js's show/hide behaviour, including inertness while hidden, is driven in a real browser by
#  tests/engine/test_back_to_top_and_toasts_browser.py since the 2026-10 scroll-up rewrite.)


# --- one row per badge ------------------------------------------------------------------------------------

STAGE_FNS = JS[JS.index('        function stageList(item) {'):JS.index('        function metaNote(text, kind) {')]

MW2 = {'stages': [
    {'series': 'cod', 'series_name': 'Call of Duty Series', 'stage': n, 'title': title, 'part': False,
     'editions': ['Ultra HD']}
    for n, title in [(17, 'Call of Duty: Modern Warfare II'), (18, 'Call of Duty: Modern Warfare III'),
                     (19, 'Call of Duty: Black Ops 6'), (20, 'Call of Duty: Black Ops 7')]
] + [
    {'series': 'fps', 'series_name': 'Shooters', 'stage': 3, 'title': 'Modern', 'part': False, 'editions': []},
    {'series': 'fps', 'series_name': 'Shooters', 'stage': 5, 'title': '', 'part': True, 'editions': []},
]}


def _stage_rows(tmp_path, width):
    from playwright.sync_api import sync_playwright
    body = '<div class="pp-cplan" style="max-width:100%"><div class="pp-cplan__line-main" id="host"></div></div>'
    script = STAGE_FNS + f'\ndocument.getElementById("host").appendChild(stageList({json.dumps(MW2)}));'
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': width, 'height': 800})
        page.goto(_page(tmp_path, body, script, f'stages-{width}.html'))
        rows = page.evaluate("""() => [...document.querySelectorAll('.pp-cplan__stage')].map(row => {
            const seen = el => [...el.childNodes].filter(n => !(n.classList && n.classList.contains('sr-only')))
                .map(n => n.nodeType === 3 ? n.textContent : seen(n)).join('');
            return { name: seen(row.querySelector('.pp-cplan__stage-name')),
                     nums: seen(row.querySelector('.pp-cplan__stage-nums')),
                     spoken: row.querySelector('.pp-cplan__stage-nums').textContent,
                     titles: [...row.querySelectorAll('.pp-cplan__stage-n')].map(n => n.title),
                     overflow: row.scrollWidth > row.clientWidth }; })""")
        page_overflow = page.evaluate('document.documentElement.scrollWidth > innerWidth')
        browser.close()
    return rows, page_overflow


def test_a_games_stages_read_as_one_row_per_badge(tmp_path):
    rows, _ = _stage_rows(tmp_path, 1280)
    assert [(r['name'], r['nums']) for r in rows] == [
        ('Call of Duty Series (Ultra HD)', 'Stages 17, 18, 19, 20'),
        ('Shooters', 'Stage 3'),
        ('Shooters', 'Part of stage 5'),   # a bundle route keeps its own row: the game alone does not clear it
    ]
    assert rows[0]['titles'] == ['Call of Duty: Modern Warfare II', 'Call of Duty: Modern Warfare III',
                                 'Call of Duty: Black Ops 6', 'Call of Duty: Black Ops 7']
    assert rows[2]['titles'] == ['']
    # A screen reader hears each title after its number, since a hover title is not reliably read out.
    assert rows[0]['spoken'].startswith('Stages 17 (Call of Duty: Modern Warfare II), 18 (')


def test_the_stage_rows_fit_a_phone_without_scrolling_sideways(tmp_path):
    rows, page_overflow = _stage_rows(tmp_path, 375)
    assert not page_overflow and not any(r['overflow'] for r in rows)


def test_a_game_with_no_stages_draws_no_list(tmp_path):
    from playwright.sync_api import sync_playwright
    script = STAGE_FNS + '\nwindow.__out = String(stageList({stages: []}));'
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(_page(tmp_path, '', script, 'empty.html'))
        assert page.evaluate('window.__out') == 'null'
        browser.close()
