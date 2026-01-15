/**
 * Game Detail Page JavaScript
 * Handles carousel navigation and form filtering with scroll position preservation
 */

document.addEventListener('DOMContentLoaded', () => {
    // Get container with data attributes
    const container = document.getElementById('game-detail-container');
    if (!container) return;

    const baseUrl = container.dataset.baseUrl;
    const scrollKey = container.dataset.scrollKey;

    // Parse initial query params from URL
    const urlParams = new URLSearchParams(window.location.search);
    const queryParams = new URLSearchParams();
    let page = 1;
    let nextPageUrl = '';

    // Copy URL params to queryParams
    for (const [key, value] of urlParams) {
        if (key !== 'page') {
            queryParams.append(key, value);
        } else {
            page = parseInt(value) || 1;
        }
    }

    // Update nextPageUrl
    nextPageUrl = `${baseUrl}?page=${page + 1}&${queryParams.toString()}`;

    // ====================
    // Carousel Navigation
    // ====================
    const carouselNavLinks = document.querySelectorAll('[data-slide-to]');
    const carousel = document.getElementById('screenshot-carousel');

    if (carousel && carouselNavLinks.length > 0) {
        carouselNavLinks.forEach(link => {
            link.addEventListener('click', (e) => {
                e.preventDefault();
                const targetId = e.currentTarget.dataset.slideTo;
                const targetSlide = document.getElementById(targetId);
                if (targetSlide) {
                    carousel.scrollTo({
                        left: targetSlide.offsetLeft,
                        behavior: 'smooth'
                    });
                }
            });
        });
    }

    // ====================
    // Form Filtering with Scroll Position Preservation
    // ====================
    const form = document.getElementById('filter-form');
    const unearnedToggle = document.getElementById('unearned-toggle');

    // Save scroll position before form submit
    if (form) {
        form.addEventListener('submit', () => {
            localStorage.setItem(scrollKey, window.scrollY);
            page = 2;
            nextPageUrl = `${baseUrl}?page=${page}&${queryParams.toString()}`;
        });
    }

    if (unearnedToggle) {
        unearnedToggle.addEventListener('submit', () => {
            localStorage.setItem(scrollKey, window.scrollY);
            page = 2;
            nextPageUrl = `${baseUrl}?page=${page}&${queryParams.toString()}`;
        });
    }

    // Restore scroll position after page load
    const savedScroll = localStorage.getItem(scrollKey);
    if (savedScroll) {
        window.scrollTo({
            top: parseInt(savedScroll),
            behavior: 'smooth'
        });
        localStorage.removeItem(scrollKey);
    }
});
