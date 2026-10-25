"""Game flag badges draw in a real browser, against the BUILT output.css (owner, 2026-10-24).

What a source check cannot see: a `<use>` copy inherits from its symbol and its host, never from the sprite around
the original, so a glyph that lost its stroke attributes renders as a FILLED BLOB, and a struck-out glyph's cut is
only a gap if `--pp-gflag-cut` reaches into the copy. Both are painted-pixel questions.

Renders the real sprite and the real tag into a page with the real stylesheet. Skips with a reason where
Playwright's browser is not installed; CI installs it.
"""
from pathlib import Path

import pytest
from django.template import Context, Template

from tests.engine.test_job_tiles_fit_browser import _SKIP_REASON
from trophies.services.flag_badges import FLAG_ORDER

ROOT = Path(__file__).resolve().parents[2]
CSS = ROOT / 'static' / 'css' / 'output.css'
SPRITE = ROOT / 'templates' / 'partials' / 'icons' / '_game_flag_sprite.html'

pytestmark = pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or '')

def _page(tmp_path, cut=None, chips=False):
    """One badge per flag, drawn by the real tag at 10x (`zoom`) so a 2-unit stroke is many pixels wide, or with
    `chips` the Game pages' labelled chips. `cut` overrides `--pp-gflag-cut` on each one: a colour, or 'plate' for
    the element's own painted background (so a correct default and a forced plate must paint identically)."""
    sprite = Template(SPRITE.read_text(encoding='utf-8')).render(Context())
    if chips:
        probe = Template('<div data-k="{{ k }}" class="probe">{% include "trophies/partials/_game_flags.html" '
                         'with flags=keys %}</div>')
    else:
        probe = Template('{% load flag_badges %}<div data-k="{{ k }}" class="probe">'
                         '{% flag_badges keys "md" "row" %}</div>')
    html = ''.join(probe.render(Context({'k': k, 'keys': (k,)})) for k in FLAG_ORDER)
    style = '.probe{display:inline-block;margin:12px;zoom:10}' + (
        f'.probe .pp-gflag,.probe .pp-gflag-chip{{--pp-gflag-cut:{cut}}}' if cut and cut != 'plate' else '')
    script = ('<script>for (const el of document.querySelectorAll(".pp-gflag, .pp-gflag-chip")) '
              '{ el.style.setProperty("--pp-gflag-cut", getComputedStyle(el).backgroundColor); }</script>'
              if cut == 'plate' else '')
    page = tmp_path / ('chips.html' if chips else 'flags.html')
    page.write_text(
        '<!doctype html><html data-theme="plat-pursuit-dark"><head><meta charset="utf-8">'
        f'<link rel="stylesheet" href="{CSS.as_uri()}"><style>{style}</style></head>'
        f'<body class="bg-base-100">{sprite}{html}{script}</body></html>', encoding='utf-8')
    return page


def _shots(tmp_path, cut=None, chips=False):
    """{key: PIL image of the icon} for every flag."""
    from io import BytesIO

    from PIL import Image
    from playwright.sync_api import sync_playwright
    page = _page(tmp_path, cut, chips)
    icon = '.pp-gflag-chip svg' if chips else '.pp-gflag__i'
    with sync_playwright() as p:
        b = p.chromium.launch()
        try:
            pg = b.new_page(viewport={'width': 2400, 'height': 1600})
            pg.goto(page.as_uri())
            return {k: Image.open(BytesIO(pg.locator(f'[data-k="{k}"] {icon}').screenshot())).convert('RGB')
                    for k in FLAG_ORDER}
        finally:
            b.close()


def _share(img, test):
    px = list(img.getdata())
    return sum(1 for p in px if test(p)) / len(px)


def test_every_glyph_draws_as_a_stroke_not_a_blob(tmp_path):
    """Some of the icon box is the glyph and most of it is the plate. A lost `fill="none"` paints the closed shapes
    solid (well over half the box); a lost stroke paints nothing at all."""
    for key, img in _shots(tmp_path).items():
        colours = img.getcolors(maxcolors=1 << 20)
        plate = max(colours)[1]                                   # the commonest colour is the plate
        drawn = _share(img, lambda p, q=plate: sum(abs(a - b) for a, b in zip(p, q)) > 60)
        assert 0.08 < drawn < 0.5, f'{key}: {drawn:.0%} of the icon is drawn'


def test_the_struck_out_glyphs_cut_their_slash_through_the_badge(tmp_path):
    """The cut is `--pp-gflag-cut`, set on the badge and inherited into the `<use>` copy. Set it to a probe lime (no
    tone is green) and lime must appear on exactly the two struck-out glyphs, so the cut reaches the copy."""
    lime = lambda p: p[1] > 200 and p[0] < 60 and p[2] < 60   # noqa: E731
    for key, img in _shots(tmp_path, cut='rgb(0, 255, 0)').items():
        share = _share(img, lime)
        if key in ('delisted', 'unobtainable'):
            assert share > 0.02, f'{key}: the cut never reached the copy'
        else:
            assert share == 0, f'{key} has no slash, so no cut'


@pytest.mark.parametrize('chips', [False, True], ids=['badge', 'game-page-chip'])
def test_by_default_the_cut_is_the_surface_it_sits_on(tmp_path, chips):
    """The gap must read as a gap: by default the cut paints exactly the badge's plate (or, on the Game pages, the
    chip's own surface). Forcing the cut to the element's painted background must change NO pixel; a cut that fell
    back to `--pp-bg-1`, or a chip recipe that drifted from chips.css, paints a dark stripe and fails."""
    default, forced = _shots(tmp_path, chips=chips), _shots(tmp_path, cut='plate', chips=chips)
    for key in ('delisted', 'unobtainable'):
        diff = sum(1 for a, b in zip(default[key].getdata(), forced[key].getdata())
                   if sum(abs(x - y) for x, y in zip(a, b)) > 12)
        assert diff == 0, f'{key}: {diff} pixels of the slash gap are not the surface colour'
