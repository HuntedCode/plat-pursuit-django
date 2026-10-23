"""The trophy control row and the DLC strip in a real browser (the trophy list pass, GL-04 / GL-10).

Renders the real `_controls.html` and a real group nav (grid.html) with the real utils.js + trophy-grid.js against
the BUILT output.css in Chromium:

- More filters is the house filter drawer (PlatPursuit.filterPanel): it opens, its badge counts the filters
  inside it (a hidden DLC field is not one), and a link that arrives with one set opens it on load.
- The shared count and load bar both pages drive (PlatPursuit.TrophyControls.setCount / setBusy).
- On a phone the button keeps its name with its label hidden.
- On a phone the DLC strip scrolls sideways WITHOUT widening the layout viewport. Only real device emulation shows
  that defect (a narrow desktop viewport cannot), and the test proves its instrument can see it: something too wide
  in the same column does widen the same page.

Skips with a reason where Playwright's browser is not installed; CI installs it (see test_plat_card_layout).
"""
from pathlib import Path

import pytest
from django.template.loader import render_to_string
from django.test import RequestFactory

from trophies.forms import GameDetailForm

ROOT = Path(__file__).resolve().parents[2]
CSS = ROOT / 'static' / 'css' / 'output.css'
UTILS = ROOT / 'static' / 'js' / 'utils.js'
GRID_JS = ROOT / 'static' / 'js' / 'trophy-grid.js'


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

PACKS = ['Golf With Your Friends', 'New Worlds Pack', 'Bouncy Castle Pack', 'Corrupted Forest', 'Peaceful Pines Pack',
         'Olympus Odyssey Pack']


def _page(tmp_path, query='', show_dlc=True, dlc_toggle=False, extra_css=''):
    request = RequestFactory().get('/games/x/' + (f'?{query}' if query else ''))
    controls = render_to_string('trophies/partials/trophy_grid/_controls.html', {
        'form': GameDetailForm(request.GET), 'count': 56, 'show_earned': True, 'show_dlc': show_dlc,
        'dlc_toggle': dlc_toggle, 'list_url': '', 'panel_id': 'more', 'request': request,
        'selected_trophy_types': request.GET.getlist('trophy_type'),
        'selected_rarity_brackets': request.GET.getlist('rarity_bracket'),
    })
    groups = {f'g{i}': {'trophy_group_name': name, 'trophy_group_icon_url': '', 'defined_trophies': {'bronze': 4}}
              for i, name in enumerate(PACKS)}
    trophies = {gid: [] for gid in groups}
    grid = render_to_string('trophies/partials/trophy_grid/grid.html', {
        'trophies': trophies, 'groups': groups, 'earned': {}, 'group_pct': {}, 'group_totals': {},
        'profile': None, 'show_group_nav': True, 'id_prefix': '',
    })
    page = tmp_path / 'controls.html'
    # The page's real column: base.html's flex wrapper, then the container main -- the shape that leaks.
    page.write_text(
        '<!doctype html><html data-theme="plat-pursuit-dark"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<link rel="stylesheet" href="{CSS.as_uri()}"><style>{extra_css}</style></head><body class="bg-base-100">'
        '<div id="zoom-wrapper" style="display:flex;flex-direction:column">'
        f'<main class="container mx-auto px-4"><form id="f">{controls}</form>{grid}</main></div>'
        f'<script src="{UTILS.as_uri()}"></script><script src="{GRID_JS.as_uri()}"></script></body></html>',
        encoding='utf-8')
    return page.as_uri()


def _wait_open(pg):
    pg.wait_for_function("() => { const p = document.getElementById('more'); return !p.hidden && !p.style.height; }")


def test_more_filters_opens_and_counts_what_is_inside_it(tmp_path):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={'width': 1280, 'height': 900})
        pg.goto(_page(tmp_path))
        assert pg.locator('#more').is_hidden()
        assert pg.get_attribute('[data-gd-more]', 'aria-expanded') == 'false'
        pg.click('[data-gd-more]')
        _wait_open(pg)
        assert pg.get_attribute('[data-gd-more]', 'aria-expanded') == 'true'
        badge = pg.locator('[data-gd-more-count]')
        assert badge.is_hidden()
        pg.click('#more label.gd-chip:has-text("Gold")')
        assert badge.text_content() == '1' and badge.is_visible()
        pg.select_option('#more select[name="dlc_filter"]', 'dlc')
        assert badge.text_content() == '2'
        # Sort and All / Unearned stand on the row itself: never counted as hidden filters.
        pg.select_option('select[name="sort"]', 'alpha')
        pg.click('.gd-controls__show label:has-text("Unearned")')
        assert badge.text_content() == '2'
        b.close()


def test_a_hidden_dlc_field_is_not_a_filter(tmp_path):
    """The Game page keeps its DLC field hidden for a list without DLC: a value left in it does not apply."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.goto(_page(tmp_path, query='dlc_filter=dlc', show_dlc=False, dlc_toggle=True))
        assert pg.locator('[data-gd-dlc]').is_hidden()
        assert pg.locator('[data-gd-more-count]').is_hidden()
        assert pg.locator('#more').is_hidden(), 'nothing in force, so nothing opens'
        pg.evaluate("document.querySelector('[data-gd-dlc]').hidden = false;"
                    "PlatPursuit.TrophyControls.sync(document.querySelector('.gd-controls'))")
        assert pg.locator('[data-gd-more-count]').text_content() == '1'
        b.close()


def test_a_link_with_a_filter_set_opens_the_panel_showing_it(tmp_path):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.goto(_page(tmp_path, query='trophy_type=gold&rarity_bracket=rare'))
        assert pg.locator('#more').is_visible()
        assert pg.locator('[data-gd-more-count]').text_content() == '2'
        b.close()


def test_the_shared_count_and_load_bar(tmp_path):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.goto(_page(tmp_path))
        pg.evaluate("PlatPursuit.TrophyControls.setCount(document.querySelector('.gd-controls'), 7)")
        pg.wait_for_function("() => document.querySelector('[data-gd-count]').textContent.trim() === '7'")
        pg.evaluate("PlatPursuit.TrophyControls.setBusy(document.querySelector('.gd-controls'), true)")
        assert 'is-active' not in pg.get_attribute('[data-gd-loadbar]', 'class'), 'an instant filter shows no bar'
        pg.wait_for_timeout(300)
        assert 'is-active' in pg.get_attribute('[data-gd-loadbar]', 'class')
        pg.evaluate("PlatPursuit.TrophyControls.setBusy(document.querySelector('.gd-controls'), false)")
        assert 'is-active' not in pg.get_attribute('[data-gd-loadbar]', 'class')
        b.close()


def test_on_a_phone_the_row_fits_and_the_dlc_strip_does_not_widen_the_page(tmp_path):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(**p.devices['iPhone 12'])
        pg = ctx.new_page()
        pg.goto(_page(tmp_path))
        # The button drops its visible label on a phone but keeps its name.
        assert pg.get_by_role('button', name='More filters').is_visible()
        assert pg.locator('.gd-controls__morelbl').bounding_box()['width'] <= 1
        assert pg.evaluate("getComputedStyle(document.querySelector('.gd-groupnav')).flexWrap") == 'nowrap'
        assert pg.evaluate("(() => { const n = document.querySelector('.gd-groupnav'); return n.scrollWidth > n.clientWidth; })()"), \
            'six packs must overflow the strip, or this proves nothing'
        iw, cw = pg.evaluate('[window.innerWidth, document.documentElement.clientWidth]')
        assert iw == cw, f'the DLC strip widened the layout viewport ({iw} vs {cw})'
        ctx.close()
        # The instrument can fail: on the same emulated device, something genuinely too wide in the same column
        # does widen the layout viewport (a narrow DESKTOP viewport would report equal widths either way).
        ctx = b.new_context(**p.devices['iPhone 12'])
        pg = ctx.new_page()
        pg.goto(_page(tmp_path, extra_css='main::after { content: ""; display: block; width: 900px; height: 1px; }'))
        iw, cw = pg.evaluate('[window.innerWidth, document.documentElement.clientWidth]')
        assert iw > cw, 'the widening check cannot see a widened page; it proves nothing'
        b.close()
