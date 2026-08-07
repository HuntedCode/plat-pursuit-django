/*
 * Company detail -- page motion + the role/sort swap. The interactive controls are the role switcher
 * (segmented .pp-switch radios) and the sort select; both HTMX-swap just #company-groups (the header, your-
 * progress block, community strip, switcher, and toolbar stay put). This owns the header count-ups (catalogue
 * totals + your-progress %), the your-progress bar fill-from-0, the staggered group-card reveal, and the
 * .is-swapping settle on a role/sort change. Mirrors franchise-detail.js.
 *
 * Wired via PlatPursuit.onPageReady(boot): element wiring re-runs on first load AND on HTMX Back/Forward
 * history restore; the body-level listener is guarded by `first` so it binds once (rebuild-playbook s7).
 */
(function () {
    var PP = window.PlatPursuit || {};
    var revealHandle = null;

    function initReveal() {
        if (revealHandle) { revealHandle.disconnect(); revealHandle = null; }
        var grid = document.querySelector('#company-groups .fgroup-grid');
        if (!grid || !PP.staggerReveal) { return; }
        var fadeEase = 'cubic-bezier(0.2, 0.8, 0.2, 1)';
        var springEase = 'cubic-bezier(0.34, 1.4, 0.64, 1)';
        revealHandle = PP.staggerReveal({
            grid: grid, cardSelector: '.fgroup', step: 26,
            reveal: function (el, delayMs) {
                if (!el.animate) { return; }
                el.animate([{ opacity: 0 }, { opacity: 1 }],
                           { duration: 420, delay: delayMs, easing: fadeEase, fill: 'backwards' });
                el.animate([{ transform: 'translateY(12px) scale(0.98)' }, { transform: 'none' }],
                           { duration: 480, delay: delayMs, easing: springEase, fill: 'backwards' });
            },
        });
    }

    // Settle the group list the instant a role/sort change fires (HTMX swaps its innerHTML).
    function onFormChange() {
        var g = document.getElementById('company-groups');
        if (g) { g.classList.add('is-swapping'); }
    }

    function onAfterSwap(e) {
        var t = (e.detail && e.detail.target) || e.target;
        if (!t || t.id !== 'company-groups') { return; }
        t.classList.remove('is-swapping');
        initReveal();
    }

    function boot(first) {
        if (first) {
            document.querySelectorAll('[data-scard-count]').forEach(function (el) {
                if (PP.countUp) { PP.countUp(el, 900); }
            });
            var pct = document.querySelector('.fr-prog__pct[data-countup]');
            if (pct && PP.countUp) { PP.countUp(pct, 900); }
            var hz = document.querySelector('.fr-prog .pp-horizon');
            var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
            if (hz && !reduce) {
                var target = hz.style.getPropertyValue('--horizon-progress');
                hz.style.setProperty('--horizon-progress', '0%');
                requestAnimationFrame(function () {
                    requestAnimationFrame(function () { hz.style.setProperty('--horizon-progress', target); });
                });
            }
        }
        var form = document.getElementById('co-form');
        if (form) { form.addEventListener('change', onFormChange); }   // same fn ref -> dedupes across boots
        initReveal();
        if (first) { document.body.addEventListener('htmx:afterSwap', onAfterSwap); }
    }

    if (PP.onPageReady) { PP.onPageReady(boot); }
    else { document.addEventListener('DOMContentLoaded', function () { boot(true); }); }
})();
