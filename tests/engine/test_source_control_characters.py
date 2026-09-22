"""No stray control bytes in tracked source.

This exists because the same accident happened twice, years apart in code terms, and both times it
produced a test that PASSED while checking nothing:

  - `test_recap_controller.py` (2026-08) meant to assert that nothing re-clears the recap card's fit.
    Its regex was written as `\\bfitCard`; the `\\b` reached the file as byte 0x08, so the pattern
    could never match, and `assert not re.search(...)` was true forever.
  - `test_mod_center.py` (2026-09) meant to strip `<script>` bodies before checking page copy for em
    dashes. Its `\\1` reached the file as byte 0x01. Same shape: the guard ran, matched nothing, and
    reported success.

Both were authored through a shell heredoc, where a non-raw string turns `\\b` and `\\1` into the
characters they name. Neither is visible in a diff, in review, or in an editor. A byte scan is.

Text files only, and the tracked tree only: this makes no claim about images, fonts or the venv.
"""
import re

import pytest

from tests.engine._tree import ROOT, source_files

#: Everything below 0x20 except tab, newline and carriage return, which are legitimate whitespace.
CONTROL = re.compile('[\x00-\x08\x0b\x0c\x0e-\x1f]')

SUFFIXES = {'.py', '.html', '.css', '.js', '.md', '.txt', '.json', '.yml', '.yaml', '.toml'}

#: Third-party bundles we ship but did not write. A minified vendor file may legitimately carry
#: anything; the point of this guard is OUR source. A PREFIX rather than a directory name, so this
#: does not quietly become "any directory called vendor, anywhere".
SKIP_PATHS = ('static/vendor',)


def _tracked_text_files():
    """Our own text source. "Tracked" is the claim in the docstring above, and it was not quite true:
    this walked into `.claude/` and read `settings.local.json`, which is untracked local config that
    happens to end in `.json`. `_tree.SKIP_DIRS` is where that is now excluded, once, for every guard
    that walks the repo."""
    return source_files('*', suffixes=SUFFIXES, skip_paths=SKIP_PATHS)


def test_no_control_characters_in_source():
    offences = []
    for path, rel in _tracked_text_files():
        try:
            text = path.read_text(encoding='utf-8')
        except (UnicodeDecodeError, OSError):
            continue                      # not text after all; not this guard's business
        for found in CONTROL.finditer(text):
            line = text.count('\n', 0, found.start()) + 1
            offences.append(f'{rel}:{line} contains {found.group()!r}')

    assert not offences, (
        'control bytes in source, which is how a regex escape silently becomes a character and a '
        'guard silently stops guarding:\n  ' + '\n  '.join(offences))


def test_the_scan_actually_reaches_the_source_tree():
    """A guard on the guard. If the skip list or the suffix set ever swallowed the tree, the test
    above would pass over an empty generator and report perfect health."""
    scanned = {rel for _path, rel in _tracked_text_files()}

    assert len(scanned) > 500, f'the scan only reached {len(scanned)} files'
    for expected in ('trophies/models.py', 'templates/partials/navbar.html',
                     'static/css/components/chrome.css', 'tests/engine/test_mod_center.py'):
        assert expected in scanned, f'{expected} was not scanned'


@pytest.mark.parametrize('char', ['\x00', '\x01', '\x08', '\x1b'])
def test_the_pattern_catches_what_it_is_named_for(char):
    assert CONTROL.search(f'prefix{char}suffix')


@pytest.mark.parametrize('char', ['\t', '\n', '\r'])
def test_ordinary_whitespace_is_not_an_offence(char):
    assert not CONTROL.search(f'prefix{char}suffix')


# ── CSS glyphs are escapes, never literals ───────────────────────────────────────────────────────

#: A `content:` declaration's quoted value.
_CSS_CONTENT = re.compile(r"""content:\s*(['"])(.*?)\1""")


def test_no_css_content_value_carries_a_literal_non_ascii_character():
    """Every glyph in a `content:` is written as a CSS ESCAPE (`\\2713`), never as the character.

    THIS SHIPPED. The Game Lists card menu marks the current section with a tick, and the stylesheet
    carried `content: '¹3'` -- U+00B9 SUPERSCRIPT ONE followed by an ASCII `3`. It was written
    into the file through a generated Python string where `\\2713` with ONE backslash is an OCTAL
    escape: `chr(0o271)` is `¹`, and the `3` was the leftover digit. It reached `output.css` and
    `staticfiles/` before an audit read the bytes, and it rendered in the accent colour and was
    announced aloud as "superscript one three".

    The escape form is what the whole stylesheet already uses -- `\\2605` for stars, `\\2022` for a
    bullet, `\\00b7` for a middot, `\\203A` for a chevron, and `\\2713` for this same tick in
    `elements.css` -- so this asserts a convention rather than imposing one.

    Why the convention is right, beyond consistency: a literal depends on every tool between the
    editor and the browser agreeing about encoding, and on this occasion one of them did not. An
    escape is ASCII all the way down and cannot be mangled by anything that preserves ASCII.

    ASCII WORDS ARE FINE and deliberately not caught: `content: "Bronze"`, `content: "tap to flip"`
    and `content: '%'` are all real and all legible in any encoding.
    """
    offences = []
    for path in sorted((ROOT / 'static' / 'css').rglob('*.css')):
        if path.name == 'output.css':
            continue          # build output; the source files below are what anyone edits
        for n, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
            for _quote, value in _CSS_CONTENT.findall(line):
                bad = [c for c in value if ord(c) > 127]
                if bad:
                    offences.append(
                        f'{path.relative_to(ROOT).as_posix()}:{n}: '
                        f'{value!r} contains {[hex(ord(c)) for c in bad]}'
                    )

    assert not offences, (
        'CSS `content:` values must use escapes (\\2713), not literal characters -- a literal '
        'depends on every tool in the chain agreeing about encoding:\n  ' + '\n  '.join(offences)
    )


def test_that_guard_is_actually_scanning_the_stylesheets():
    """A guard on the guard, the same one the control-byte scan above carries. If the glob or the
    skip ever swallowed the tree, the test above would pass over nothing and report perfect health.

    Anchored on the two files that hold the ticks this rule exists for."""
    seen = {}
    for path in (ROOT / 'static' / 'css').rglob('*.css'):
        if path.name == 'output.css':
            continue
        seen[path.name] = len(_CSS_CONTENT.findall(path.read_text(encoding='utf-8')))

    assert len(seen) > 20, f'the scan only reached {len(seen)} stylesheets'
    for expected in ('gamelists.css', 'elements.css'):
        assert seen.get(expected), f'{expected} was not scanned, or carries no content: values'


def test_the_pattern_catches_the_character_that_shipped():
    """The exact byte sequence, so this cannot rot into a test about some other glyph."""
    found = _CSS_CONTENT.findall("    content: '¹3';")
    assert found and any(ord(c) > 127 for c in found[0][1])

    # ...and does not fire on the escape that replaced it, nor on an ASCII word.
    for ok in ("content: '\\2713';", 'content: "Bronze";', "content: '%';"):
        assert not [c for c in _CSS_CONTENT.findall(ok)[0][1] if ord(c) > 127]
