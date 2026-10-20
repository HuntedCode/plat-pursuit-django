"""The house button set and the theme colours it shares (SW-01, docs/design/reviews/README.md).

Two things a source read cannot prove:
- the BUILT stylesheet's theme block reads the `--pp-*` tokens (the build has silently dropped rules
  before, so this reads output.css, not input.css);
- a text button and an icon button of the same `pp-cta` size are the same height. The first cut was
  padding-driven and came out 1px taller than the icon square, so the two misaligned side by side.
  That is a layout fact, measured in Chromium against the real output.css and fonts.

The browser half skips with a reason where Playwright's browser is not installed; CI installs it (see
test_plat_card_layout).
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CSS = ROOT / 'static' / 'css' / 'output.css'
STATUS = ('info', 'success', 'warning', 'error', 'secondary', 'accent')


def _theme_blocks():
    """The declaration blocks of the plat-pursuit-dark theme in the built CSS. Selected by OUR theme's
    selector only: DaisyUI also emits its own default themes (one under `:root:not([data-theme])`),
    which never apply because base.html always sets data-theme="plat-pursuit-dark"."""
    css = CSS.read_text(encoding='utf-8')
    return [body for sel, body in re.findall(r'([^{}]*)\{([^{}]*)\}', css)
            if '[data-theme=plat-pursuit-dark]' in sel]


def test_the_built_theme_reads_the_house_status_colours():
    blocks = _theme_blocks()
    assert blocks, 'the plat-pursuit-dark theme block is missing from output.css'
    for body in blocks:
        for name in STATUS:
            assert f'--color-{name}:var(--pp-{name})' in body, (
                f'--color-{name} no longer reads --pp-{name}; two shades of {name} again')


def _browser_missing():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return 'playwright not installed'
    try:
        with sync_playwright() as p:
            p.chromium.launch().close()
        return None
    except Exception as exc:  # noqa: BLE001 -- any launch failure means we cannot measure
        return f'playwright chromium unavailable ({type(exc).__name__}); run `playwright install chromium`'


_SKIP_REASON = _browser_missing()
browser = pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or '')

ICON = '<svg viewBox="0 0 24 24"><path d="M6 6l12 12"/></svg>'
SPECIMEN = ''.join(
    f'<div data-size="{size}"><button class="pp-cta {mod}">Copy code</button>'
    f'<button class="pp-cta {mod} pp-cta--icon" aria-label="Copy">{ICON}</button>'
    f'<button class="pp-cta {mod} pp-cta--bare">Cancel</button></div>'
    for size, mod in (('default', ''), ('sm', 'pp-cta--sm'), ('xs', 'pp-cta--xs')))


@browser
def test_text_and_icon_buttons_of_a_size_line_up(tmp_path):
    from playwright.sync_api import sync_playwright

    page_file = tmp_path / 'buttons.html'
    page_file.write_text(
        f'<!doctype html><html data-theme="plat-pursuit-dark"><head><link rel="stylesheet" href="{CSS.as_uri()}">'
        f'</head><body>{SPECIMEN}</body></html>', encoding='utf-8')
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={'width': 800, 'height': 600})
        page.goto(page_file.as_uri())
        faces = page.evaluate("document.fonts.load('700 12px Inter').then(f => document.fonts.ready.then(() => f.length))")
        assert faces > 0, 'Inter did not load, so heights would be measured in a fallback font'
        rows = page.evaluate("""() => [...document.querySelectorAll('[data-size]')].map(row => ({
            size: row.dataset.size,
            heights: [...row.querySelectorAll('button')].map(b => b.getBoundingClientRect().height),
            iconSquare: (() => { const r = row.querySelector('.pp-cta--icon').getBoundingClientRect();
                                 return Math.round(r.width) === Math.round(r.height); })() }))""")
        b.close()
    expected = {'default': 36, 'sm': 28, 'xs': 22}
    for row in rows:
        assert row['iconSquare'], f"{row['size']}: the icon button is not square"
        # Unrounded heights. The small sizes are a FIXED height, so they must match exactly (they were 1px
        # apart, which shows). The default stays padding-driven, so existing buttons keep their shipped
        # size: its text button sits ~0.4px under the 36px icon square, below what a screen can show.
        tolerance = 0.5 if row['size'] == 'default' else 0.01
        assert max(row['heights']) - min(row['heights']) < tolerance, f"{row['size']}: text and icon buttons differ {row['heights']}"
        assert abs(max(row['heights']) - expected[row['size']]) < 0.01, f"{row['size']}: {row['heights']}px"
