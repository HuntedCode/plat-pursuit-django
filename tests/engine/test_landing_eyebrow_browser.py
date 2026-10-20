"""The landing eyebrow at phone widths, in a real browser (FV-18, docs/design/reviews/first-visit.md).

At 320px the hero's "For PlayStation trophy hunters" outgrew its line at the eyebrow's 0.18em tracking,
wrapped, and left the tick stranded beside the first line. A width question needs the real fonts and the
real stylesheet, so this loads the REAL `output.css` from a file URL (its `@font-face` URLs resolve to the
shipped woff2 files) and the hero's REAL eyebrow lifted from `home/landing.html`, inside a copy of the hero's
wrapper classes (`land-hero` > `container mx-auto px-4` > `land-hero__inner`), which set its width.

Skips with a reason where the Playwright browser is not installed; CI installs it (see test_plat_card_layout).
"""
import re
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


def _hero_eyebrow():
    """The hero eyebrow exactly as the template ships it, wrapper included."""
    src = (ROOT / 'templates/home/landing.html').read_text(encoding='utf-8')
    match = re.search(r'<p class="land-eyebrow">.*?For PlayStation trophy hunters</p>', src)
    assert match, 'the hero eyebrow moved; update this test with it'
    return ('<section class="land-hero"><div class="container mx-auto px-4"><div class="land-hero__inner">'
            f'{match.group(0)}</div></div></section>')


def _text_lines(tmp_path, width):
    from playwright.sync_api import sync_playwright

    css = (ROOT / 'static/css/output.css').as_uri()
    page_file = tmp_path / 'eyebrow.html'
    page_file.write_text(
        f'<!doctype html><html data-theme="plat-pursuit-dark"><head><link rel="stylesheet" href="{css}">'
        f'</head><body>{_hero_eyebrow()}</body></html>', encoding='utf-8')
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': width, 'height': 600})
        page.goto(page_file.as_uri())
        # Load the eyebrow's real face explicitly: `fonts.ready` can resolve before the swap, and the
        # fallback is NARROWER, so measuring early would pass a line that wraps in production. A
        # missing font file resolves to an empty list rather than an error, so count the faces.
        faces = page.evaluate("document.fonts.load('700 12px Inter').then(f => document.fonts.ready.then(() => f.length))")
        assert faces > 0, 'Inter did not load, so this would measure the narrower fallback'
        lines = page.evaluate("""() => {
            const e = document.querySelector('.land-eyebrow');
            const text = [...e.childNodes].find(n => n.nodeType === 3 && n.textContent.trim());
            const r = document.createRange(); r.selectNodeContents(text);
            return new Set([...r.getClientRects()].map(x => Math.round(x.top))).size; }""")
        browser.close()
    return lines


@pytest.mark.parametrize('width', [320, 375])
def test_the_hero_eyebrow_stays_on_one_line_on_phones(tmp_path, width):
    assert _text_lines(tmp_path, width) == 1
