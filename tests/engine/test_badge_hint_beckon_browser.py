"""Browse Badges' "How do badges work?" hint beckons once on every load, in a real browser (owner, 2026-10-24).

The page stopped opening the teaching as a first-visit modal (PR-06), which left the header hint as the only
way in, and the owner did not want it missed. So it lights to primary while a ring pings twice off its icon,
then settles back. These check what review cannot: that the beat actually runs against the BUILT output.css
(an @keyframes that loses its colour, the way lightningcss strips color-mix(), still parses), that it ENDS
rather than looping, and that reduced motion keeps the colour and drops the ring.

Renders the real page through the test client and lifts the hint's markup, so the template hook and the CSS
are tested as a pair. Skips with a reason where Playwright's browser is not installed; CI installs it.
"""
import re
from pathlib import Path

import pytest
from django.urls import reverse

from tests.engine.test_job_tiles_fit_browser import _SKIP_REASON

ROOT = Path(__file__).resolve().parents[2]
CSS = ROOT / 'static' / 'css' / 'output.css'

pytestmark = [pytest.mark.django_db, pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or '')]

ANIMS = """() => document.querySelector('.pp-edhint').getAnimations({subtree: true}).map((a) => ({
  name: a.animationName, pseudo: a.effect.pseudoElement || '', iterations: a.effect.getTiming().iterations}))"""

# Mid-beat, all animations paused: the hint's colour against a probe painted `--pp-primary` directly. Compared
# as PAINTED pixels: an interpolated colour serialises as oklab() while the token reads as oklch().
MID_BEAT = """() => {
  const hint = document.querySelector('.pp-edhint');
  for (const a of hint.getAnimations()) { a.pause(); a.currentTime = 2000; }   // 0.8s delay + half of 2.4s
  const probe = document.createElement('span'); probe.style.color = 'var(--pp-primary)'; document.body.append(probe);
  const px = (c) => { const x = document.createElement('canvas').getContext('2d'); x.fillStyle = c;
                      x.fillRect(0, 0, 1, 1); return [...x.getImageData(0, 0, 1, 1).data].join(','); };
  return [px(getComputedStyle(hint).color), px(getComputedStyle(probe).color)];
}"""


# Near the END of the beat with the pointer on the link: hover is primary, so the link must be primary too.
LATE_HOVERED = """() => {
  const hint = document.querySelector('.pp-edhint');
  for (const a of hint.getAnimations()) { a.pause(); a.currentTime = 3150; }  // 97% of the beat
  const probe = document.createElement('span'); probe.style.color = 'var(--pp-primary)'; document.body.append(probe);
  const px = (c) => { const x = document.createElement('canvas').getContext('2d'); x.fillStyle = c;
                      x.fillRect(0, 0, 1, 1); return [...x.getImageData(0, 0, 1, 1).data].join(','); };
  return [px(getComputedStyle(hint).color), px(getComputedStyle(probe).color)];
}"""


def _hint(client):
    html = client.get(reverse('badges_list')).content.decode()
    found = re.findall(r'<a class="pp-edhint[^"]*".*?</a>', html, re.S)
    assert len(found) == 1, f'expected the header hint once on Browse Badges, found {len(found)}'
    return found[0]


def _run(tmp_path, hint, script, reduced=False, hover=False):
    from playwright.sync_api import sync_playwright
    page = tmp_path / 'hint.html'
    page.write_text(
        '<!doctype html><html data-theme="plat-pursuit-dark"><head><meta charset="utf-8">'
        f'<link rel="stylesheet" href="{CSS.as_uri()}"></head><body class="bg-base-100"><div>{hint}</div>'
        '</body></html>', encoding='utf-8')
    with sync_playwright() as p:
        b = p.chromium.launch()
        try:
            pg = b.new_page()
            pg.emulate_media(reduced_motion='reduce' if reduced else 'no-preference')
            pg.goto(page.as_uri())
            if hover:
                pg.hover('.pp-edhint')
            return pg.evaluate(script)
        finally:
            b.close()


def test_the_hint_beckons_once_and_stops(client, tmp_path):
    anims = {(a['name'], a['pseudo']): a['iterations'] for a in _run(tmp_path, _hint(client), ANIMS)}
    assert anims.get(('ppEdhintBeckon', '')) == 1, f'the hint does not light up once on load: {anims}'
    assert anims.get(('ppEdhintPing', '::before')) == 2, f'the ring does not ping twice off the icon: {anims}'


def test_the_beat_actually_reaches_primary(client, tmp_path):
    hint, primary = _run(tmp_path, _hint(client), MID_BEAT)
    assert hint == primary, f'mid-beat the hint is {hint}, not primary {primary}: the keyframes lost their colour'


def test_reduced_motion_keeps_the_colour_and_drops_the_ring(client, tmp_path):
    names = [(a['name'], a['pseudo']) for a in _run(tmp_path, _hint(client), ANIMS, reduced=True)]
    assert names == [('ppEdhintBeckon', '')], f'reduced motion should be the colour beat alone: {names}'


def test_the_beat_never_drags_a_hovered_link_back_to_mute(client, tmp_path):
    """An animation outranks `:hover`, so keyframes that pinned the resting mute at 0% and 100% faded a hovered
    link to grey at the end of the beat, then snapped it back to primary when the beat ended (audit,
    2026-10-24). Without those stops the beat starts and ends at the link's own colour, hover included."""
    hint, primary = _run(tmp_path, _hint(client), LATE_HOVERED, hover=True)
    assert hint == primary, f'a hovered hint reads {hint} late in the beat, not primary {primary}'
