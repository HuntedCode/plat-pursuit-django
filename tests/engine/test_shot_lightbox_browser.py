"""The screenshot lightbox never selects its screenshots (owner, 2026-10-22, on prod).

A quick double-click on an arrow selected the screenshot beside it, and the browser tints a selected image
blue. The selection comes from the browser's double-click landing while shot-lightbox.js swaps the image
under it, so this renders the real partial with the real script against the BUILT output.css in Chromium,
opens it from a thumbnail as the page does, and double-clicks with raw mouse events. (Without the script the
double-click selects nothing, which is how a first cut of this test passed without the fix.)

Skips with a reason where Playwright's browser is not installed; CI installs it (see test_plat_card_layout).
"""
from pathlib import Path

import pytest
from django.template.loader import render_to_string

ROOT = Path(__file__).resolve().parents[2]
CSS = ROOT / 'static' / 'css' / 'output.css'
JS = ROOT / 'static' / 'js' / 'shot-lightbox.js'
# A 16x9 grey PNG: real pixels, so the image has a box to land a selection on.
SHOT = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABAAAAAJCAIAAAC0SDtlAAAAEUlEQVR4nGNoIBEwjGoYFBoAGJ/YAfh07lQAAAAASUVORK5CYII='
SELECTED_IMAGES = ("(() => { const s = getSelection(); return s.rangeCount && !s.isCollapsed"
                   " ? s.getRangeAt(0).cloneContents().querySelectorAll('img').length : 0; })()")


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


@pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or '')
def test_double_clicking_an_arrow_selects_no_screenshot(tmp_path):
    from playwright.sync_api import sync_playwright

    dialog = render_to_string('trophies/partials/game_detail/shot_lightbox.html', {
        'image_urls': {'screenshot_urls': [SHOT, SHOT, SHOT]}, 'shot_title': 'Lightbox',
    })
    page_file = tmp_path / 'lightbox.html'
    page_file.write_text(
        f'<!doctype html><html data-theme="plat-pursuit-dark"><head><link rel="stylesheet" href="{CSS.as_uri()}">'
        f'</head><body><div id="gd-shots"><button type="button" data-shot="0">Open</button></div>{dialog}'
        f'<script src="{JS.as_uri()}"></script></body></html>', encoding='utf-8')
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={'width': 1280, 'height': 800})
        page.goto(page_file.as_uri())
        page.click('#gd-shots [data-shot="0"]')
        page.wait_for_function("document.getElementById('gd-shot-modal').open"
                               " && !document.getElementById('gd-shot-modal').classList.contains('is-animating')")

        for target in ('[data-shot-next]', '[data-shot-prev]', '.gd-shotmodal__thumb >> nth=1'):
            page.evaluate('getSelection().removeAllRanges()')
            box = page.locator(target).bounding_box()
            page.mouse.dblclick(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
            page.wait_for_timeout(400)   # past the 300ms push swap
            assert page.evaluate(SELECTED_IMAGES) == 0, f'a double-click on {target} selected a screenshot'

        page.keyboard.press('Control+a')
        assert page.evaluate(SELECTED_IMAGES) == 0, 'select-all reached the screenshots'
        b.close()
