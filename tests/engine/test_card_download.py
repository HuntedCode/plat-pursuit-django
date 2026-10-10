"""The shared download button: PlatPursuit.CardDownload + components/download-button.css.

Three surfaces now save a server-rendered share card -- the plat card modal, the recap ceremony, and the
recap's below-fold panel -- and until this extraction each had its own copy of fetch-blob-anchor with its
own idea of what a slow press should look like. The ceremony's was the worst of them: a bare
`window.location.href`, which cannot show progress, cannot name the file, and on a failure replaces the
whole page (the ceremony included) with whatever the error response happens to be.

The behaviour itself was exercised in a browser against both real surfaces -- states, the file, the rate
limit, the ceremony surviving it -- since none of that is visible in source. These pin the contract that
makes those surfaces share one implementation, which is the part that rots quietly.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
UTILS = (ROOT / 'static' / 'js' / 'utils.js').read_text(encoding='utf-8')
CSS = (ROOT / 'static' / 'css' / 'output.css').read_text(encoding='utf-8')
ICONS = (ROOT / 'templates' / 'partials' / 'download_button_icons.html').read_text(encoding='utf-8')

SURFACES = {
    'the plat card modal': ROOT / 'static' / 'js' / 'plat-cards.js',
    'the recap': ROOT / 'static' / 'js' / 'monthly-recap.js',
    'the challenge share dialog': ROOT / 'static' / 'js' / 'challenge-share.js',
}


def test_the_helper_is_exported():
    assert 'window.PlatPursuit.CardDownload = CardDownload;' in UTILS


@pytest.mark.parametrize('name,path', SURFACES.items(), ids=list(SURFACES))
def test_no_surface_keeps_its_own_copy_of_the_download(name, path):
    """The duplication this replaced. Each copy drifted: one sent a bare `theme=`, one tracked the event
    and one did not, one swapped a spinner into innerHTML (throwing away the button's contents), and the
    ceremony's did not fetch at all."""
    code = path.read_text(encoding='utf-8')
    assert 'CardDownload.attach' in code, f'{name} is not on the shared helper'
    assert 'createObjectURL' not in code, (
        f'{name} still has its own blob-and-anchor save -- that is the thing being shared'
    )
    assert 'window.location.href' not in code or 'png/' not in code.split('window.location.href')[1][:120], (
        f'{name} navigates to the PNG endpoint, which cannot report a failure'
    )


def test_a_slow_press_is_acknowledged_before_it_finishes():
    """The load-bearing state. The PNG is composed by headless Chromium, so the gap between press and file
    is long enough that an unannotated `disabled` reads as a dead click."""
    assert "classList.toggle('is-busy'" in UTILS
    assert "setAttribute('aria-busy'" in UTILS, 'the only progress indicator does not announce like one'
    for state in ('idle', 'busy', 'done'):
        assert re.search(rf'\.pp-dl__i--{state}\b', CSS), f'the {state} glyph has no rule'
        assert f'pp-dl__i--{state}' in ICONS, f'the {state} glyph does not ship in the markup'


def test_the_idle_label_belongs_to_the_caller():
    """The plat card names the variant it is about to save ("Download 100% card"). A fixed idle string
    overwrote that the first time the button was used, so a saved card silently demoted its own button to
    a generic "Download" -- and shrank by 50px doing it, shuffling the action row."""
    assert 'idleText = labelEl.textContent' in UTILS, 'idle is a fixed string again'
    assert 'button.style.minWidth' in UTILS, (
        'nothing pins the width, so a caller label longer than our three resizes the button mid-press'
    )


#: The two surfaces that put a download button inside a PREVIEW MODAL, and so have a second reason to
#: block it (the preview still loading or failed). The recap has no preview to wait on.
MODALS = {name: SURFACES[name] for name in ('the plat card modal', 'the challenge share dialog')}


@pytest.mark.parametrize('name,path', MODALS.items(), ids=list(MODALS))
def test_two_reasons_to_be_disabled_do_not_race(name, path):
    """A theme swap re-disabled the plat card's button while the "Saved" revert timer was still queued to
    re-enable it, and whichever fired last won. `disabled` is derived from both reasons, never written by
    either -- which is why the caller's reason comes in through setBlocked() rather than the property."""
    assert 'blocked || busy' in UTILS, 'disabled is not derived from both reasons'
    assert 'setBlocked(on)' in UTILS
    code = path.read_text(encoding='utf-8')
    assert 'downloader.setBlocked' in code and not re.search(r'\bgo\.disabled\s*=', code), (
        f'{name} writes the button disabled directly again'
    )


@pytest.mark.parametrize('name,path', MODALS.items(), ids=list(MODALS))
def test_a_failed_download_does_not_block_the_retry_it_advises(name, path):
    """"Give it a minute" was shown by the same call that disabled the only button that could take the
    advice. A PREVIEW failure blocks (there is no card to download); a DOWNLOAD failure must not."""
    code = path.read_text(encoding='utf-8')
    assert 'showError(msg, false)' in code, f'{name}: download errors block the button again'
    assert 'blocks !== false' in code, f'{name}: showError lost the distinction between the two failures'


def test_the_ceremony_shows_its_failures_on_the_stage():
    """The stage is z-index 90 and the page's toast host is z-50, so a toast fired from inside the
    ceremony renders BEHIND it -- the plat card modal gets a top-layer host for free from <dialog>, a
    takeover div does not. So the ceremony opts out of the toast and carries its own line."""
    recap = SURFACES['the recap'].read_text(encoding='utf-8')
    assert 'toast: false' in recap, 'the ceremony would fire a toast behind itself'
    assert 'showDownloadError' in recap, 'and then have nowhere to show a failure'

    markup = (ROOT / 'templates' / 'recap' / 'monthly_recap.html').read_text(encoding='utf-8')
    assert 'id="recap-dl-error"' in markup
    assert re.search(r'id="recap-dl-error"[^>]*aria-live', markup), (
        'the failure line is not announced, and the press that caused it moved focus nowhere'
    )


def test_the_tracking_event_followed_the_download_to_the_primary_surface():
    """`recap_image_download` hung off the below-fold panel, which was the only way to get the card when
    it was written. The ceremony is the primary route now, so leaving it there would have quietly zeroed
    the metric as the ceremony took over."""
    recap = SURFACES['the recap'].read_text(encoding='utf-8')
    assert recap.count('recap_image_download') == 1, 'the event is duplicated or gone'
    # The METHOD, not its call site -- `wireDownload()` appears first as the call, whose slice ends
    # inside the url arrow and misses everything after it.
    wire = recap[recap.index('    wireDownload() {'):]
    wire = wire[:wire.index('    cardPngUrl() {')]
    assert 'trackDownload()' in wire, 'the ceremony saves without recording it'


def _attach_body():
    start = UTILS.index('const CardDownload = {')
    return UTILS[start:UTILS.index('\n};', start)]


def test_a_reset_drops_the_render_still_in_flight():
    """A surface that reuses ONE button across cards resets it when it switches (the challenge dialog on My
    Challenges, reopened for a second run while the first render is still going). Without a generation check
    the first render then saved under the second card's name and flipped the second card's button to
    "Saved". Both completion paths must ask whether their press is still the current one."""
    body = _attach_body()
    assert 'reset() { generation += 1;' in body, 'reset no longer invalidates the press in flight'
    assert body.count('if (mine !== generation) { return; }') == 2, (
        'the save AND the failure path must each drop a stale completion'
    )


def test_the_file_is_named_when_pressed_not_when_it_lands():
    """The filename describes the card the hunter pressed Download on. Read when the blob lands, it described
    whatever the surface was showing seconds later -- or nothing, once a closed dialog had cleared its card."""
    body = _attach_body()
    named = body.index('const filename = opts.filename();')
    assert named < body.index('fetch(opts.url()'), 'the name is read after the fetch starts'
    assert 'a.download = filename;' in body
