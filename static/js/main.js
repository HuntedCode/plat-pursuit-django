document.addEventListener('DOMContentLoaded', function() {
    // ===== Sticky chrome alignment =====
    // The pinned chrome stack (navbar → sub-nav → hotbar) uses Tailwind sticky
    // offsets like top-16 (64px) and top-[7.25rem] (116px) as good first guesses,
    // but the actual rendered navbar height varies by 1-2px depending on font
    // metrics, avatar dimensions, and DPI rounding. Hardcoded offsets cause a
    // visible shift on first scroll because the sub-nav's natural position and
    // its sticky position don't match exactly.
    //
    // This function measures the real heights and inline-styles `top:` on the
    // sub-nav and hotbar so they always sit flush against whatever is above
    // them. The Tailwind classes remain as the pre-JS fallback (avoids FOUC on
    // slow loads) and the JS overrides them once the layout is known.
    function alignStickyChrome() {
        const navbar = document.querySelector('.pp-nav');
        if (!navbar) return;
        // Each pinned tier laps the tier above it by 1px. On fractional-DPR devices (real phones) a
        // measured-flush boundary between two separately-composited bars can leave a sub-pixel seam where
        // the page shows through un-blurred/un-dimmed; a 1px overlap closes it and is invisible (the upper
        // bar has the higher z-index, so it paints over the lapped pixel). Desktop and emulated-mobile snap
        // to integer device pixels and never gap, which is why it only showed on a physical phone.
        const LAP = 1;
        // The PSN outage banner pins above the navbar. Its CSS guesses one line (36px, 40px at lg) for the
        // pre-JS paint; on a phone it wraps to about three, so pin the navbar at its measured height.
        const outage = document.getElementById('site-banner-psn-outage');
        if (outage) navbar.style.top = Math.round(outage.getBoundingClientRect().height) + 'px';
        // The navbar's bottom, not just its height: the outage banner pins it lower (its `top`), and
        // everything stacked below must clear that too.
        const navH = Math.round(navbar.getBoundingClientRect().height + (parseFloat(getComputedStyle(navbar).top) || 0));
        let bottom = navH;   // running bottom edge of the pinned chrome stack

        const subnav = document.querySelector('.pp-sub');
        let railH = 0;
        if (subnav) {
            subnav.style.top = (bottom - LAP) + 'px';
            railH = Math.round(subnav.getBoundingClientRect().height) - LAP;
            bottom += railH;
        }
        // --chrome-rail: what the sub-nav adds to the stack. While a page bar is pinned the sub-nav slides
        // away and everything below it moves up by this much (--rail-shift, chrome.css).
        document.documentElement.style.setProperty('--chrome-rail', railH + 'px');

        // --chrome-nav-height: bottom of navbar + sub-nav. The pinned Badge Art Reveal banner offsets its
        // own sticky top by this (already lapped, so the banner overlaps the sub-nav too).
        document.documentElement.style.setProperty('--chrome-nav-height', (bottom - LAP) + 'px');

        // The reveal banner pins directly below the nav chrome and joins the pinned stack, so everything
        // downstream must clear it too.
        const revealBanner = document.getElementById('art-reveal-banner');
        if (revealBanner) {
            bottom = (bottom - LAP) + Math.round(revealBanner.getBoundingClientRect().height);
        }

        // --chrome-height: full chrome-stack bottom. Used by elements outside the main content column
        // (sidebar ads) to clear the chrome; the 1px laps are immaterial to that layout offset.
        document.documentElement.style.setProperty('--chrome-height', bottom + 'px');

        // --sticky-top: where in-content bars (the page mini-bar, etc.) pin. Lap the last chrome bar by 1px
        // so the mini-bar/sub-nav seam closes the same way.
        document.documentElement.style.setProperty('--sticky-top', (bottom - LAP) + 'px');
    }

    alignStickyChrome();
    // Re-measure on resize because font scaling, orientation changes, and
    // viewport width can all change the navbar's actual height.
    window.addEventListener('resize', alignStickyChrome);
    // Re-measure after web fonts finish loading (font swap can change line
    // heights and therefore navbar height).
    if (document.fonts && document.fonts.ready) {
        document.fonts.ready.then(alignStickyChrome);
    }

    // (The hub sub-nav's mobile sheet + desktop overflow "More" moved to static/js/subnav.js.)

    // ===== Back to top button =====
    const backToTop = document.getElementById('back-to-top');
    if (backToTop) wireBackToTop(backToTop);

    // Light mode was removed 2026-08-22: the rebuild's --pp-* token foundation is dark-first
    // (every rebuilt surface reads dark values), so the old toggle produced a broken hybrid.
    // A proper light theme is a future rebuild item (parallel --pp palette + contrast audit).

    // ── Avatar account menu: crafted entrance + a little life over DaisyUI's focus dropdown ──
    // DaisyUI shows/hides the panel on focus; we layer an .is-open class (the CSS spring + section cascade)
    // and count the trophy loot up on open. Open/close is detected via focusin/focusout on the wrap (its own
    // mechanism is focus-based, so these align). Purely additive -- absent this, the menu still works.
    (function () {
        const wrap = document.getElementById('nav-account');
        const panel = wrap && wrap.querySelector('.pp-avpanel');
        if (!wrap || !panel) return;
        let menuOpen = false;
        wrap.addEventListener('focusin', () => {
            if (menuOpen) return;
            menuOpen = true;
            panel.classList.add('is-open');
            // Count the loot up on open -- but not mid-sync, so we don't fight navsync's live number updates.
            const av = wrap.querySelector('[data-nav-avatar]');
            if (av && av.getAttribute('data-sync') === 'syncing') return;
            if (window.PlatPursuit && PlatPursuit.countUp) {
                panel.querySelectorAll('[data-nav-loot]').forEach((el) => {
                    const n = parseInt((el.textContent || '').replace(/[^0-9]/g, ''), 10);
                    if (!isNaN(n)) { el.dataset.countup = n; PlatPursuit.countUp(el, 620, { from: 0 }); }
                });
            }
        });
        wrap.addEventListener('focusout', (e) => {
            if (wrap.contains(e.relatedTarget)) return;   // focus moved WITHIN the menu -> still open
            menuOpen = false;
            panel.classList.remove('is-open');
        });
    })();

    // Note: legacy mega-menu dropdown handlers and the path-based mobile
    // tab bar active-state setter were removed when the navbar was
    // collapsed to direct-link buttons (Community Hub initiative). The
    // mobile tab bar now uses hub_section template logic for the active
    // state, and the navbar has no dropdowns to coordinate.
});

/**
 * Back to top (2026-10, SW-02 in docs/design/reviews/README.md).
 *
 * Offered only while the reader scrolls back UP past the first screens, and withdrawn while they scroll
 * down: the pattern Safari and Chrome use for their own toolbars. The old button appeared after 300px
 * and stayed, so it sat over whatever was under the corner (a contract card's Claim button on phones).
 *
 * Hidden means INERT, not just invisible: out of the tab order and hidden from screen readers, and the
 * stylesheet drops pointer events, because an invisible button still takes the tap meant for whatever
 * is under it (the Challenge optimizer's Next sat exactly there).
 *
 * After a click it stays hidden for the whole smooth scroll home; otherwise the upward scroll it starts
 * would offer the button again on the way up.
 */
function wireBackToTop(btn) {
    const SHOW_BELOW = 600; // px from the top before the button is offered at all
    const NUDGE = 8;        // px of travel that counts as a direction, so momentum jitter does nothing
    let lastY = window.scrollY;
    let ticking = false;
    let homeward = false;   // set by a click, cleared once the page is back near the top

    const set = (shown) => {
        if (btn.classList.contains('is-shown') === shown) return;
        btn.classList.toggle('is-shown', shown);
        btn.tabIndex = shown ? 0 : -1;
        if (shown) btn.removeAttribute('aria-hidden');
        else btn.setAttribute('aria-hidden', 'true');
        // Never leave focus on a control that just became aria-hidden and unfocusable.
        if (!shown && document.activeElement === btn) btn.blur();
    };

    const update = () => {
        ticking = false;
        // Clamped to the real scroll range: iOS's rubber-band bounce past either end reports positions
        // outside it, and the bounce back would otherwise read as scrolling up and offer the button.
        const max = Math.max(0, document.documentElement.scrollHeight - window.innerHeight);
        const y = Math.min(Math.max(window.scrollY, 0), max);
        const delta = y - lastY;
        if (y < SHOW_BELOW) {
            homeward = false;
            set(false);
            lastY = y;
            return;
        }
        if (Math.abs(delta) < NUDGE) return; // keep the baseline, so slow scrolls still add up
        // The scroll home only ever moves UP, so any downward move is the reader taking over: stop
        // suppressing, or an interrupted trip home would hide the button until they reached the top.
        if (delta > 0) homeward = false;
        set(delta < 0 && !homeward);
        lastY = y;
    };

    window.addEventListener('scroll', () => {
        if (!ticking) {
            ticking = true;
            requestAnimationFrame(update);
        }
    }, { passive: true });

    btn.addEventListener('click', () => {
        homeward = true;
        // Send keyboard focus to the top of the content the reader just jumped to, so the next Tab
        // continues from there rather than from a button at the end of the page that is now hidden.
        const main = document.getElementById('main-content');
        if (main) {
            if (!main.hasAttribute('tabindex')) main.setAttribute('tabindex', '-1');
            main.focus({ preventScroll: true });
        }
        set(false);
        const smooth = !window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        window.scrollTo({ top: 0, behavior: smooth ? 'smooth' : 'auto' });
    });

    btn.classList.remove('is-shown');
    btn.tabIndex = -1;
    btn.setAttribute('aria-hidden', 'true');
}
