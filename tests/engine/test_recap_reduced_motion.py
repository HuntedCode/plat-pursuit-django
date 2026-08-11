"""Reduced motion must remove MOVEMENT, never content or behaviour.

Two live bugs sat in the recap for anyone with `prefers-reduced-motion: reduce`:

1. `.stagger-item` rests at `opacity: 0` and is revealed BY its animation. The reduced-motion block
   killed the animation and never restored the end state, so every platinum card, badge row,
   personal-best chip and calendar day rendered permanently invisible.
2. The calendar's platinum-day click handlers were registered inside `animateCalendarSlide`, which is
   only reached when motion is allowed -- so the preference removed a FEATURE, not just its movement.

Both are asserted here against the real source. The CSS half additionally needs the override to sit
AFTER the rule it beats: the first attempt at this fix put it in the main reduced-motion block, which is
declared earlier in the file, so at equal specificity the base `opacity: 0` won and the fix did nothing.
That was only caught by rendering with the preference on, which no unit test here can do -- so this
pins the ORDER, which is the part that silently breaks.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DECK_CSS = ROOT / 'static' / 'css' / 'components' / 'recap-deck.css'
CONTROLLER = ROOT / 'static' / 'js' / 'monthly-recap.js'


@pytest.fixture(scope='module')
def deck_css():
    # The deck's CSS lives in its own component file now; it used to be a <style> block inside
    # monthly_recap.html (and a second one inside the calendar slide partial).
    return DECK_CSS.read_text(encoding='utf-8')


def test_stagger_items_are_restored_under_reduced_motion(deck_css):
    """The override must exist at all."""
    blocks = re.findall(r'@media \(prefers-reduced-motion: reduce\) \{(.*?)\n    \}', deck_css, re.S)

    assert any('.stagger-item' in b and 'opacity: 1' in b for b in blocks), (
        'reduced motion kills the stagger animation; something must restore opacity, or the content '
        'is invisible'
    )


def test_the_restore_comes_after_the_rule_it_overrides(deck_css):
    """Order is the whole fix. Same specificity, so the later rule wins -- and the base `.stagger-item`
    sits in the middle of the file. An override declared above it is dead code that reads as a fix."""
    base = re.search(r'\n    \.stagger-item \{[^}]*opacity: 0', deck_css)
    assert base, 'base .stagger-item rule (opacity: 0) not found'

    restores = [
        m.start() for m in re.finditer(r'@media \(prefers-reduced-motion: reduce\)', deck_css)
        if '.stagger-item' in deck_css[m.start():m.start() + 400]
        and 'opacity: 1' in deck_css[m.start():m.start() + 400]
    ]

    assert restores, 'no reduced-motion block restores .stagger-item'
    assert max(restores) > base.start(), (
        'the reduced-motion restore is declared BEFORE the opacity:0 base rule, so it loses on source '
        'order and the content stays invisible'
    )


def test_platinum_day_handlers_are_not_registered_inside_the_animation():
    """Interactivity must not live in a code path that a motion preference skips."""
    js = CONTROLLER.read_text(encoding='utf-8')

    anim = re.search(r'animateCalendarSlide\(slideEl\) \{(.*?)\n    \}\n', js, re.S)
    assert anim, 'animateCalendarSlide not found'
    assert 'showPlatinumDetails' not in anim.group(1), (
        'the platinum-day click handler is registered inside the animation, which is skipped under '
        'prefers-reduced-motion -- the preference would remove the feature, not just its motion'
    )


def test_interactions_are_wired_outside_the_motion_path():
    """...and the wiring that replaced it runs unconditionally, before the early-returning animator."""
    js = CONTROLLER.read_text(encoding='utf-8')

    wire = re.search(r'wireSlideInteractions\(slideEl, slideType\) \{(.*?)\n    \}\n', js, re.S)
    assert wire, 'wireSlideInteractions not found'
    assert 'showPlatinumDetails' in wire.group(1)
    # It must not inherit the animator's guard.
    assert 'prefersReducedMotion' not in wire.group(1), 'wiring must not depend on motion preference'
    # And it must be invoked before the animation call it replaced part of.
    assert js.index('this.wireSlideInteractions(') < js.index('this.triggerSlideAnimations(slideEl, slideType);')
