"""Roadmap phase chips and section stripes, in a real browser (the review leftovers, SW-01).

WHY A BROWSER: the phase headers are built by roadmap-detail.js and the editor's pill by roadmap_editor.js,
and the repo has no JS runner. The colours are only real once the stylesheet resolves `--pp-<tone>` and
its fallback. So this loads the REAL `output.css`, the REAL roadmap-detail.js (driven through its own sort
control) and the REAL `_updatePhasePill` lifted from the editor, with the REAL phase data from Python.
"""
import json
from pathlib import Path

import pytest

from trophies.util_modules.trophy_phases import phases_by_key, phases_for_template

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

CSS = (ROOT / 'static/css/output.css').as_uri()
DETAIL = (ROOT / 'static/js/roadmap-detail.js').read_text(encoding='utf-8')
EDITOR = (ROOT / 'static/js/roadmap_editor.js').read_text(encoding='utf-8')
_PILL_START = EDITOR.index('    function _updatePhasePill(pillEl, phaseKey) {')
PILL_FN = EDITOR[_PILL_START:EDITOR.index('\n    }\n', _PILL_START) + 7]

# (trophy id, type, phase): a platinum, a coloured phase, and a quiet one.
ITEMS = [(1, 'platinum', ''), (2, 'gold', 'story'), (3, 'bronze', 'grindy'), (4, 'silver', 'story')]
COLOUR = """(value) => { const s = document.createElement('span'); s.style.color = value;
    document.body.appendChild(s); const c = getComputedStyle(s).color; s.remove(); return c; }"""


def _page(tmp_path, body, script, name):
    path = tmp_path / name
    path.write_text(f'<!doctype html><html data-theme="plat-pursuit-dark"><head><link rel="stylesheet" href="{CSS}">'
                    f'</head><body>{body}<script>{script}</script></body></html>', encoding='utf-8')
    return path.as_uri()


@pytest.fixture
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def test_grouped_sort_headers_wear_the_phase_tone(tmp_path, browser):
    items = ''.join(f'<details class="trophy-guide-item" data-order="{i}" data-type="{t}" data-phase="{ph}" '
                    f'data-earned="0"><summary>Trophy {i}</summary></details>' for i, t, ph in ITEMS)
    toc = ''.join(f'<li class="toc-trophy-entry" data-trophy-id="{i}">Trophy {i}</li>' for i, _, _ in ITEMS)
    body = (f'<script type="application/json" id="roadmap-trophy-phases">{json.dumps(phases_for_template())}</script>'
            '<select class="trophy-guide-sort"><option value="default">Default</option>'
            '<option value="phase">Phase</option></select>'
            f'<div class="trophy-guides-list">{items}</div><ul id="toc-trophy-guides">{toc}</ul>')
    page = browser.new_page(viewport={'width': 1280, 'height': 900})
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.goto(_page(tmp_path, body, DETAIL + '\nPlatPursuit.RoadmapDetail.init();', 'roadmap.html'))
    page.select_option('.trophy-guide-sort', 'phase')
    headers = page.evaluate("""() => [...document.querySelectorAll('.phase-section-header')].map(h => ({
        key: h.dataset.phaseKey, stripe: getComputedStyle(h).borderLeftColor,
        chip: h.querySelector('.bd-chip').className }))""")
    toc_stripes = page.evaluate("""() => Object.fromEntries([...document.querySelectorAll('.toc-phase-header')]
        .map(li => [li.dataset.phaseKey, getComputedStyle(li).borderLeftColor]))""")
    info, mute = page.evaluate(COLOUR, 'var(--pp-info)'), page.evaluate(COLOUR, 'var(--pp-text-mute)')
    assert not errors, errors

    by_key = {h['key']: h for h in headers}
    assert list(by_key) == ['platinum', 'story', 'grindy'], headers
    assert by_key['platinum']['chip'].split() == ['bd-chip', 'bd-chip--info', 'ml-auto']
    assert by_key['story']['chip'].split() == ['bd-chip', f'bd-chip--{phases_by_key()["story"]["tone"]}', 'ml-auto']
    assert by_key['story']['stripe'] == info, 'the coloured phase lost its stripe'
    # The quiet tone has no colour token: the stripe must fall back, not go invalid (and so invisible).
    assert by_key['grindy']['stripe'] == mute, f'the quiet phase stripe did not fall back: {by_key["grindy"]}'
    assert toc_stripes == {'platinum': info, 'story': info, 'grindy': mute}, toc_stripes


@pytest.mark.parametrize('phase, expect', [
    ('story', {'hidden': False, 'tone': 'bd-chip--info', 'text': 'Story'}),
    ('', {'hidden': False, 'tone': 'bd-chip--warning', 'text': 'No phase'}),
    ('retired-phase', {'hidden': False, 'tone': 'bd-chip--warning', 'text': 'retired-phase'}),
    (None, {'hidden': True, 'tone': None, 'text': ''}),
])
def test_the_editor_pill_is_a_house_chip_and_really_hides(tmp_path, browser, phase, expect):
    """Each update starts from a pill an earlier update SHOWED, so the template's starting `hidden`
    attribute cannot hide a broken hide. Hiding has to use the attribute: the chip's own `display`
    outranks the `.hidden` utility."""
    template = (ROOT / 'templates/trophies/roadmap_edit.html').read_text(encoding='utf-8')
    pill = template[template.index('<span class="trophy-guide-phase-pill'):]
    pill = pill[:pill.index('</span>') + 7]
    script = (f'const trophyPhasesByKey = {json.dumps(phases_by_key())};\n{PILL_FN}\n'
              "const pill = document.querySelector('.trophy-guide-phase-pill');\n"
              "_updatePhasePill(pill, 'combat');\n"
              f'_updatePhasePill(pill, {json.dumps(phase)});')
    page = browser.new_page()
    page.goto(_page(tmp_path, pill, script, 'pill.html'))
    got = page.evaluate("""() => { const p = document.querySelector('.trophy-guide-phase-pill');
        return { hidden: getComputedStyle(p).display === 'none',
                 tone: [...p.classList].find(c => c.startsWith('bd-chip--')) || null,
                 text: p.textContent.trim() }; }""")
    assert got['hidden'] == expect['hidden'] and got['tone'] == expect['tone'], got
    assert expect['text'] in got['text'], got
