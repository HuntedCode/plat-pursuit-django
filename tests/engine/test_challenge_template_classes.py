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


#: A `{% ... %}` TAG usually contributes no characters to a class name -- it is control flow, so what sits
#: either side of it are separate tokens and both are checkable. Replacing it with a space captures that.
#:
#: NOT EXACT, and an earlier version of this comment claimed it was. A tag CAN sit mid-token
#: (`class="btn-{% if x %}primary{% else %}ghost{% endif %}"`), and then the space yields `btn-`,
#: `primary` and `ghost` -- three tokens, none of them a class, i.e. the false positives the `{{ }}`
#: branch below exists to avoid. Tags that emit characters at all (`{% cycle %}`, `{% firstof %}`, a
#: custom tag) break it the same way. The regex cannot tell the two shapes apart, so this is a bet that
#: holds for every class attribute in this directory (checked) and would need the sentinel treatment the
#: day one of them interpolates mid-token.
TAG_RE = re.compile(r'\{%.*?%\}', re.DOTALL)
#: A `{{ ... }}` VARIABLE does contribute characters, and can complete a class name mid-token
#: (`text-{{ tone }}`), so it is replaced with a sentinel and any token carrying one is skipped. That
#: token is genuinely unresolvable, and a guard that guesses at those reports false positives and gets
#: deleted -- which is worse than the gap.
VAR_RE = re.compile(r'\{\{.*?\}\}', re.DOTALL)
UNRESOLVABLE = '\x00'


def _classes(html):
    """Every statically-resolvable class token in the file.

    ORDER IS THE WHOLE TRICK, and getting it wrong is how this started. The first version split on
    whitespace FIRST and then dropped tokens containing a brace, which works only while no class
    attribute holds a tag: `class="pp-csq{% if card.job %} pp-csq--jobs{% endif %}"` splits into `{%`,
    `if`, `card.job`, `%}` and so on, and `if`, `endif` and `card.job` carry no brace at all. The guard
    duly reported that the template used eight classes absent from the bundle, none of which were
    classes. Strip the Django syntax, then split.
    """
    found = set()
    for match in re.finditer(r'class="([^"]*)"', html):
        value = TAG_RE.sub(' ', match.group(1))
        value = VAR_RE.sub(UNRESOLVABLE, value)
        for token in value.split():
            if UNRESOLVABLE in token or '{' in token or '}' in token:
                continue
            found.add(token)
    return found


def in_bundle(cls):
    """Is `cls` in the bundle as a WHOLE selector, rather than as the start of a longer one?

    A plain `in` test is a substring test, and a BEM family defeats it: `.pp-csq` is satisfied by
    `.pp-csq-grid`, `.pp-csq__art` by `.pp-csq__art--icon`, `.link` by `.link-hover` and `.badge` by
    `.badge-sm`. So the base class of a family could be entirely absent from the bundle -- rendering with
    none of its styling, which is the exact failure this file exists to catch -- while the guard stayed
    green because a cousin of it compiled.

    The boundary is "not followed by another class-name character or an escape". `.pp-csq{`, `.pp-csq,`,
    `.pp-csq:hover` and `.pp-csq.pp-csq--done` all count; `.pp-csq-grid` does not.
    """
    for form in (as_selector(cls), '.' + cls):
        if re.search(re.escape(form) + r'(?![-\w\\])', BUNDLE):
            return True
    return False


@pytest.mark.parametrize('template', TEMPLATES, ids=lambda p: p.name)
def test_every_class_reaches_the_compiled_bundle(template):
    missing = sorted(
        cls for cls in _classes(template.read_text(encoding='utf-8')) if not in_bundle(cls)
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


@pytest.mark.parametrize('template', TEMPLATES, ids=lambda p: p.name)
def test_no_template_reaches_for_daisyuis_badge(template):
    """Status pills are `.bd-chip`, never DaisyUI's `.badge`.

    `static/css/components/chips.css` states the policy in its own header: the chip is "the strangler
    replacement for DaisyUI `.badge` as shared atoms convert". Both of these templates missed it, and the
    cost was visible rather than theoretical -- `badge-success` resolves to DaisyUI's `--color-success`
    while the challenge square's done ring uses `--pp-success`, so the header's "Finished" pill and the
    rings in the grid below it were two different greens a few hundred pixels apart meaning the same thing.

    Pinned per-template rather than as one assertion over the app, so a new page inherits the guard the
    day it is added rather than the day somebody remembers to extend a list.
    """
    html = template.read_text(encoding='utf-8')

    assert 'class="badge' not in html, (
        f'{template.name} renders a DaisyUI badge. Use `.bd-chip` + a tone modifier '
        '(components/chips.css), which tints from --pp-* instead of DaisyUI\'s theme.'
    )
