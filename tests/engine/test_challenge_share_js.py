"""`challenge-share.js`, pinned by SOURCE TEXT because this project has no JS test runner.

THE DIALOG PORTS THE PLAT CARD MODAL'S LIFECYCLE WHOLE, and every guard in that lifecycle is a bug that
shipped once (plat-cards.js tells each story). None of them shows in a Python suite: drop one and the dialog
still opens, still closes, still downloads. What breaks is a preview for one run landing in a dialog reopened
for another, a stale "Saved", a card painted over the swatch row on a landscape phone, an exit that plays
twice. So these are STRUCTURAL pins: each asserts a guard is PRESENT, with what its absence costs. They cannot
prove the guard works; they stop it being deleted while tidying, which is the failure that actually happens.

The download half (derived `disabled`, a failure that does not block its own retry) is pinned for this file
alongside the plat modal in `test_card_download.py`, because those guards belong to `CardDownload`'s callers
as a family.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
JS = (ROOT / 'static' / 'js' / 'challenge-share.js').read_text(encoding='utf-8')


def _code_only(source):
    """`source` without its comment lines, so an absence check cannot match the comment explaining it."""
    out, in_block = [], False
    for line in source.splitlines():
        stripped = line.strip()
        if in_block:
            in_block = '*/' not in stripped
            continue
        if stripped.startswith('/*'):
            in_block = '*/' not in stripped
            continue
        if stripped.startswith(('//', '*')):
            continue
        out.append(line)
    return '\n'.join(out)


CODE = _code_only(JS)


def _function(name):
    start = CODE.index('function %s(' % name)
    end = CODE.find('\n    function ', start + 10)
    return CODE[start:end if end != -1 else len(CODE)]


def test_a_stale_preview_is_dropped():
    """Clicks outrun fetches: open run A, close, open run B, and A's preview lands second. Every completion
    path of the preview fetch must check it is still the current request, or A's card paints into B's
    dialog."""
    load = _function('loadPreview')
    assert 'var token = ++reqToken;' in load
    assert load.count('token !== reqToken') == 2, 'the success or the failure path lost its stale check'
    assert 'token === reqToken' in load, "a stale request can clear the current one's busy state"


def test_closing_invalidates_the_preview_in_flight():
    """Esc closes a <dialog> natively, without passing through close(), so the `close` listener is the one
    place that runs on every exit. It must drop the in-flight preview, forget the run, and undo the recede."""
    listener = re.search(r"addEventListener\('close', function \(\) \{([^}]*)\}", CODE)
    assert listener, 'the dialog has no close listener'
    body = listener.group(1)
    assert 'reqToken++' in body, 'a preview landing after close would repaint a closed dialog'
    assert 'current = null' in body
    assert 'pageRecede(false)' in body, 'Esc leaves the page receded'


def test_opening_resets_the_download_button():
    """One button serves every run on My Challenges. Opening for a new run must reset it, which also drops a
    render still in flight for the previous run (`CardDownload.reset`'s generation counter) -- otherwise the
    new run is greeted with the old one's "Saved", or its file."""
    assert 'downloader.reset()' in _function('open')


def test_the_preview_shrinks_rather_than_overflowing():
    """`fit()` clamps the scale at ZERO, not at a minimum size: the box is overflow:hidden with an inline
    frame height, so any floor paints the card over the swatch row on a landscape phone. It also resets the
    frame width before measuring, or each resize ratchets the card smaller."""
    fit = _function('fit')
    assert 'Math.max(0,' in fit, 'the scale lost its zero clamp'
    assert "frame.style.width = '';" in fit, 'fit() measures the width it set last time'
    assert fit.index("frame.style.width = '';") < fit.index('frame.clientWidth'), 'the reset comes too late'
    assert 'BOX_VH' in fit, 'the budget no longer comes from the viewport'


def test_the_exit_plays_once():
    """A second close while the exit animation runs must not start another, and the native close must
    still happen if `animationend` never fires (a hidden tab)."""
    close = _function('close')
    assert "dlg.classList.contains('is-closing')" in close.split('\n')[1], 'close() is not re-entry guarded'
    assert 'setTimeout(finish' in close, 'a missed animationend would leave the dialog open'


def test_page_wide_listeners_bind_once_and_dialog_listeners_every_boot():
    """`onPageReady` re-runs boot after an htmx history restore, which replaces <body>: listeners on the
    dialog die with it and must be re-bound, while the body-click and resize listeners are on surviving
    objects and must not pile up."""
    boot = _function('boot')
    first = boot.index('if (first) {')
    assert "addEventListener('resize', fit)" in boot[first:], 'resize is bound on every boot'
    assert "document.body.addEventListener('click'" in boot[first:], 'the trigger delegate piles up'
    assert "dlg.addEventListener('click'" in boot[:first], 'the dialog listeners only bind on the first boot'


def test_there_is_no_preview_cache():
    """The plat modal caches previews because it prefetches on hover. This dialog has no prefetch, and the
    picker rewrites a run's squares in place without a reload, so a cache would show a preview that
    disagrees with the freshly rendered download -- the one thing the design promises cannot happen."""
    assert 'previewCache' not in CODE
    assert 'PP.API.request(current.htmlUrl)' in _function('loadPreview')
