"""The pinned chrome on a trophy list, in a real browser (the laptop review, 2026-10-23).

Mid-scroll on a trophy list the navbar, the sub-nav, the page's mini-bar and a sticky group header held 215px, a
third of a 1366x657 laptop. Now:

- While a page bar is pinned, the sub-nav gives way to it: it slides up behind the navbar and the bar sits right
  under the navbar (StickyReveal's `pp-bar-pinned` on <html>, chrome.css). Back at the top, the sub-nav returns.
- The group headers no longer pin; the bar names the group under it, with the hunter's progress in it
  (trophy-grid.js), in place of the page title.

Renders the real group label partial and a real trophy grid with the real utils.js, main.js and trophy-grid.js
against the BUILT output.css in Chromium.

Skips with a reason where Playwright's browser is not installed; CI installs it (see test_plat_card_layout).
"""
from pathlib import Path

import pytest
from django.template.loader import render_to_string

ROOT = Path(__file__).resolve().parents[2]
CSS = ROOT / 'static' / 'css' / 'output.css'
SCRIPTS = [ROOT / 'static' / 'js' / name for name in ('utils.js', 'main.js', 'trophy-grid.js')]


def _browser_missing():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return 'playwright not installed'
    try:
        with sync_playwright() as p:
            p.chromium.launch().close()
        return None
    except Exception as exc:  # noqa: BLE001 -- any launch failure means we cannot test
        return f'playwright chromium unavailable ({type(exc).__name__}); run `playwright install chromium`'


_SKIP_REASON = _browser_missing()
pytestmark = pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or '')

GROUPS = [('default', 'Golf With Your Friends', 45), ('001', 'New Worlds Pack', 100)]


def _page(tmp_path, signed_in=True):
    groups = {gid: {'trophy_group_name': name, 'trophy_group_icon_url': '', 'defined_trophies': {'bronze': 4}}
              for gid, name, _ in GROUPS}
    grid = render_to_string('trophies/partials/trophy_grid/grid.html', {
        'trophies': {gid: [] for gid, _, _ in GROUPS}, 'groups': groups, 'earned': {},
        'group_pct': {gid: pct for gid, _, pct in GROUPS}, 'group_totals': {gid: {'bronze': 2} for gid, _, _ in GROUPS},
        'profile': signed_in, 'show_group_nav': False, 'id_prefix': '',
    })
    label = render_to_string('trophies/partials/trophy_grid/_minibar_group.html', {'view': 'trophies'})
    scripts = ''.join(f'<script src="{s.as_uri()}"></script>' for s in SCRIPTS)
    page = tmp_path / 'chrome.html'
    page.write_text(
        '<!doctype html><html data-theme="plat-pursuit-dark"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<link rel="stylesheet" href="{CSS.as_uri()}">'
        # Each group stands a few screens tall, as a real list's rows would make it.
        '<style>.gd-group { min-height: 1600px; }</style></head><body class="bg-base-100">'
        '<header class="pp-nav"><div style="height:58px">Platinum Pursuit</div></header>'
        '<nav class="pp-sub"><div class="pp-sub__inner container mx-auto flex"><a class="pp-subpill" href="#">Games</a></div></nav>'
        '<main class="container mx-auto px-4"><div style="height:400px">hero</div>'
        '<span id="mb-sentinel" class="pp-sentinel"></span>'
        '<div class="pp-minibar gd-minibar" data-sticky-reveal data-sticky-sentinel="#mb-sentinel" data-mb-active="trophies">'
        '<div class="pp-minibar__inner container mx-auto"><span class="pp-minibar__id">'
        f'<span class="pp-minibar__title" data-minibar-page-title>Jak 3</span>{label}</span></div></div>'
        f'<div id="browse-results">{grid}</div></main>{scripts}'
        "<script>document.addEventListener('DOMContentLoaded', () => PlatPursuit.StickyReveal.init());</script>"
        '</body></html>', encoding='utf-8')
    return page.as_uri()


STATE = """() => {
  const sub = document.querySelector('.pp-sub'), bar = document.querySelector('.pp-minibar');
  const label = document.querySelector('[data-minibar-group]'), pct = document.querySelector('[data-minibar-group-pct]');
  return {
    flag: document.documentElement.classList.contains('pp-bar-pinned'),
    subVisible: getComputedStyle(sub).visibility === 'visible',
    subBottom: Math.round(sub.getBoundingClientRect().bottom),
    navBottom: Math.round(document.querySelector('.pp-nav').getBoundingClientRect().bottom),
    barTop: Math.round(bar.getBoundingClientRect().top),
    pinned: bar.classList.contains('is-pinned'),
    label: label.hidden ? null : document.querySelector('[data-minibar-group-name]').textContent,
    pct: pct.hidden ? null : pct.textContent,
    titleShown: getComputedStyle(document.querySelector('[data-minibar-page-title]')).display !== 'none',
    nameShown: document.querySelector('[data-minibar-group-name]').getBoundingClientRect().width > 0,
    headPosition: getComputedStyle(document.querySelector('.gd-group__head')).position,
  };
}"""


def _scroll_to_group(pg, index, into=300):
    pg.evaluate('([i, d]) => window.scrollTo(0, document.querySelectorAll(".gd-group")[i].offsetTop + d)', [index, into])


def _settled(pg, label):
    """Waits for the bar's transitions (0.28s) and the label to show `label` (None: hidden)."""
    pg.wait_for_function('(want) => { const l = document.querySelector("[data-minibar-group]");'
                         ' return want === null ? l.hidden : (!l.hidden && l.textContent.startsWith(want)); }', arg=label)
    pg.wait_for_timeout(450)
    return pg.evaluate(STATE)


@pytest.mark.parametrize('width,height', [(1366, 657), (390, 844)])
def test_the_bar_takes_the_rails_place_and_names_the_group(tmp_path, width, height):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        try:
            pg = b.new_page(viewport={'width': width, 'height': height})
            pg.goto(_page(tmp_path))

            # The page title: under 520px the Browse pages' rule hides every bar's title (game-browse.css), and
            # only the group name is kept there.
            title_kept = width > 520
            top = _settled(pg, None)
            assert not top['flag'] and top['subVisible'] and not top['pinned'], top
            assert top['titleShown'] == title_kept, top

            _scroll_to_group(pg, 0)
            first = _settled(pg, 'Golf With Your Friends')
            assert first['flag'] and first['pinned'], first
            assert not first['subVisible'] and first['subBottom'] <= first['navBottom'], f'the sub-nav is still down: {first}'
            assert first['barTop'] == first['navBottom'] - 1, f'the bar does not sit under the navbar: {first}'
            assert first['pct'] == '45%' and not first['titleShown'], first
            assert first['nameShown'], f'the group name is in the bar but not shown (a bare % names nothing): {first}'
            assert first['headPosition'] == 'static', f'the group header still pins: {first}'

            _scroll_to_group(pg, 1)
            second = _settled(pg, 'New Worlds Pack')
            assert second['pct'] == '100%', second

            pg.evaluate('window.scrollTo(0, 0)')
            back = _settled(pg, None)
            assert not back['flag'] and back['subVisible'], f'the sub-nav did not come back: {back}'
            assert back['titleShown'] == title_kept, back
        finally:
            b.close()


def test_signed_out_the_bar_names_the_group_without_a_percentage(tmp_path):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        try:
            pg = b.new_page(viewport={'width': 1366, 'height': 657})
            pg.goto(_page(tmp_path, signed_in=False))
            _scroll_to_group(pg, 1)
            state = _settled(pg, 'New Worlds Pack')
            assert state['pct'] is None, state
        finally:
            b.close()


def test_a_grid_that_is_not_showing_gives_the_title_back(tmp_path):
    """Another view (Ranks, Ratings) hides the grid: the bar stays pinned with its own title."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        try:
            pg = b.new_page(viewport={'width': 1366, 'height': 657})
            pg.goto(_page(tmp_path))
            _scroll_to_group(pg, 0)
            _settled(pg, 'Golf With Your Friends')
            # A view switch rewrites data-mb-active without a scroll; the other view's panel keeps the page tall.
            pg.evaluate('''() => {
                const spacer = document.createElement('div'); spacer.style.height = '6000px';
                document.getElementById('browse-results').after(spacer);
                document.getElementById('browse-results').style.display = 'none';
                document.querySelector('.pp-minibar').dataset.mbActive = 'leaderboard';
            }''')
            state = _settled(pg, None)
            assert state['pinned'] and state['titleShown'], state
        finally:
            b.close()
