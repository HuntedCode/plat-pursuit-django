"""The new-contracts modal keeps its buttons inside the dialog on a short laptop, in a real browser (2026-10-23).

The dialog is a frame capped at a share of the window, and only its list scrolls; its `overflow: visible` (for the
discipline popovers) means anything pushed past the bottom has no scrollbar to reach it by. The covers shrank on
short screens only below 1024px wide, so a laptop (wide but short: 1366x657, about 1280x633 at 150% scaling) kept
the full covers, the list fell to its floor, and "Got it" / "Open the board" hung below the dialog.

Renders the real partial (`trophies/partials/career/_new_contracts.html`) with a full wave (six heroes, five
disciplines, a long list, "and N more") against the BUILT output.css in Chromium, after the real fonts load.

Skips with a reason where Playwright's browser is not installed; CI installs it (see test_plat_card_layout).
"""
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.template.loader import render_to_string

ROOT = Path(__file__).resolve().parents[2]
CSS = ROOT / 'static' / 'css' / 'output.css'


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

DISCS = [('combat', 'Combat', 'swords'), ('exploration', 'Exploration', 'compass'), ('mind', 'Mind', 'brain'),
         ('heart', 'Heart', 'medal'), ('finesse', 'Finesse', 'gauge')]
# Two-line titles on purpose: the hero captions reserve two lines, and the wave should be the tallest it gets.
NAMES = ['A Plague Tale: Innocence', 'Airheart: Tales of Broken Wings', 'Another Crab\'s Treasure',
         'All in Abyss: Judge the Fake', 'A Hole New World', 'Adventures of Pip']


class _Jobs:
    """Stands in for the contract's jobs manager: the template calls `.all`."""

    def __init__(self, jobs):
        self._jobs = jobs

    def all(self):
        return self._jobs


def _job(i):
    slug, _, icon = DISCS[i % 5]
    return SimpleNamespace(slug=f'job-{i}', name=f'Job {i}', discipline=slug, icon=icon)


def _contract(i):
    return SimpleNamespace(name=NAMES[i % len(NAMES)] + ('' if i < 6 else f' {i}'), status='claimable',
                           max_progress=0, cover_url='', jobs=_Jobs([_job(i), _job(i + 1)]))


def _page(tmp_path):
    rows = [_contract(i) for i in range(20)]
    nc = {
        'total': 279, 'heroes': rows[:6], 'rows': rows, 'extra': 79,
        'disciplines': [{'slug': slug, 'label': label, 'count': 40,
                         'jobs': [SimpleNamespace(slug=f'{slug}-j', name=f'{label} job', icon=icon, count=40)]}
                        for slug, label, icon in DISCS],
    }
    modal = render_to_string('trophies/partials/career/_new_contracts.html', {'nc': nc, 'nc_preview': True})
    page = tmp_path / 'nc.html'
    page.write_text(
        '<!doctype html><html data-theme="plat-pursuit-dark"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<link rel="stylesheet" href="{CSS.as_uri()}"></head><body class="bg-base-100">{modal}</body></html>',
        encoding='utf-8')
    return page.as_uri()


MEASURE = """() => {
  const q = (s) => document.querySelector('#new-contracts ' + s).getBoundingClientRect();
  const dialog = q('.nc__dialog'), actions = q('.pp-howto__actions'), list = q('.nc__list');
  return {dialogTop: dialog.top, dialogBottom: dialog.bottom, actionsBottom: actions.bottom,
          list: Math.round(list.height), art: Math.round(q('.nc__hero-art').height), vh: innerHeight,
          // One row plus the gap to the next: the list's unit of "how much can I read".
          row: q('.nc__row:nth-child(2)').top - q('.nc__row:nth-child(1)').top};
}"""


def _open(tmp_path, width, height):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        try:
            pg = b.new_page(viewport={'width': width, 'height': height}, reduced_motion='reduce')
            pg.goto(_page(tmp_path))
            faces = pg.evaluate("""Promise.all([document.fonts.load('600 14px Inter'),
                                document.fonts.load('800 22px "Bricolage Grotesque"')])
                                .then((f) => document.fonts.ready.then(() => f.map((x) => x.length)))""")
            assert all(faces), f'a face did not load, so the layout would be measured in a fallback font: {faces}'
            pg.evaluate("document.getElementById('new-contracts').hidden = false")
            pg.wait_for_timeout(100)
            return pg.evaluate(MEASURE)
        finally:
            b.close()


# Laptops (the bug), a tall desktop, a short tablet, a landscape phone, and the phones the modal's earlier fit
# passes were tuned for.
@pytest.mark.parametrize('width,height', [(1280, 633), (1366, 657), (1536, 730), (1440, 790), (1920, 950),
                                          (1024, 600), (844, 390), (390, 844), (375, 667)])
def test_the_buttons_stay_inside_the_dialog(tmp_path, width, height):
    m = _open(tmp_path, width, height)
    assert m['actionsBottom'] <= m['dialogBottom'], f'"Got it" hangs {m["actionsBottom"] - m["dialogBottom"]:.0f}px below the dialog: {m}'
    # The modal keeps a gutter round the dialog; a cap past the room it leaves pushes the dialog into it.
    assert m['dialogTop'] >= 8 and m['dialogBottom'] <= m['vh'] - 8, f'the dialog runs into the screen edge: {m}'


@pytest.mark.parametrize('width,height,rows', [(1280, 633, 2), (1366, 657, 2), (1536, 730, 2.5)])
def test_a_short_laptop_still_shows_more_than_one_row_of_the_list(tmp_path, width, height, rows):
    """The list is the content; at its 72px floor it showed one row. The smaller covers and the lifted dialog cap
    buy the rows: two on the shortest laptops, two and a half by 730px."""
    m = _open(tmp_path, width, height)
    assert m['list'] >= rows * m['row'], f'the list shows {m["list"] / m["row"]:.1f} rows, wanted {rows}: {m}'


def test_a_phone_keeps_its_dialog_cap(tmp_path):
    """Phones keep 88vh: their `vh` ignores the browser toolbars, and that slack keeps the buttons clear of them."""
    m = _open(tmp_path, 375, 667)
    assert abs((m['dialogBottom'] - m['dialogTop']) - 0.88 * m['vh']) <= 2, f'a phone got the laptop cap: {m}'


def test_a_tall_desktop_keeps_the_full_covers(tmp_path):
    tall, short = _open(tmp_path, 1920, 950), _open(tmp_path, 1366, 657)
    assert tall['art'] > short['art'] + 30, f'the short-screen covers reached a tall desktop: {tall} {short}'
