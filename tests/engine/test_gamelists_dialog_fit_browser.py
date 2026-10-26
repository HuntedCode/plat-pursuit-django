"""A list dialog keeps its buttons on screen, in a real browser (review AU-03, owner 2026-10-26).

The New list dialog is meant to scroll its body between a pinned head and foot. A <form> wraps the body and the
foot, and the column never passed through it, so the whole dialog scrolled instead and the Create list button
sat under that scroll on the owner's 1280 x 633 laptop and on a phone. Renders the real My Lists page's dialog
against the built output.css, opens it, and asks whether the button is the thing actually under its own centre.

Skips with a reason where Playwright's browser is not installed (see test_trophy_controls_browser).
"""
import re
from pathlib import Path

import pytest

from tests.engine.test_gamelists_detail import _staff
from tests.engine.test_trophy_controls_browser import _SKIP_REASON

ROOT = Path(__file__).resolve().parents[2]
CSS = ROOT / 'static' / 'css' / 'output.css'
pytestmark = [pytest.mark.django_db, pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or '')]

REACHABLE = """(label) => {
  const b = [...document.querySelectorAll('dialog[open] button')].find(x => x.textContent.trim() === label);
  const r = b.getBoundingClientRect(), x = r.left + r.width / 2, y = r.top + r.height / 2;
  const hit = y < innerHeight ? document.elementFromPoint(x, y) : null;
  return {bottom: Math.round(r.bottom), visible: !!hit && (hit === b || b.contains(hit))};
}"""


@pytest.mark.parametrize('viewport', [(1280, 633), (375, 667)])
def test_the_create_button_is_on_screen_when_the_dialog_opens(client, tmp_path, viewport):
    from playwright.sync_api import sync_playwright

    _staff(client, psn='me')
    html = client.get('/my-lists/').content.decode()
    dialog = re.search(r'<dialog id="gl-create".*?</dialog>', html, flags=re.S).group(0)
    page = tmp_path / 'dialog.html'
    page.write_text(
        '<!doctype html><html data-theme="plat-pursuit-dark"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<link rel="stylesheet" href="{CSS.as_uri()}"></head><body class="bg-base-100">{dialog}'
        '<script>document.getElementById("gl-create").showModal();</script></body></html>', encoding='utf-8')
    with sync_playwright() as p:
        b = p.chromium.launch()
        try:
            pg = b.new_page(viewport={'width': viewport[0], 'height': viewport[1]})
            pg.goto(page.as_uri())
            got = pg.evaluate(REACHABLE, 'Create list')
        finally:
            b.close()
    assert got['visible'] and got['bottom'] <= viewport[1], f'Create list is off screen at {viewport}: {got}'
