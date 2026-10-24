"""A job page's contract cards never break a job's name mid-word, in a real browser (PR-01, 2026-10-24).

The job-scoped card names each job it levels in a tile three to a row. The page's grid gives a card 299 to
318px on a laptop or a 4-up desktop and 355px on a tablet, too narrow for three tiles to hold "Cartographer"
(67px at 10px bold), and `overflow-wrap: anywhere` broke names into "Gunsling / er". The card is now a
container: under 365px its tiles take two columns, centred.

Renders a real job page through the test client, takes its contract cards, and lays one at a time out at the
card widths the grid produces, against the BUILT output.css in Chromium, after the real fonts load.

Skips with a reason where Playwright's browser is not installed; CI installs it (see test_plat_card_layout).
"""
import re
from pathlib import Path

import pytest

from trophies.models import Contract, Job

ROOT = Path(__file__).resolve().parents[2]
CSS = ROOT / 'static' / 'css' / 'output.css'

pytestmark = pytest.mark.django_db


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
pytestmark = [pytestmark, pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or '')]

# The catalogue's longest names (the 25 jobs are seeded by migration), so every width is tested at its worst.
JOBS = ['cartographer', 'mastermind', 'gunslinger', 'survivalist', 'pathfinder', 'infiltrator']


def _contract_game(contract):
    """A real member game: membership is derived from an anchored concept trusted-matched on the contract's
    igdb id (see test_jobs_surface._contract_game)."""
    from django.utils import timezone
    from tests.factories import ConceptFactory, GameFactory, IGDBMatchFactory

    concept = ConceptFactory(unified_title=contract.name, anchor_migration_completed_at=timezone.now())
    IGDBMatchFactory(concept=concept, igdb_id=contract.igdb_id)
    GameFactory(concept=concept)


def _cards(client):
    """The cartographer page's contract cards: one levelling each of 2 to 6 jobs."""
    jobs = [Job.objects.get(slug=s) for s in JOBS]
    for n in range(2, 7):
        c = Contract.objects.create(name=f'Six {n}', slug=f'six-{n}', igdb_id=97000 + n, is_live=True)
        c.jobs.set(jobs[:n])
        _contract_game(c)
    html = client.get('/jobs/cartographer/').content.decode()
    cards = re.findall(r'(<(article|div)[^>]*class="[^"]*\brp-row\b.*?)(?=<(?:article|div)[^>]*class="[^"]*\brp-row\b|<div[^>]*id="jobd-sentinel|$)',
                       html.split('id="jobd-contracts"', 1)[1], re.S)
    assert len(cards) == 5, f'expected five contract cards on the page, found {len(cards)}'
    return [c[0] for c in cards]


MEASURE = """() => [...document.querySelectorAll('.rp-row')].map((card) => {
  const lines = (n) => { const r = document.createRange(); r.selectNodeContents(n);
                         return new Set([...r.getClientRects()].map((x) => Math.round(x.top))).size; };
  const tiles = [...card.querySelectorAll('.rp-jobtile')];
  return {n: tiles.length,
          broken: tiles.map((t) => t.querySelector('.rp-jobtile__name')).filter((e) => lines(e) > 1).map((e) => e.textContent.trim()),
          shown: tiles.filter((t) => t.getClientRects().length).length,
          rows: new Set(tiles.map((t) => Math.round(t.getBoundingClientRect().top))).size,
          // A lone tile on its row should sit centred under the pair above it, not hang at the left.
          lastOffset: tiles.length ? Math.round((tiles.at(-1).getBoundingClientRect().left + tiles.at(-1).getBoundingClientRect().right) / 2
                                                - (card.querySelector('.rp-jobtiles').getBoundingClientRect().left + card.querySelector('.rp-jobtiles').getBoundingClientRect().right) / 2) : 0};
})"""


def _layout(tmp_path, cards, width, viewport=1440):
    from playwright.sync_api import sync_playwright
    page = tmp_path / 'cards.html'
    page.write_text(
        '<!doctype html><html data-theme="plat-pursuit-dark"><head><meta charset="utf-8">'
        f'<link rel="stylesheet" href="{CSS.as_uri()}"></head><body class="bg-base-100">'
        f'<div class="grid gap-3" style="width:{width}px">{"".join(cards)}</div></body></html>', encoding='utf-8')
    with sync_playwright() as p:
        b = p.chromium.launch()
        try:
            pg = b.new_page(viewport={'width': viewport, 'height': 900})
            pg.goto(page.as_uri())
            faces = pg.evaluate("Promise.all([document.fonts.load('700 10px Inter')])"
                                ".then((f) => document.fonts.ready.then(() => f.map((x) => x.length)))")
            assert all(faces), f'Inter did not load, so names would be measured in a fallback font: {faces}'
            return pg.evaluate(MEASURE)
        finally:
            b.close()


# The card widths the job page's grid produces: 4-up at 1280 and 1440, 3-up at 1024, 2-up on a tablet, and the
# 4-up wide desktops that just fit three across.
@pytest.mark.parametrize('width', [299, 303, 318, 340, 355, 363, 367, 410])
def test_no_job_name_breaks_mid_word(client, tmp_path, width):
    for card in _layout(tmp_path, _cards(client), width):
        assert not card['broken'], f'a {card["n"]}-job card {width}px wide breaks {card["broken"]} mid-word'


def test_a_narrow_card_takes_two_columns_and_centres_the_odd_tile(client, tmp_path):
    by_n = {c['n']: c for c in _layout(tmp_path, _cards(client), 299)}
    assert by_n[3]['rows'] == 2 and by_n[6]['rows'] == 3 and by_n[2]['rows'] == 1, by_n
    assert abs(by_n[3]['lastOffset']) <= 2 and abs(by_n[5]['lastOffset']) <= 2, f'the odd tile hangs off-centre: {by_n}'


def test_a_card_with_room_keeps_three_across(client, tmp_path):
    """The wide desktop and the 2-up tablet over 790px keep the original three-wide grid."""
    by_n = {c['n']: c for c in _layout(tmp_path, _cards(client), 410)}
    assert by_n[3]['rows'] == 1 and by_n[6]['rows'] == 2, f'a card with room for three dropped to two: {by_n}'


def test_a_phone_keeps_its_list_row_without_tiles(client, tmp_path):
    """Under 640px the card is the phone list row, which hides the tiles. The narrow-card rule matched the
    phone row too (equal specificity, later in the file) and brought them back as full-width blocks."""
    for card in _layout(tmp_path, _cards(client), 343, viewport=390):
        assert card['shown'] == 0, f'a {card["n"]}-job phone row shows {card["shown"]} job tiles: {card}'
