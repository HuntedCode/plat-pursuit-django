"""The `.pp-cta` primitive's INERT contract, which is the one part of it a reader cannot see is broken.

Two pages deliberately mark a button inert with `aria-disabled="true"` rather than the native
`disabled` attribute, and both write the reasoning down: a disabled button takes no pointer events (so
a `title` never appears) and leaves the tab order (so it cannot be reached by keyboard), which makes the
reason for the refusal unreachable. `my_challenges.html` does it for the beta gate; `my_lists.html` does
it for the list cap.

Neither noticed that every inert style in `cta.css` keyed on the NATIVE `:disabled` pseudo-class. Both
buttons kept full opacity, `cursor: pointer`, the hover glow and the press transform -- they looked
completely live while refusing to do anything, and said so only after a press. Lists shipped like that.

This is pinned as SOURCE TEXT because the failure is invisible in every other way: the markup is right,
the tests all pass, the page renders, and only a human looking at the screen can tell. A refactor of
`cta.css` that drops the attribute clause would silently regress both pages, and nothing else would
notice. (The repo already pins CSS contracts this way -- see `test_list_spotlight`.)
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CTA = (ROOT / 'static' / 'css' / 'components' / 'cta.css').read_text(encoding='utf-8')
BUNDLE = (ROOT / 'static' / 'css' / 'output.css').read_text(encoding='utf-8')

INERT = '[aria-disabled="true"]'


def test_the_primitive_dims_an_aria_disabled_button():
    """The look itself. Without this the attribute carries semantics and nothing a sighted hunter sees."""
    assert '.pp-cta:disabled,\n.pp-cta' + INERT + ' {' in CTA, (
        'aria-disabled lost the :disabled look; both gated buttons render fully live again'
    )


def test_no_hover_or_press_affordance_survives_on_an_aria_disabled_button():
    """The other half, and the half that makes it feel live rather than merely look it. A glow on hover
    and a 1px press translate both say "this works"."""
    for rule in ('.pp-cta:hover:not(:disabled)',
                 '.pp-cta--ghost:hover:not(:disabled)',
                 '.pp-cta--danger:hover:not(:disabled)',
                 '.pp-cta:active:not(:disabled)'):
        assert rule + ':not(' + INERT + ')' in CTA, f'{rule} still offers its affordance when aria-disabled'


def test_pointer_events_are_not_taken_away():
    """DELIBERATELY absent. `pointer-events: none` would take back the hover and focus reachability the
    attribute was chosen for, which is the whole reason these pages did not just use `disabled`."""
    inert_block = CTA[CTA.index('.pp-cta' + INERT):]
    inert_block = inert_block[:inert_block.index('}')]

    assert 'pointer-events' not in inert_block


def test_the_compiled_bundle_carries_it():
    """A build alone is invisible -- the rule has to reach `output.css`, which is what pages load.

    Matched WITHOUT quotes: lightningcss strips them, so the shipped selector is
    `[aria-disabled=true]`. Checking for the quoted form here would pass on the source and prove
    nothing about the bundle.
    """
    assert '.pp-cta[aria-disabled=true]' in BUNDLE
    assert '.pp-cta:hover:not(:disabled):not([aria-disabled=true])' in BUNDLE
