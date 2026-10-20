"""The back-to-top button and toasts, in a real browser (SW-02, DL-10 in docs/design/reviews/README.md).

WHY A BROWSER: the repo has no JS runner, and both are behaviour a source read cannot prove. Back-to-top is
driven by scroll DIRECTION across animation frames; a toast's look is the stylesheet's, and its exit is an
animation. So this loads the REAL `output.css`, the REAL back-to-top partial, the REAL `wireBackToTop`
(lifted from main.js) and the REAL `utils.js`, from file URLs, no server.

Skips with a reason where Playwright's browser is not installed; CI installs it (see test_plat_card_layout).
"""
from pathlib import Path

import pytest

pytest.importorskip('playwright.sync_api')

ROOT = Path(__file__).resolve().parents[2]


def _browser_missing():
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as p:
            p.chromium.launch().close()
        return None
    except Exception as exc:  # noqa: BLE001 -- any launch failure means we cannot measure
        return f'playwright chromium unavailable ({type(exc).__name__}); run `playwright install chromium`'


_SKIP_REASON = _browser_missing()
pytestmark = pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or '')

CSS = (ROOT / 'static/css/output.css').as_uri()
MAIN = (ROOT / 'static/js/main.js').read_text(encoding='utf-8')
WIRE = MAIN[MAIN.index('function wireBackToTop(btn) {'):]
BUTTON = (ROOT / 'templates/partials/back_to_top.html').read_text(encoding='utf-8').split('{% endcomment %}', 1)[1]
UTILS = (ROOT / 'static/js/utils.js').read_text(encoding='utf-8')
TOAST_HOSTS = ('<div id="toast-container" class="pp-toasts"></div>'
               '<div id="toast-announcer" class="sr-only" aria-live="polite"></div>')


def _page(tmp_path, body, script, name):
    path = tmp_path / name
    path.write_text(f'<!doctype html><html data-theme="plat-pursuit-dark"><head><link rel="stylesheet" href="{CSS}">'
                    f'</head><body>{body}<script>{script}</script></body></html>', encoding='utf-8')
    return path.as_uri()


# --- back to top -----------------------------------------------------------------------------------------

STATE = """() => { const b = document.getElementById('back-to-top'), r = b.getBoundingClientRect();
    return { shown: b.classList.contains('is-shown'), tab: b.tabIndex, hidden: b.getAttribute('aria-hidden'),
             events: getComputedStyle(b).pointerEvents,
             hit: b.contains(document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2)) }; }"""


def _open_totop(tmp_path, *, reduced, toasts=False):
    """A long page with the real button and wiring. Reduced motion makes every state instant, so a sample
    is the settled one; without it, the click's smooth scroll home really animates. `toasts` adds the real
    toast hosts and utils.js, in base.html's order (the hosts after the button)."""
    from playwright.sync_api import sync_playwright
    p = sync_playwright().start()
    browser = p.chromium.launch()
    page = browser.new_page(viewport={'width': 1280, 'height': 800})
    page.emulate_media(reduced_motion='reduce' if reduced else 'no-preference')
    page.goto(_page(tmp_path, '<main id="main-content"><div style="height:6000px"></div></main>' + BUTTON
                    + (TOAST_HOSTS if toasts else ''),
                    (UTILS + '\n' if toasts else '') + WIRE + "\nwireBackToTop(document.getElementById('back-to-top'));",
                    f'totop-{"rm" if reduced else "motion"}{"-toasts" if toasts else ""}.html'))

    def scroll_to(y):
        page.evaluate(f'window.scrollTo({{top: {y}, behavior: "instant"}})')
        page.wait_for_timeout(80)  # the scroll handler waits for an animation frame
        return page.evaluate(STATE)

    def close():
        browser.close()
        p.stop()
    return page, scroll_to, close


@pytest.fixture
def totop(tmp_path):
    page, scroll_to, close = _open_totop(tmp_path, reduced=True)
    yield page, scroll_to
    close()


@pytest.fixture
def totop_moving(tmp_path):
    page, scroll_to, close = _open_totop(tmp_path, reduced=False)
    yield page, scroll_to
    close()


def test_it_wears_the_quiet_house_look_not_the_default_cta(totop_moving):
    """The audit's HIGH: chrome.css loads before cta.css, so a bare `.pp-totop` lost to `.pp-cta` and the
    button wore the default CTA's see-through cyan wash at 36px, with a slide-in that snapped."""
    page, scroll_to = totop_moving
    scroll_to(2000)
    scroll_to(1800)
    page.wait_for_timeout(400)
    look = page.evaluate("""() => { const b = document.getElementById('back-to-top'), cs = getComputedStyle(b);
        const probe = document.createElement('span'); probe.style.color = 'var(--pp-text-dim)';
        document.body.appendChild(probe);
        const alpha = (cs.backgroundColor.match(/\\/\\s*([\\d.]+)\\)/) || [0, 1])[1];
        return { h: b.getBoundingClientRect().height, w: b.getBoundingClientRect().width, alpha: +alpha,
                 color: cs.color, dim: getComputedStyle(probe).color, transition: cs.transitionProperty }; }""")
    assert look['h'] == pytest.approx(40, abs=0.5) and look['w'] == pytest.approx(40, abs=0.5), look
    assert look['alpha'] >= 0.9, f'a see-through wash, not the quiet surface: {look}'
    assert look['color'] == look['dim'], f'not the dim resting colour: {look}'
    assert 'transform' in look['transition'], f'the slide-in snaps: {look}'


def test_interrupting_the_trip_home_gives_the_button_back(totop_moving):
    """The audit's catch: a reader who scrolls down mid-animation took over, so the click's suppression
    must end there rather than hiding the button until they next reach the top."""
    page, scroll_to = totop_moving
    scroll_to(4000)
    assert scroll_to(3800)['shown']
    page.click('#back-to-top')
    page.wait_for_timeout(120)  # mid smooth-scroll
    y = page.evaluate('window.scrollY')
    assert y > 1200, 'the scroll home finished before it could be interrupted; slow the sample down'
    scroll_to(y + 600)          # the reader grabs the page and scrolls down
    assert scroll_to(y + 300)['shown'], 'still suppressed after the reader took over from the trip home'


def test_a_click_moves_keyboard_focus_to_the_content(totop):
    page, scroll_to = totop
    scroll_to(2000)
    scroll_to(1800)
    page.focus('#back-to-top')
    page.keyboard.press('Enter')
    page.wait_for_timeout(120)
    assert page.evaluate('document.activeElement.id') == 'main-content', 'focus left on a now-hidden button'


def test_focus_leaves_the_button_when_scrolling_hides_it(totop):
    """Hidden means aria-hidden and out of the tab order, so focus must not stay parked on it."""
    page, scroll_to = totop
    scroll_to(2000)
    assert scroll_to(1800)['shown']
    page.focus('#back-to-top')
    assert not scroll_to(2200)['shown']
    assert page.evaluate('document.activeElement.id') != 'back-to-top', 'focus left on a hidden button'


def test_a_rubber_band_bounce_past_the_end_does_not_offer_it(totop):
    """iOS reports scroll positions past the end during an overscroll bounce, and the bounce back is not the
    reader scrolling up. A desktop browser cannot overscroll, so the position the handler reads is faked."""
    page, scroll_to = totop
    end = page.evaluate('document.documentElement.scrollHeight - window.innerHeight')
    assert not scroll_to(end)['shown']

    def bounce_to(y):
        page.evaluate("""y => { Object.defineProperty(window, 'scrollY', { get: () => y, configurable: true });
                                window.dispatchEvent(new Event('scroll')); }""", y)
        page.wait_for_timeout(80)
        return page.evaluate(STATE)

    bounce_to(end + 80)
    assert not bounce_to(end)['shown'], 'the bounce back from an overscroll offered the button'


def test_it_starts_hidden_and_inert(totop):
    page, _ = totop
    state = page.evaluate(STATE)
    assert state == {'shown': False, 'tab': -1, 'hidden': 'true', 'events': 'none', 'hit': False}


def test_scrolling_down_never_offers_it_and_scrolling_up_does(totop):
    _, scroll_to = totop
    assert not scroll_to(1500)['shown'], 'offered while reading down the page'
    up = scroll_to(1300)
    assert up['shown'] and up['tab'] == 0 and up['hidden'] is None and up['events'] == 'auto' and up['hit']
    assert not scroll_to(1600)['shown'], 'still there after the reader went back to scrolling down'


def test_near_the_top_it_is_never_offered(totop):
    _, scroll_to = totop
    scroll_to(500)
    assert not scroll_to(300)['shown'], 'offered within the first screen, where Home is a flick away'


def test_a_few_pixels_of_jitter_change_nothing(totop):
    _, scroll_to = totop
    scroll_to(2000)
    shown = scroll_to(1800)['shown']
    assert shown
    assert scroll_to(1804)['shown'] == shown, 'momentum jitter hid it'


def test_a_click_takes_you_home_and_it_stays_hidden_on_the_way(totop_moving):
    page, scroll_to = totop_moving
    scroll_to(3000)
    assert scroll_to(2800)['shown']
    page.click('#back-to-top')
    samples = []
    for _ in range(40):  # sample the whole smooth scroll, however long the browser takes
        page.wait_for_timeout(60)
        samples.append(page.evaluate(STATE)['shown'])
        if page.evaluate('window.scrollY') == 0:
            break
    assert len(samples) > 3, 'the scroll was instant, so nothing was sampled on the way home'
    assert not any(samples), 'the upward scroll the click started offered the button again'
    assert page.evaluate('window.scrollY') == 0


# --- toasts ----------------------------------------------------------------------------------------------


def test_a_toast_is_a_house_notice_that_announces_itself_and_leaves(tmp_path):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1280, 'height': 800})
        page.goto(_page(tmp_path, TOAST_HOSTS, UTILS, 'toast.html'))
        page.evaluate("PlatPursuit.ToastManager.show('Saved. Your list is up to date.', 'success', 1500)")
        page.wait_for_timeout(450)  # past the entrance, well before the exit
        toast = page.evaluate("""() => { const t = document.querySelector('#toast-container > *');
            const cs = getComputedStyle(t), probe = document.createElement('span');
            probe.style.color = 'var(--pp-text)'; document.body.appendChild(probe);
            return { cls: t.className, color: cs.color, text: getComputedStyle(probe).color,
                     icon: !!t.querySelector('.pp-notice__icon'), body: t.querySelector('.pp-notice__body').textContent,
                     said: document.getElementById('toast-announcer').textContent }; }""")
        assert toast['cls'].split() == ['pp-notice', 'pp-toast', 'pp-notice--success']
        assert toast['color'] == toast['text'], 'toast text is no longer the readable house text colour'
        assert toast['icon'] and toast['body'] == 'Saved. Your list is up to date.'
        assert toast['said'] == 'Saved. Your list is up to date.', 'the toast is silent to a screen reader'

        # 'constructor' is a key every object inherits: a truthy lookup let it through as a tone.
        page.evaluate("PlatPursuit.ToastManager.show('<b>not markup</b>', 'constructor', 1500)")
        last = page.evaluate("""() => { const t = [...document.querySelectorAll('#toast-container > *')].pop();
            return { cls: t.className, bold: !!t.querySelector('b') }; }""")
        assert last == {'cls': 'pp-notice pp-toast pp-notice--info', 'bold': False}, 'unknown tone or HTML injection'

        page.wait_for_timeout(1800)  # both lifetimes, plus the exit
        assert page.evaluate("document.querySelectorAll('#toast-container > *').length") == 0, 'toasts never left'
        browser.close()


@pytest.mark.parametrize('width', [390, 1280])
def test_a_toast_moves_the_button_aside_until_it_leaves(tmp_path, width):
    """They share the bottom-right corner at every width, and the toast stack paints over the button, which
    then peeked out above it. While a toast is up the button steps aside, and it comes back after."""
    page, scroll_to, close = _open_totop(tmp_path, reduced=False, toasts=True)
    try:
        page.set_viewport_size({'width': width, 'height': 800})
        scroll_to(2000)
        assert scroll_to(1800)['hit']
        page.evaluate("PlatPursuit.ToastManager.show('Saved.', 'success', 700)")
        page.wait_for_timeout(450)
        aside = page.evaluate(STATE)
        seen = page.evaluate("getComputedStyle(document.getElementById('back-to-top')).visibility")
        # Not `hit` alone: the toast covers the button's centre whether or not it stepped aside.
        assert seen == 'hidden' and aside['events'] == 'none', f'the button stayed out under the toast: {aside}, {seen}'
        page.wait_for_timeout(1200)  # the toast's life, its exit, and the button's fade back in
        assert page.evaluate("document.querySelectorAll('#toast-container > *').length") == 0
        back = page.evaluate(STATE)
        assert back['shown'] and back['hit'], f'the button never came back after the toast left: {back}'
    finally:
        close()


def test_with_reduced_motion_a_toast_still_moves_the_button_aside(tmp_path):
    """The reduced-motion twin of the step-aside rule swaps the transition; it must still hide."""
    page, scroll_to, close = _open_totop(tmp_path, reduced=True, toasts=True)
    try:
        scroll_to(2000)
        assert scroll_to(1800)['hit']
        page.evaluate("PlatPursuit.ToastManager.show('Saved.', 'success', 700)")
        page.wait_for_timeout(450)
        seen = page.evaluate("getComputedStyle(document.getElementById('back-to-top')).visibility")
        assert seen == 'hidden' and page.evaluate(STATE)['events'] == 'none'
    finally:
        close()


def test_a_toast_does_not_take_the_button_from_a_keyboard_user_on_it(tmp_path):
    """Stepping aside would drop their focus to <body> and lose their place on the page."""
    page, scroll_to, close = _open_totop(tmp_path, reduced=False, toasts=True)
    try:
        scroll_to(2000)
        assert scroll_to(1800)['shown']
        page.keyboard.press('Tab')  # the only focusable control on the page, reached the keyboard way
        assert page.evaluate('document.activeElement.id') == 'back-to-top'
        page.evaluate("PlatPursuit.ToastManager.show('Saved.', 'success', 700)")
        page.wait_for_timeout(450)
        assert page.evaluate('document.activeElement.id') == 'back-to-top', 'the toast took keyboard focus away'
        assert page.evaluate("getComputedStyle(document.getElementById('back-to-top')).visibility") == 'visible'
    finally:
        close()
