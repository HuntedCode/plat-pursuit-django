"""Every `animation` names a keyframe that exists (2026-08).

A CSS `animation: someName ...` whose `@keyframes someName` is missing is **silently inert**. No console
error, no build warning, no parse failure -- the declaration is simply dropped and the element renders at
its resting state. The only symptom is that a motion beat stops happening, which nobody notices until
someone looks at that specific surface on that specific page.

That is exactly how the Collection wall and badge detail's stage stack both lost their entrance: the
keyframe they shared lived in `collection-case.css`, and retiring the Case view deleted the file. Two
surfaces went static, in a commit that had no reason to touch either of them, and it stayed that way.

So this checks the whole components tree rather than the one keyframe that broke, because the failure mode
is the deletion of a DEFINITION, and the next one will be some other name in some other file.
"""
import re
from pathlib import Path

import pytest

COMPONENTS = Path(__file__).resolve().parents[2] / 'static' / 'css' / 'components'
OUTPUT_CSS = Path(__file__).resolve().parents[2] / 'static' / 'css' / 'output.css'

# `animation` / `animation-name` shorthand values that are keywords, not keyframe names.
_NOT_A_NAME = {
    'none', 'inherit', 'initial', 'unset', 'revert', 'infinite', 'alternate', 'alternate-reverse',
    'normal', 'reverse', 'both', 'forwards', 'backwards', 'running', 'paused', 'linear', 'ease',
    'ease-in', 'ease-out', 'ease-in-out', 'step-start', 'step-end',
}


def _strip_comments(css):
    return re.sub(r'/\*.*?\*/', '', css, flags=re.S)


def _referenced_names(css):
    """Keyframe names used by `animation:` / `animation-name:` declarations."""
    names = set()
    for value in re.findall(r'(?<![\w-])animation(?:-name)?\s*:([^;}]+)', _strip_comments(css)):
        # Drop functional values (cubic-bezier(...), steps(...), var(...)) before tokenising -- their
        # commas and bare words would otherwise read as names. The character class MUST include the
        # hyphen: `\w+\(` matches only `bezier(...)` inside `cubic-bezier(...)`, stranding a bare
        # `cubic-` that then reads as a keyframe name. Repeated to unwind nesting.
        while True:
            stripped = re.sub(r'[\w-]+\([^()]*\)', ' ', value)
            if stripped == value:
                break
            value = stripped
        for token in re.split(r'[\s,]+', value.strip()):
            if not token or token in _NOT_A_NAME:
                continue
            if re.fullmatch(r'-?[\d.]+m?s', token) or token.startswith('var(') or token.startswith('--'):
                continue
            if re.fullmatch(r'[A-Za-z][\w-]*', token):
                names.add(token)
    return names


def _defined_names(css):
    return set(re.findall(r'@keyframes\s+([\w-]+)', _strip_comments(css)))


def test_every_animation_reference_resolves_to_a_keyframe():
    """Checked against the whole components tree at once, because keyframes are shared ACROSS files --
    `ppRevealIn` is used by two components and defined in a third. A per-file check would flag every
    legitimate cross-file use, so the bundle is the only honest unit here.
    """
    css = '\n'.join(p.read_text(encoding='utf-8') for p in sorted(COMPONENTS.glob('*.css')))
    missing = sorted(_referenced_names(css) - _defined_names(css))

    assert not missing, (
        f'these animations name keyframes that do not exist: {missing}. A CSS animation with no matching '
        f'@keyframes is silently inert -- the motion just stops happening, with nothing in the console.'
    )


def test_the_shared_settle_in_keyframe_survives_the_build():
    """`ppRevealIn` specifically, end to end. The source check above passes the moment the name is typed
    anywhere; this one holds the compiled bundle, which is what the browser actually loads.

    Worth its own test because lightningcss is an active participant in this file's history: it silently
    drops frames containing `color-mix()`, and it rewrites units. A keyframe can be present in source and
    absent (or gutted) in `output.css`, and the two consumers here would go static exactly as before.
    """
    out = OUTPUT_CSS.read_text(encoding='utf-8')
    assert '@keyframes ppRevealIn' in out, 'the settle-in keyframe did not survive the CSS build'

    block = out[out.index('@keyframes ppRevealIn'):]
    block = block[:block.index('}}') + 2]
    assert 'opacity:0' in block.replace(' ', ''), 'the entrance no longer starts transparent'
    assert 'translateY' in block, 'the entrance no longer rises into place'


@pytest.mark.parametrize('consumer, selector', [
    ('collection-gallery.css', '.pp-gallery.is-revealing .pp-gallery__card'),
    ('badge-detail.css', '.bd-stages--armed.is-inview > .bd-stage'),
])
def test_both_consumers_still_reference_the_shared_keyframe(consumer, selector):
    """Named individually so that retiring one of these surfaces is a deliberate act. If a consumer goes
    away the keyframe may be able to go with it -- but that has to be noticed, which is the whole lesson
    of how it disappeared the first time: a file was deleted and nobody checked who was still using it.
    """
    css = (COMPONENTS / consumer).read_text(encoding='utf-8')
    rule = re.search(re.escape(selector) + r'\s*\{([^}]*)\}', css)
    assert rule, f'{selector} is gone from {consumer}; if that is intended, retire this parametrisation'
    assert 'ppRevealIn' in rule.group(1), f'{selector} no longer plays the shared settle-in'
