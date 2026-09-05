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
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]

#: Everything below 0x20 except tab, newline and carriage return, which are legitimate whitespace.
CONTROL = re.compile('[\x00-\x08\x0b\x0c\x0e-\x1f]')

SUFFIXES = {'.py', '.html', '.css', '.js', '.md', '.txt', '.json', '.yml', '.yaml', '.toml'}

SKIP_DIRS = {'node_modules', 'venv', '.venv', '.git', 'staticfiles', '__pycache__', '.pytest_cache',
             'htmlcov', 'dist', 'build', '.ruff_cache'}

#: Third-party bundles we ship but did not write. A minified vendor file may legitimately carry
#: anything; the point of this guard is OUR source.
SKIP_PATHS = {'static/vendor'}


def _tracked_text_files():
    for path in ROOT.rglob('*'):
        if not path.is_file() or path.suffix.lower() not in SUFFIXES:
            continue
        rel = path.relative_to(ROOT).as_posix()
        if any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
            continue
        if any(rel.startswith(skip) for skip in SKIP_PATHS):
            continue
        yield path, rel


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
