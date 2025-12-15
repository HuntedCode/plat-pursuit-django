document.addEventListener('DOMContentLoaded', () => {
    const carousel = document.querySelector('.carousel');
    if (carousel) {
        let items = carousel.querySelectorAll('.carousel-item');
        if (items.length > 0) {
            const originalLength = items.length;
            const getStep = () => {
                const itemWidth = items[0].getBoundingClientRect().width;
                const gap = (items.length > 1) ? parseFloat(getComputedStyle(items[1]).marginLeft) || 0 : 0;
                return itemWidth + gap;
            };
            const step = getStep();
            const visibleCount = Math.round(carousel.offsetWidth / step) || 1;

            for (let i = 0; i < visibleCount; i++) {
                const clone = items[i].cloneNode(true);
                carousel.appendChild(clone);
            }
            items = carousel.querySelectorAll('.carousel-item');

            carousel.scrollLeft = 0;

            const autoScroll = () => {
                carousel.scrollBy({
                    left: step,
                    behavior: 'smooth'
                });
            };

            let intervalId = setInterval(autoScroll, 5000);

            carousel.addEventListener('scrollend', () => {
                if (carousel.scrollLeft >= (originalLength * step)) {
                    carousel.scrollTo({
                        left: carousel.scrollLeft - (originalLength * step),
                        behavior: 'instant'
                    });
                }
            });

            carousel.addEventListener('mouseenter', () => clearInterval(intervalId));
            carousel.addEventListener('mouseleave', () => intervalId = setInterval(autoScroll, 5000));

            window.addEventListener('resize', () => {
                clearInterval(intervalId);
                // Remove clones later
            });
        }
    }
});