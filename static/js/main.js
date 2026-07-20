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
        const navH = Math.round(navbar.getBoundingClientRect().height);
        let bottom = navH;   // running bottom edge of the pinned chrome stack

        const subnav = document.querySelector('.pp-sub');
        if (subnav) {
            subnav.style.top = (bottom - LAP) + 'px';
            bottom = (bottom - LAP) + Math.round(subnav.getBoundingClientRect().height);
        }

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
    if (backToTop) {
        let scrollTimeout;

        const handleScroll = () => {
            clearTimeout(scrollTimeout);
            scrollTimeout = setTimeout(() => {
                if (window.scrollY > 300) {
                    backToTop.classList.remove('opacity-0');
                    backToTop.classList.add('opacity-100');
                } else {
                    backToTop.classList.remove('opacity-100');
                    backToTop.classList.add('opacity-0');
                }
            }, 100);
        };

        window.addEventListener('scroll', handleScroll);

        backToTop.addEventListener('click', () => {
            window.scrollTo({ top: 0, behavior: 'smooth' });
        });

        handleScroll();
    }

    // ===== Theme Toggle (shared logic for all theme toggle elements) =====
    function getCurrentTheme() {
        return localStorage.getItem('theme') || 'plat-pursuit-dark';
    }

    function setTheme(theme) {
        document.documentElement.setAttribute('data-theme', theme);
        localStorage.setItem('theme', theme);
        updateAllThemeIcons();
    }

    function toggleTheme() {
        const current = getCurrentTheme();
        const newTheme = current === 'plat-pursuit-dark' ? 'plat-pursuit-light' : 'plat-pursuit-dark';
        setTheme(newTheme);
    }

    function updateAllThemeIcons() {
        const isDark = getCurrentTheme() === 'plat-pursuit-dark';

        // Dropdown theme toggle (desktop user menu)
        const dropdownSun = document.getElementById('dropdown-theme-icon-sun');
        const dropdownMoon = document.getElementById('dropdown-theme-icon-moon');
        const dropdownLabel = document.getElementById('theme-toggle-label');
        if (dropdownSun) dropdownSun.classList.toggle('hidden', !isDark);
        if (dropdownMoon) dropdownMoon.classList.toggle('hidden', isDark);
        if (dropdownLabel) dropdownLabel.textContent = isDark ? 'Light Mode' : 'Dark Mode';

        // More drawer theme toggle (mobile - authenticated)
        const drawerSun = document.getElementById('drawer-theme-icon-sun');
        const drawerMoon = document.getElementById('drawer-theme-icon-moon');
        const drawerLabel = document.getElementById('drawer-theme-toggle-label');
        if (drawerSun) drawerSun.classList.toggle('hidden', !isDark);
        if (drawerMoon) drawerMoon.classList.toggle('hidden', isDark);
        if (drawerLabel) drawerLabel.textContent = isDark ? 'Light Mode' : 'Dark Mode';

        // More drawer theme toggle (mobile - guest)
        const drawerSunGuest = document.getElementById('drawer-theme-icon-sun-guest');
        const drawerMoonGuest = document.getElementById('drawer-theme-icon-moon-guest');
        const drawerLabelGuest = document.getElementById('drawer-theme-toggle-label-guest');
        if (drawerSunGuest) drawerSunGuest.classList.toggle('hidden', !isDark);
        if (drawerMoonGuest) drawerMoonGuest.classList.toggle('hidden', isDark);
        if (drawerLabelGuest) drawerLabelGuest.textContent = isDark ? 'Light Mode' : 'Dark Mode';
    }

    // Bind theme toggle to user dropdown item
    const themeToggleItem = document.getElementById('theme-toggle-item');
    if (themeToggleItem) {
        themeToggleItem.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            toggleTheme();
        });
    }

    // Initialize theme icons on load
    updateAllThemeIcons();

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
