document.addEventListener('DOMContentLoaded', function() {
    // Back to top button
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

    // Theme toggle
    const themeToggle = document.getElementById('theme-toggle');
    const sunIcon = document.getElementById('theme-icon-sun');
    const moonIcon = document.getElementById('theme-icon-moon');

    if (themeToggle && sunIcon && moonIcon) {
        function updateThemeIcons() {
            // Read from localStorage (source of truth) to avoid race conditions with DOM attribute
            const theme = localStorage.getItem('theme') || 'plat-pursuit-dark';
            const isDark = theme === 'plat-pursuit-dark';
            sunIcon.classList.toggle('hidden', !isDark);
            moonIcon.classList.toggle('hidden', isDark);
        }

        themeToggle.addEventListener('click', () => {
            // Read from localStorage (source of truth) to avoid race conditions
            const currentTheme = localStorage.getItem('theme') || 'plat-pursuit-dark';
            const newTheme = currentTheme === 'plat-pursuit-dark' ? 'plat-pursuit-light' : 'plat-pursuit-dark';
            document.documentElement.setAttribute('data-theme', newTheme);
            localStorage.setItem('theme', newTheme);
            updateThemeIcons();
        });

        updateThemeIcons();
    }
});