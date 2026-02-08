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

    // Bind theme toggle to More drawer items
    document.querySelectorAll('#more-drawer-theme-toggle').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            toggleTheme();
        });
    });

    // Initialize theme icons on load
    updateAllThemeIcons();

    // ===== Expandable Search Bar (Desktop) =====
    const searchToggleBtn = document.getElementById('search-toggle-btn');
    const searchExpandable = document.getElementById('search-expandable');
    const searchInput = document.getElementById('search-input');

    if (searchToggleBtn && searchExpandable) {
        searchToggleBtn.addEventListener('click', () => {
            const isActive = searchExpandable.classList.toggle('active');
            searchToggleBtn.setAttribute('aria-expanded', isActive);
            if (isActive && searchInput) {
                setTimeout(() => searchInput.focus(), 300);
            }
        });

        // Close on click outside
        document.addEventListener('click', (e) => {
            if (searchExpandable.classList.contains('active') &&
                !searchExpandable.contains(e.target) &&
                !searchToggleBtn.contains(e.target)) {
                searchExpandable.classList.remove('active');
                searchToggleBtn.setAttribute('aria-expanded', 'false');
            }
        });

        // Close on Escape
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && searchExpandable.classList.contains('active')) {
                searchExpandable.classList.remove('active');
                searchToggleBtn.setAttribute('aria-expanded', 'false');
                searchToggleBtn.focus();
            }
        });
    }

    // ===== Mobile Search Overlay =====
    const mobileSearchBtn = document.getElementById('mobile-search-btn');
    const mobileSearchOverlay = document.getElementById('mobile-search-overlay');
    const mobileSearchClose = document.getElementById('mobile-search-close');
    const mobileSearchInput = document.getElementById('mobile-search-input');

    if (mobileSearchBtn && mobileSearchOverlay) {
        mobileSearchBtn.addEventListener('click', () => {
            mobileSearchOverlay.classList.add('open');
            mobileSearchOverlay.setAttribute('aria-hidden', 'false');
            if (mobileSearchInput) {
                setTimeout(() => mobileSearchInput.focus(), 300);
            }
        });

        if (mobileSearchClose) {
            mobileSearchClose.addEventListener('click', () => {
                mobileSearchOverlay.classList.remove('open');
                mobileSearchOverlay.setAttribute('aria-hidden', 'true');
            });
        }

        // Close mobile search on Escape
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && mobileSearchOverlay.classList.contains('open')) {
                mobileSearchOverlay.classList.remove('open');
                mobileSearchOverlay.setAttribute('aria-hidden', 'true');
                mobileSearchBtn.focus();
            }
        });
    }

    // ===== More Drawer (Mobile) =====
    const moreBtn = document.getElementById('mobile-more-btn');
    const moreDrawer = document.getElementById('more-drawer');
    const moreBackdrop = document.getElementById('more-drawer-backdrop');

    function openMoreDrawer() {
        if (!moreDrawer || !moreBackdrop) return;
        moreBackdrop.classList.remove('hidden');
        moreDrawer.setAttribute('aria-hidden', 'false');
        // Trigger reflow for animation
        void moreDrawer.offsetHeight;
        moreDrawer.classList.add('open');
        document.body.style.overflow = 'hidden';
    }

    function closeMoreDrawer() {
        if (!moreDrawer || !moreBackdrop) return;
        moreDrawer.classList.remove('open');
        moreDrawer.setAttribute('aria-hidden', 'true');
        document.body.style.overflow = '';
        // Wait for animation to finish before hiding backdrop
        setTimeout(() => {
            moreBackdrop.classList.add('hidden');
        }, 300);
    }

    if (moreBtn) {
        moreBtn.addEventListener('click', () => {
            if (moreDrawer && moreDrawer.classList.contains('open')) {
                closeMoreDrawer();
            } else {
                openMoreDrawer();
            }
        });
    }

    if (moreBackdrop) {
        moreBackdrop.addEventListener('click', closeMoreDrawer);
    }

    // Close More drawer on Escape
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && moreDrawer && moreDrawer.classList.contains('open')) {
            closeMoreDrawer();
            if (moreBtn) moreBtn.focus();
        }
    });

    // ===== Mega Menu Close Behavior (Desktop) =====
    // Ensure only one mega menu is open at a time
    const megaMenus = document.querySelectorAll('.mega-menu-dropdown');

    megaMenus.forEach(menu => {
        menu.addEventListener('toggle', () => {
            if (menu.open) {
                // Close all other mega menus
                megaMenus.forEach(other => {
                    if (other !== menu && other.open) {
                        other.removeAttribute('open');
                    }
                });
            }
        });
    });

    // Close mega menus on Escape
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            megaMenus.forEach(menu => {
                if (menu.open) {
                    menu.removeAttribute('open');
                }
            });
        }
    });

    // ===== Mobile Tab Bar Active State =====
    const currentPath = window.location.pathname;
    const tabItems = document.querySelectorAll('.mobile-tabbar-item[href]');

    tabItems.forEach(item => {
        const href = item.getAttribute('href');
        if (href === '/' && currentPath === '/') {
            item.classList.add('active');
        } else if (href !== '/' && currentPath.startsWith(href)) {
            item.classList.add('active');
        }
    });
});
