"""The claim ceremony fits the screen it plays on, in a real browser (the laptop overlap bug, 2026-10-23).

The job list never scrolls and is the one block in the card that shrinks when the card hits its height cap. On a
laptop (wide, so it took the roomy tiles and the summary, but short) a page of 5 overflowed the shrunken list and
its last tiles painted under the Pursuer ladder and the pager. Plays the real claim-ceremony.js against the BUILT
output.css in Chromium, with reduced motion so it settles at once, and checks:

- A laptop-height window fits a full page of 5 with no tile outside the list (compact tiles, no summary, the
  tighter short-screen spacing).
- A window too short even for that gets fewer jobs a page rather than a spill (fitPageSize).
- A tall desktop keeps the roomy tiles and the "Tiers earned" summary.

Skips with a reason where Playwright's browser is not installed; CI installs it (see test_plat_card_layout).
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CSS = ROOT / 'static' / 'css' / 'output.css'
CEREMONY_JS = ROOT / 'static' / 'js' / 'claim-ceremony.js'


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

DISCS = ['combat', 'exploration', 'mind', 'heart', 'finesse']
APPRENTICE = {'key': 'apprentice', 'name': 'Apprentice', 'level': 10, 'rank': 1}


def _payload(count):
    """A claim of `count` jobs, every one crossing a tier so the summary has something to show."""
    jobs = [{'slug': f'job-{i}', 'name': f'Job {i}', 'disc': DISCS[i % 5], 'icon': 'swords', 'xp': 6000,
             'from_level': 8, 'to_level': 11, 'from_frac': 0.2, 'to_frac': 0.5, 'tier': 'Apprentice',
             'tiers': [APPRENTICE]} for i in range(count)]
    rungs = [{'key': 'newbie', 'name': 'Newbie', 'reached': True, 'current': False},
             {'key': 'recruit', 'name': 'Recruit', 'reached': True, 'current': True},
             {'key': 'seeker', 'name': 'Seeker', 'reached': False, 'current': False}]
    ladder = {'rungs': rungs, 'fill': 40,
              'current': {'key': 'recruit', 'label': 'Recruit II', 'division': 2, 'next_label': 'Recruit I', 'levels_to_next': 3}}
    return {'xp': count * 6000, 'accepted': ['c'], 'first_claim': False, 'rank_now': 'Recruit II', 'jobs': jobs,
            'pursuer': {'from_level': 20, 'to_level': 21, 'rank_up': False, 'div_up': False, 'ranks': [], 'ladder': ladder}}


MEASURE = """() => {
  const jobs = document.querySelector('.ccx__jobs').getBoundingClientRect();
  const tiles = Array.from(document.querySelectorAll('.ccx__job')).map((t) => t.getBoundingClientRect());
  const ladder = document.querySelector('.ccx__rankbar').getBoundingClientRect();
  const stage = document.querySelector('.ccx__stage').getBoundingClientRect();
  return {perPage: tiles.length, pages: document.querySelectorAll('.ccx__dot').length,
          spill: Math.max(...tiles.map((t) => t.bottom)) - jobs.bottom,
          ladderGap: ladder.top - Math.max(...tiles.map((t) => t.bottom)),
          stageInView: stage.top >= 0 && stage.bottom <= innerHeight,
          tileH: tiles[0].height,
          summary: getComputedStyle(document.querySelector('.ccx__summary')).display};
}"""


@pytest.fixture(scope='module')
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def _play(browser, tmp_path, width, height, count):
    page_file = tmp_path / 'ceremony.html'
    page_file.write_text(
        '<!doctype html><html data-theme="plat-pursuit-dark"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<link rel="stylesheet" href="{CSS.as_uri()}"></head><body class="bg-base-100">'
        f'<script src="{CEREMONY_JS.as_uri()}"></script></body></html>', encoding='utf-8')
    ctx = browser.new_context(viewport={'width': width, 'height': height}, reduced_motion='reduce')
    pg = ctx.new_page()
    pg.goto(page_file.as_uri())
    pg.evaluate('(p) => { window.PlatPursuit.ClaimCeremony.play(p); }', _payload(count))
    pg.wait_for_selector('.ccx.is-static')
    result = pg.evaluate(MEASURE)
    ctx.close()
    return result


@pytest.mark.parametrize('width,height', [(1280, 633), (1366, 657), (1536, 730)])
@pytest.mark.parametrize('count', [5, 13])
def test_a_laptop_fits_a_full_page_with_nothing_under_the_ladder(browser, tmp_path, width, height, count):
    m = _play(browser, tmp_path, width, height, count)
    assert m['spill'] <= 0, f'a tile overflows the job list by {m["spill"]}px, under the ladder: {m}'
    assert m['ladderGap'] >= 0, f'the ladder overlaps the last tile: {m}'
    assert m['stageInView'], m
    assert m['perPage'] == 5, f'a laptop fell back to fewer than 5 jobs a page (each claim gains a page turn): {m}'
    assert m['summary'] == 'none', f'the summary (the unbounded block) shows on a short screen: {m}'
    assert m['pages'] == (0 if count == 5 else 3), m


# 615px fits 5 tiles only WITHOUT the pager, so it catches a fit measured before the pager is in place.
@pytest.mark.parametrize('height', [520, 615])
def test_a_screen_too_short_for_five_pages_fewer_instead_of_spilling(browser, tmp_path, height):
    m = _play(browser, tmp_path, 1280, height, 13)
    assert 1 <= m['perPage'] < 5, f'expected a smaller page on a {height}px window: {m}'
    assert m['spill'] <= 0 and m['ladderGap'] >= 0, m
    assert m['pages'] == -(-13 // m['perPage']), f'the pager does not cover every job: {m}'


def test_a_tall_desktop_keeps_the_roomy_tiles_and_the_summary(browser, tmp_path):
    tall = _play(browser, tmp_path, 1920, 950, 5)
    short = _play(browser, tmp_path, 1366, 657, 5)
    assert tall['perPage'] == 5 and tall['spill'] <= 0, tall
    assert tall['summary'] == 'flex', f'the tall desktop lost the Tiers earned summary: {tall}'
    assert tall['tileH'] > short['tileH'], f'the roomy tile is not roomier than the short screen tile: {tall} {short}'

