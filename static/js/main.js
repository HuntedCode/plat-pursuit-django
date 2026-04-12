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
        const navbar = document.querySelector('nav.navbar');
        if (!navbar) return;
        const navH = Math.round(navbar.getBoundingClientRect().height);

        const subnav = document.querySelector('.hub-subnav');
        let subnavH = 0;
        if (subnav) {
            subnav.style.top = navH + 'px';
            subnavH = Math.round(subnav.getBoundingClientRect().height);
        }

        // --chrome-height: navbar + sub-nav only. Used by elements outside the
        // main content column (sidebar ads) that don't overlap the hotbar.
        const chromeH = navH + subnavH;
        document.documentElement.style.setProperty('--chrome-height', chromeH + 'px');

        // --sticky-top: the full pinned-chrome stack including the hotbar
        // (when present). Elements inside main that need to clear the entire
        // stack use this property for their sticky `top` offset.
        let stickyTop = chromeH;
        const hotbar = document.getElementById('hotbar-wrapper');
        if (hotbar) {
            // When the hotbar is expanded, leave 8px breathing room between it
            // and whatever pins above. When collapsed, drop the gap to 0 so the
            // toggle "tab" attaches flush against the bottom of the sub-nav (or
            // navbar on non-hub pages), reading as a tab handle of the chrome
            // above rather than a floating element.
            const isCollapsed = localStorage.getItem('hotbar_hidden') === 'true';
            const gap = isCollapsed ? 0 : 8;
            const hotbarTop = chromeH + gap;
            hotbar.style.top = hotbarTop + 'px';
            stickyTop = hotbarTop + Math.round(hotbar.getBoundingClientRect().height);
        }
        document.documentElement.style.setProperty('--sticky-top', stickyTop + 'px');
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
    // Re-measure when the hotbar is toggled. A rAF loop runs during the
    // collapse/expand transition so --sticky-top tracks the hotbar's
    // changing height in real-time, keeping dependent sticky elements
    // smooth. The loop stops on transitionend (or a 500ms safety timeout).
    document.addEventListener('hotbar:toggle', function() {
        const container = document.getElementById('hotbar-container');
        if (!container) { alignStickyChrome(); return; }
        let frame;
        function tick() {
            alignStickyChrome();
            frame = requestAnimationFrame(tick);
        }
        tick();
        container.addEventListener('transitionend', function handler() {
            cancelAnimationFrame(frame);
            alignStickyChrome();
            container.removeEventListener('transitionend', handler);
        });
        setTimeout(function() { cancelAnimationFrame(frame); alignStickyChrome(); }, 500);
    });

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

    // Note: legacy mega-menu dropdown handlers and the path-based mobile
    // tab bar active-state setter were removed when the navbar was
    // collapsed to direct-link buttons (Community Hub initiative). The
    // mobile tab bar now uses hub_section template logic for the active
    // state, and the navbar has no dropdowns to coordinate.
});
