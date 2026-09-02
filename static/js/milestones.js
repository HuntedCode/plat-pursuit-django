/*
 * Milestones page motion: header count-ups + a staggered card reveal that also fills each card's next-rung
 * progress bar from 0. Cards render visible by default (no-JS safe); the reveal is a WAAPI enhancement.
 * Wired via PlatPursuit.onPageReady so it also re-runs cleanly on an HTMX Back/Forward restore.
 */
(function () {
    var PP = window.PlatPursuit || {};
    var revealHandle = null;

    function initReveal() {
        if (revealHandle) { revealHandle.disconnect(); revealHandle = null; }
        var grid = document.querySelector('[data-msc-grid]');
        if (!grid || !PP.staggerReveal) { return; }
        var fadeEase = 'cubic-bezier(0.2, 0.8, 0.2, 1)';
        var springEase = 'cubic-bezier(0.34, 1.4, 0.64, 1)';
        revealHandle = PP.staggerReveal({
            grid: grid, cardSelector: '.msc', step: 26,
            reveal: function (el, delayMs) {
                if (!el.animate) { return; }
                el.animate([{ opacity: 0 }, { opacity: 1 }],
                           { duration: 420, delay: delayMs, easing: fadeEase, fill: 'backwards' });
                el.animate([{ transform: 'translateY(12px) scale(0.985)' }, { transform: 'none' }],
                           { duration: 480, delay: delayMs, easing: springEase, fill: 'backwards' });
                // Fill the next-rung progress bar from 0 as the card lands.
                var bar = el.querySelector('.pp-horizon');
                if (bar) {
                    var target = bar.style.getPropertyValue('--horizon-progress');
                    bar.style.setProperty('--horizon-progress', '0%');
                    requestAnimationFrame(function () {
                        requestAnimationFrame(function () {
                            bar.style.setProperty('--horizon-progress', target || '0%');
                        });
                    });
                }
            },
        });
    }

    function boot(first) {
        if (first && PP.countUp) {
            document.querySelectorAll('[data-scard-count]').forEach(function (el) { PP.countUp(el, 900); });
        }
        initReveal();
    }

    if (PP.onPageReady) { PP.onPageReady(boot); }
    else { document.addEventListener('DOMContentLoaded', function () { boot(true); }); }
})();
