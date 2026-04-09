document.addEventListener('DOMContentLoaded', function() {
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
