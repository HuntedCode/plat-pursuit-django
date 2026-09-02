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
    var scroller = null;

    // Infinite scroll over the grouped list (24 groups/fetch; the view slices + sends
    // X-Has-Next). Re-created per role/sort swap because the swap replaces the grid node AND
    // changes the querystring the scroller must carry (hx-push-url keeps location current).
    function initScroller() {
        if (scroller) { scroller.destroy(); scroller = null; }
        if (!PP.InfiniteScroller) { return; }
        scroller = PP.InfiniteScroller.create({
            gridId: 'fgroup-grid', sentinelId: 'co-groups-sentinel', loadingId: 'co-groups-loading',
            paginateBy: 24, cardSelector: '.fgroup',
            // The reveal engine adds .pp-reveal to the grid, which holds every .fgroup at
            // opacity:0 until it earns .is-revealed -- so appended nodes MUST go through
            // revealHandle.observe (the sibling pages' pattern), never a bare one-off fade:
            // fill:'backwards' reverts to the hidden base style when the animation ends and
            // the appended groups would flash in and VANISH (the lane audit's High).
            onAppend: function (cards) {
                if (revealHandle) { revealHandle.observe(cards); }
            },
        });
    }

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

    // "Read more / less" on the company description. The toggle stays hidden unless the text overflows the
    // 3-line clamp, so a short blurb never shows it. Toggling `line-clamp-3` expands/collapses.
    function onAboutToggle(e) {
        var btn = e.currentTarget;
        var text = document.querySelector('[data-co-about-text]');
        if (!text) { return; }
        text.classList.toggle('line-clamp-3');
        var collapsed = text.classList.contains('line-clamp-3');
        btn.textContent = collapsed ? 'Read more' : 'Read less';
        btn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
    }
    function wireAbout() {
        var text = document.querySelector('[data-co-about-text]');
        var btn = document.querySelector('[data-co-about-toggle]');
        if (!text || !btn) { return; }
        // If the text is already expanded (e.g. a cached history restore snapshotted it open), keep the toggle
        // available as "Read less"; otherwise only offer it when the clamped text actually overflows.
        var expanded = !text.classList.contains('line-clamp-3');
        var overflows = expanded || (text.scrollHeight - text.clientHeight > 2);
        btn.hidden = !overflows;
        if (overflows) {
            btn.textContent = expanded ? 'Read less' : 'Read more';
            btn.setAttribute('aria-expanded', expanded ? 'true' : 'false');
            // Bind unconditionally with the stable fn ref: the browser dedups (type, listener) on the same
            // node, and an HTMX history restore yields a FRESH node that needs the listener re-added. (A
            // data-* guard would wrongly skip it -- the snapshot preserves the attribute but not the listener.)
            btn.addEventListener('click', onAboutToggle);
        }
    }

    function onAfterSwap(e) {
        var t = (e.detail && e.detail.target) || e.target;
        if (!t || t.id !== 'company-groups') { return; }
        t.classList.remove('is-swapping');
        initReveal();
        initScroller();
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
        wireAbout();
        // Re-measure once web fonts settle -- a FOUT height shift can flip a borderline 3-line clamp.
        if (document.fonts && document.fonts.ready) { document.fonts.ready.then(wireAbout); }
        initReveal();
        initScroller();
        if (first) { document.body.addEventListener('htmx:afterSwap', onAfterSwap); }
    }

    if (PP.onPageReady) { PP.onPageReady(boot); }
    else { document.addEventListener('DOMContentLoaded', function () { boot(true); }); }
})();
