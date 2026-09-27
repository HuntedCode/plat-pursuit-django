"""Every class a Challenges template uses must exist in the compiled bundle.

WHY THIS EXISTS: a Tailwind class that was never compiled fails SILENTLY. The markup is right, the page
renders, the view is correct, every other test passes, and the element simply has none of the styling it
asks for. Only a human looking at the screen can tell -- and only if they know what it was supposed to
look like.

It has bitten this page twice in one day. `text-[0.7rem]` and `md:gap-4` were absent until a rebuild, and
`text-warning/90` was written for the beta card's footer and would have rendered with no colour at all,
because that alpha had never been used anywhere on the site before.

The deeper trap is that `npm run build` alone is not enough (the project's own note says so): the built
file has to reach `staticfiles/` via `collectstatic` before a page loads it. This checks the tracked
source bundle, which is the artifact a deploy builds from -- so a green run here means "somebody rebuilt
after adding this class", which is the step that actually gets forgotten.

Parametrised over the whole directory so chunks 4 and 5 inherit the guard rather than needing their own.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = (ROOT / 'static' / 'css' / 'output.css').read_text(encoding='utf-8')
TEMPLATES = sorted((ROOT / 'templates' / 'challenges').glob('*.html'))

#: Characters Tailwind backslash-escapes when it writes a class as a selector, so `md:gap-4` becomes
#: `.md\:gap-4` and `text-[0.7rem]` becomes `.text-\[0\.7rem\]`. Checking the unescaped form finds
#: nothing and would make this test vacuous rather than failing loudly.
NEEDS_ESCAPE = set('[]().:/%,#')


def as_selector(cls):
    out = []
    for ch in cls:
        if ch in NEEDS_ESCAPE:
            out.append('\\')
        out.append(ch)
    return '.' + ''.join(out)


def _classes(html):
    """Every class token in the file, minus anything holding a Django expression.

    A `class="{{ foo }}"` or `class="a {% if x %}b{% endif %}"` cannot be resolved statically, so those
    tokens are skipped rather than guessed at -- a guard that reports false positives gets deleted.
    """
    found = set()
    for match in re.finditer(r'class="([^"]*)"', html):
        for token in match.group(1).split():
            if '{' in token or '}' in token:
                continue
            found.add(token)
    return found


@pytest.mark.parametrize('template', TEMPLATES, ids=lambda p: p.name)
def test_every_class_reaches_the_compiled_bundle(template):
    missing = sorted(
        cls for cls in _classes(template.read_text(encoding='utf-8'))
        if as_selector(cls) not in BUNDLE and ('.' + cls) not in BUNDLE
    )

    assert not missing, (
        f'{template.name} uses classes absent from static/css/output.css: {missing}. '
        'They will render with no styling at all. Run `npm run build`, then `collectstatic`.'
    )


def test_the_guard_is_looking_at_something():
    """A guard that found no templates, or no classes, would pass forever while checking nothing -- which
    is the failure mode every source-text test has and most do not defend against."""
    assert TEMPLATES, 'no challenge templates found; the glob has drifted'
    assert sum(len(_classes(t.read_text(encoding='utf-8'))) for t in TEMPLATES) > 50
